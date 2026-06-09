#!/usr/bin/env python3
# routes_stress_gpu — GPU-capability probe + platform/run-mode detection + the #101
# CPU/GPU stress test.  EXTRACTED VERBATIM from gose_vm_server.py (2026-06-09) as the
# first step of splitting the monolith into per-domain modules.  These handlers form a
# near-closed cluster: they share only their own module-private state (the gpu-cap cache
# and the _stress dict) and the shared logger.  Behaviour is byte-for-byte identical to
# the in-line versions — same function names, same returns, same side effects.
#
# WHY this group first: the #101 stress endpoints already silently regressed once when an
# edit elsewhere clobbered the monolith.  Isolating them means a future edit to any other
# route group cannot delete them by accident.
#
# Dependency contract: imports the project logger as LOG (child "gose.stress_gpu", same
# handler tree as the parent "gose").  The stress watchdog thread is started here at import
# time, exactly as it was started at definition time in the monolith.

import os
import json
import time
import threading
import subprocess
import logging

# Same logger tree as gose_vm_server's LOG (RotatingFileHandler on the parent "gose"
# logger propagates to children), so stress/gpu log lines land in the same gose.log.
LOG = logging.getLogger("gose.stress_gpu")

# ===================== GPU CAPABILITY PROBE — #GPU/#101 extension =====================
# Runs glxinfo + vulkaninfo + glxgears-FPS-sample once per server lifetime.
# Results are cached in /tmp/gose-gpu-cap.json so the UI can call GET /system/gpu
# as many times as needed without re-running the expensive probe.
# All three tools degrade gracefully — "unavailable" is an honest answer, not a crash.
# In the QEMU/virgl dev VM on a Windows host:
#   glxinfo → OpenGL 4.3, renderer "virgl (AMD Radeon 780M Graphics)"
#   vulkaninfo → no Vulkan (expected: virgl has no Vulkan path on Windows host)
#   glxgears → ~375 fps (virgl through AMD 780M iGPU)

GPU_PROBE_CACHE_F  = "/tmp/gose-gpu-cap.json"
GPU_PROBE_SCRIPT   = "/userdata/gose-ui/gose_gpu_probe.py"
_gpu_cap_lock      = threading.Lock()
_gpu_cap_cache     = {}   # in-memory cache (set once)
_gpu_probe_running = False  # guard: prevents concurrent probe runs

def _gpu_probe_run():
    """Spawn gose_gpu_probe.py, wait up to 20s, return parsed JSON."""
    import subprocess as _sp
    try:
        r = _sp.run(["python3", GPU_PROBE_SCRIPT],
                    capture_output=True, timeout=20)
        out = r.stdout.decode("utf-8", errors="replace").strip()
        if out:
            return json.loads(out)
    except Exception as ex:
        LOG.warning("gpu probe failed: %s", ex)
    return {
        "ok": False,
        "error": "probe script unavailable or timed out",
        "gl":     {"available": False, "vendor": "unavailable", "renderer": "unavailable",
                   "version": "unavailable", "version_short": "unavailable",
                   "mesa": "unavailable", "direct_render": "unavailable", "error": None},
        "vulkan": {"available": False, "device_name": "unavailable",
                   "api_version": "unavailable", "note": None},
        "fps":    {"gl_fps": None, "tool": "glxgears", "note": "probe unavailable"},
        "verdict": "GPU capability unavailable — probe script not deployed",
        "tier":    "none",
    }

def gpu_capability():
    """Return GPU capability JSON — cached after first call."""
    global _gpu_cap_cache
    with _gpu_cap_lock:
        if _gpu_cap_cache:
            return _gpu_cap_cache
        # try the on-disk cache first (from a prior server start or explicit probe run)
        try:
            with open(GPU_PROBE_CACHE_F) as f:
                d = json.load(f)
            if d.get("ok"):
                _gpu_cap_cache = d
                return d
        except Exception:
            pass
    # run the probe (outside the lock so we don't block other requests)
    result = _gpu_probe_run()
    with _gpu_cap_lock:
        _gpu_cap_cache = result
    return result

def gpu_capability_refresh():
    """Force a fresh probe (invalidates in-memory + disk cache).
    If a probe is already in flight, return the stale cache rather than
    spawning a second concurrent glxgears/glxinfo run."""
    global _gpu_cap_cache, _gpu_probe_running
    with _gpu_cap_lock:
        if _gpu_probe_running:
            # probe already running — return stale cache (safe fallback)
            return _gpu_cap_cache if _gpu_cap_cache else {
                "ok": False, "error": "probe in progress — try again shortly",
                "tier": "none", "verdict": "Probe already running"
            }
        _gpu_probe_running = True
        _gpu_cap_cache = {}
    try:
        os.unlink(GPU_PROBE_CACHE_F)
    except Exception:
        pass
    try:
        result = _gpu_probe_run()
    finally:
        with _gpu_cap_lock:
            _gpu_probe_running = False
    with _gpu_cap_lock:
        _gpu_cap_cache = result
    return result

# ===================== PLATFORM / RUN-MODE DETECTION =====================
#
# Three mutually exclusive run modes:
#   "bare_metal" — GOSE booted directly on real hardware (full GPU, all features)
#   "vm"         — GOSE running inside a VM (GPU ceiling depends on passthrough)
#   "app"        — GOSE running as a windowed app on a host OS (host GPU accessible
#                  via the host OS, but Linux/Proton PC games are unavailable)
#
# Detection method:
#   1. `systemd-detect-virt --vm` — authoritative; exits 0 + prints virt-type when inside a VM.
#   2. Fallback: /proc/cpuinfo hypervisor flag (works when systemd-detect-virt absent).
#   3. If neither flags VM: assume bare_metal (most honest default; app mode requires the
#      host OS to set GOSE_RUN_MODE=app in the environment before launching the server).
#
# Vulkan is reported from the gpu_capability() cache (already proven by /system/gpu probe).
# The OOBE wizard reads this endpoint once and caches the result locally in JS.

def platform_detect():
    """Detect the GOSE run mode + virt type.  Read-only; no state mutations."""
    import subprocess as _sp

    # --- detect VM via systemd-detect-virt ---
    virt_type = None
    try:
        r = _sp.run(["systemd-detect-virt", "--vm"],
                    capture_output=True, timeout=4)
        virt_name = r.stdout.decode("utf-8", errors="replace").strip()
        if r.returncode == 0 and virt_name:
            virt_type = virt_name          # e.g. "kvm", "qemu", "vmware", "xen"
    except Exception:
        pass

    # --- /proc/cpuinfo hypervisor fallback ---
    if virt_type is None:
        try:
            with open("/proc/cpuinfo") as f:
                cpuinfo = f.read()
            if "hypervisor" in cpuinfo.lower():
                virt_type = "vm-cpuinfo"   # generic VM, virt-tool not available
        except Exception:
            pass

    # --- app mode override (host OS must inject GOSE_RUN_MODE=app) ---
    env_override = os.environ.get("GOSE_RUN_MODE", "").strip().lower()
    if env_override == "app":
        run_mode = "app"
    elif virt_type is not None:
        run_mode = "vm"
    else:
        run_mode = "bare_metal"

    # --- Vulkan from the cached GPU probe (non-blocking; uses in-memory cache if warm) ---
    gpu = {}
    try:
        with _gpu_cap_lock:
            gpu = dict(_gpu_cap_cache)
    except Exception:
        pass
    vulkan_available = bool((gpu.get("vulkan") or {}).get("available"))

    # --- honest capability summary for the wizard ---
    if run_mode == "bare_metal":
        capability = "full"
        message = (
            "Running directly on hardware — full GPU available. "
            "All games including modern titles are supported."
        )
    elif run_mode == "vm" and vulkan_available:
        capability = "full"
        message = (
            "Running in a VM with Vulkan passthrough — "
            "full driver path, modern games supported."
        )
    elif run_mode == "vm" and not vulkan_available:
        capability = "retro"
        message = (
            "Running in a VM without Vulkan — light and retro games only. "
            "Modern games that require Vulkan cannot run in this configuration."
        )
    else:  # app
        capability = "retro"
        message = (
            "Running as an app on a host OS — the host's GPU handles rendering, "
            "but Linux/Proton PC games are not available. "
            "Retro emulation via native builds works normally."
        )

    return {
        "ok": True,
        "run_mode": run_mode,          # "bare_metal" | "vm" | "app"
        "virt_type": virt_type,        # e.g. "kvm", "qemu", or null for bare-metal/app
        "vulkan": vulkan_available,
        "capability": capability,      # "full" | "retro"
        "message": message,
    }


# ===================== STRESS TEST — #101 =====================
# Safety contract (critical for a load tool):
#   * The load runs in gose_stress_worker.py, a SEPARATE KILLABLE PROCESS (not in this server).
#   * Duration is hard-capped: the worker exits on its own at the deadline even if stop is never called.
#   * POST /stress/stop sends SIGTERM to the worker process group, guaranteeing teardown.
#   * A watchdog thread here auto-kills the worker if it overruns by >10s.
#   * The server itself is never blocked: metrics are written to a JSON file by the worker;
#     GET /stress/status reads the file — no shared state, no blocking I/O on the hot path.
#   * glxgears inside the worker runs in its own process group (setsid) so SIGKILL doesn't leak.
#   * Pad still works to hit Stop: the server and kiosk are never CPU-starved (separate processes).

STRESS_METRICS_F = "/tmp/gose-stress-metrics.json"
STRESS_FLAG_F    = "/tmp/gose-stress-running.flag"
STRESS_WORKER    = "/userdata/gose-ui/gose_stress_worker.py"

_stress = {
    "proc": None,          # subprocess.Popen for the worker
    "deadline": 0.0,       # wall-clock time the worker should have finished
    "duration_s": 0,
    "lock": threading.Lock(),
}

def _stress_watchdog():
    """Background thread: auto-kill the worker if it overruns its deadline by >10s."""
    while True:
        time.sleep(5)
        with _stress["lock"]:
            p = _stress["proc"]
            if p is None:
                continue
            if p.poll() is not None:
                _stress["proc"] = None
                continue
            if time.time() > _stress["deadline"] + 10:
                LOG.warning("stress worker overran deadline — force-killing")
                try:
                    import signal as _sig
                    os.killpg(os.getpgid(p.pid), _sig.SIGKILL)
                except Exception:
                    try: p.kill()
                    except Exception: pass
                _stress["proc"] = None

threading.Thread(target=_stress_watchdog, daemon=True).start()

def stress_start(payload):
    """Launch the stress worker subprocess."""
    with _stress["lock"]:
        # reject if already running
        p = _stress["proc"]
        if p is not None and p.poll() is None:
            return {"ok": False, "error": "already running — stop first"}

    raw_dur = payload.get("duration_s", 60)
    raw_mem = payload.get("mem_mb", 256)
    try:
        dur = int(raw_dur)
        mem = int(raw_mem)
    except (TypeError, ValueError):
        return {"ok": False, "error": "duration_s and mem_mb must be integers"}

    MAX_DURATION = 600  # hard cap: 10 minutes
    dur = max(10, min(dur, MAX_DURATION))
    mem = max(64, min(mem, 2048))

    # clean up stale files
    for f in (STRESS_METRICS_F, STRESS_FLAG_F):
        try: os.unlink(f)
        except Exception: pass

    try:
        proc = subprocess.Popen(
            ["python3", STRESS_WORKER, str(dur), str(mem)],
            preexec_fn=os.setsid,   # own process group
            close_fds=True,
        )
    except Exception as ex:
        return {"ok": False, "error": "failed to start worker: " + str(ex)}

    with _stress["lock"]:
        _stress["proc"] = proc
        _stress["deadline"] = time.time() + dur + 10   # grace period
        _stress["duration_s"] = dur

    return {"ok": True, "pid": proc.pid, "duration_s": dur, "mem_mb": mem}

def stress_stop():
    """Stop the stress worker cleanly."""
    with _stress["lock"]:
        p = _stress["proc"]
        if p is None or p.poll() is not None:
            _stress["proc"] = None
            return {"ok": True, "was_running": False}
        try:
            import signal as _sig
            os.killpg(os.getpgid(p.pid), _sig.SIGTERM)
        except Exception:
            try: p.terminate()
            except Exception: pass
        _stress["proc"] = None

    return {"ok": True, "was_running": True}

def stress_status():
    """Return the current metrics written by the worker (or a 'not running' stub)."""
    # check if the worker proc is still alive (avoids stale metrics from a prior run)
    with _stress["lock"]:
        p = _stress["proc"]
        proc_alive = p is not None and p.poll() is None

    try:
        with open(STRESS_METRICS_F) as f:
            d = json.load(f)
        # if the file says running but the process is dead, mark as stopped
        if d.get("running") and not proc_alive:
            d["running"] = False
        return d
    except Exception:
        pass

    # no metrics file yet (or worker never started)
    return {
        "ok": True,
        "running": proc_alive,
        "elapsed_s": 0,
        "remaining_s": _stress["duration_s"] if proc_alive else 0,
        "duration_s": _stress["duration_s"],
        "cpu_pct": None, "mem_used_mb": None, "mem_total_mb": None,
        "cpu_temp_c": None, "cpu_freq_mhz": None,
        "gl_fps": None, "gl_running": False,
        "throttled": False, "throttle_count": 0,
    }
