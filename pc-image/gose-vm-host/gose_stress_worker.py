#!/usr/bin/env python3
"""GOSE Stress-Test Worker — runs CPU, memory, and GL load for a fixed duration.

This is a SEPARATE KILLABLE PROCESS spawned by gose_vm_server.py (POST /stress/start).
The server kills it cleanly on POST /stress/stop or when the duration expires.
It writes live metrics to /tmp/gose-stress-metrics.json every ~1s so the server
can serve GET /stress/status without any shared state.

Safety design:
  - Hard deadline: exits after `duration_s` regardless.
  - SIGTERM/SIGINT: triggers clean teardown (joins CPU threads, kills GL subprocess).
  - Never touches the gose_vm_server or kiosk process.
  - glxgears is launched with DISPLAY=:0 as its own subprocess; killed on stop.
  - Memory buffer is released on exit (GC'd).
  - CPU worker threads are daemon threads so the process can always exit.
"""
import os, sys, json, time, signal, threading, subprocess, math, gc

METRICS_F = "/tmp/gose-stress-metrics.json"
STATUS_F  = "/tmp/gose-stress-running.flag"   # existence = worker alive

def _write_metrics(data):
    tmp = METRICS_F + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, METRICS_F)
    except Exception:
        pass

def _read_cpu_pct():
    """Single-sample CPU% from /proc/stat (VM's own 4 vCPUs)."""
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        parts = line.split()
        user, nice, sys_, idle, iowait = (int(parts[i]) for i in range(1, 6))
        total = user + nice + sys_ + idle + iowait
        return {"total": total, "idle": idle + iowait}
    except Exception:
        return None

def _cpu_percent(a, b):
    if not a or not b:
        return None
    dt = b["total"] - a["total"]
    di = b["idle"] - b["idle"]   # we want change in idle
    di = b["idle"] - a["idle"]
    return max(0, min(100, round((dt - di) / dt * 100, 1))) if dt > 0 else None

def _read_mem_mb():
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
        total = info.get("MemTotal", 0) // 1024
        avail = info.get("MemAvailable", 0) // 1024
        used = max(0, total - avail)
        return used, total
    except Exception:
        return None, None

def _cpu_temp_c():
    """VM thermal zone (often zero/absent); returns None if unavailable."""
    try:
        for d in os.scandir("/sys/class/thermal"):
            if d.name.startswith("thermal_zone"):
                tp = os.path.join(d.path, "temp")
                if os.path.exists(tp):
                    v = int(open(tp).read().strip())
                    if v > 0:
                        return round(v / 1000.0, 1)
    except Exception:
        pass
    return None

def _cpufreq_mhz():
    try:
        v = open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq").read().strip()
        return round(int(v) / 1000)
    except Exception:
        pass
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("cpu MHz"):
                    return round(float(line.split(":")[1].strip()))
    except Exception:
        pass
    return None

# ---- CPU busy-loop workers ----
_cpu_stop = threading.Event()

def _cpu_worker():
    """One thread: tight math loop. daemon=True so process can exit."""
    x = 1.23456789
    while not _cpu_stop.is_set():
        for _ in range(50000):
            x = math.sin(x) * math.cos(x) + math.sqrt(abs(x) + 1.0)
        # brief yield so OS scheduler can still respond to kill signals
        time.sleep(0.0001)

# ---- Memory churn ----
_mem_buf = []

def _alloc_mem(target_mb):
    """Allocate target_mb of memory as bytearray chunks."""
    chunk = 16  # MB per chunk
    needed = max(0, target_mb - len(_mem_buf) * chunk)
    for _ in range(needed // chunk):
        try:
            _mem_buf.append(bytearray(chunk * 1024 * 1024))
        except MemoryError:
            break

def _release_mem():
    global _mem_buf
    _mem_buf = []
    gc.collect()

def _churn_mem():
    """Write/read a pattern through the buffer to ensure it stays in RAM (not just allocated)."""
    for i, buf in enumerate(_mem_buf):
        # write every 4096th byte to stride across pages (cheap but effective)
        for j in range(0, len(buf), 4096):
            buf[j] = (i + j) & 0xFF

# ---- GL stress (glxgears as a separate process) ----
_gl_proc = None

def _start_gl():
    global _gl_proc
    env = dict(os.environ, DISPLAY=":0")
    try:
        # glxgears with -info so we can parse FPS from its output.
        # -fullscreen: fills the screen to actually stress the GPU through virgl.
        _gl_proc = subprocess.Popen(
            ["/usr/bin/glxgears", "-info", "-fullscreen"],
            env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,   # own process group so SIGKILL doesn't leak
        )
    except Exception as ex:
        _gl_proc = None

def _stop_gl():
    global _gl_proc
    if _gl_proc is not None:
        try:
            os.killpg(os.getpgid(_gl_proc.pid), signal.SIGTERM)
        except Exception:
            pass
        try:
            _gl_proc.wait(timeout=3)
        except Exception:
            try:
                os.killpg(os.getpgid(_gl_proc.pid), signal.SIGKILL)
            except Exception:
                pass
        _gl_proc = None

def _gl_alive():
    return _gl_proc is not None and _gl_proc.poll() is None

# ---- throttle detection ----
_freq_baseline = None
_throttle_count = 0

def _check_throttle(cur_freq):
    global _freq_baseline, _throttle_count
    if cur_freq is None:
        return False
    if _freq_baseline is None:
        _freq_baseline = cur_freq
        return False
    # throttled if freq drops >15% from the baseline we first measured
    if cur_freq < _freq_baseline * 0.85:
        _throttle_count += 1
        return True
    # if it recovers, update baseline upward
    if cur_freq > _freq_baseline:
        _freq_baseline = cur_freq
    return False

# ---- FPS sampling from glxgears stdout ----
_fps_lock = threading.Lock()
_fps_val  = [None]

def _fps_reader():
    """Background thread: reads glxgears' stdout to extract FPS lines."""
    # glxgears prints: "302 frames in 5.0 seconds = 60.400 FPS"
    import re
    pat = re.compile(r"=\s*([\d.]+)\s*FPS")
    while _gl_proc is not None and _gl_proc.poll() is None:
        try:
            line = _gl_proc.stdout.readline()
            if not line:
                break
            m = pat.search(line.decode("utf-8", errors="replace"))
            if m:
                with _fps_lock:
                    _fps_val[0] = round(float(m.group(1)), 1)
        except Exception:
            break

# ---- main ----
def main():
    global _cpu_stop

    if len(sys.argv) < 3:
        print("usage: gose_stress_worker.py <duration_s> <mem_mb>")
        sys.exit(1)

    duration_s = int(sys.argv[1])
    mem_mb     = int(sys.argv[2])
    ncpu       = os.cpu_count() or 4
    start_t    = time.time()
    deadline   = start_t + duration_s

    # write flag so the server knows we're running
    open(STATUS_F, "w").write(str(os.getpid()))

    # graceful shutdown on SIGTERM/SIGINT
    _stop_flag = threading.Event()
    def _on_signal(sig, frame):
        _stop_flag.set()
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT,  _on_signal)

    # start CPU workers (ncpu threads = saturate all vCPUs)
    cpu_threads = []
    for _ in range(ncpu):
        t = threading.Thread(target=_cpu_worker, daemon=True)
        t.start()
        cpu_threads.append(t)

    # allocate + churn memory
    _alloc_mem(mem_mb)
    _churn_mem()

    # start GL stress
    _start_gl()
    if _gl_alive():
        fps_t = threading.Thread(target=_fps_reader, daemon=True)
        fps_t.start()

    # initial metrics
    cpu_a = _read_cpu_pct()
    time.sleep(1)

    summary_temp_peak = None
    summary_fps_min   = None
    sample_count      = 0
    throttle_count    = 0

    while not _stop_flag.is_set() and time.time() < deadline:
        elapsed = round(time.time() - start_t, 1)
        remaining = max(0, round(deadline - time.time(), 1))

        cpu_b = _read_cpu_pct()
        cpu_pct = _cpu_percent(cpu_a, cpu_b)
        cpu_a = cpu_b

        mem_used, mem_total = _read_mem_mb()
        temp_c = _cpu_temp_c()
        freq_mhz = _cpufreq_mhz()
        throttled = _check_throttle(freq_mhz)
        if throttled:
            throttle_count += 1

        with _fps_lock:
            fps = _fps_val[0]

        if temp_c and (summary_temp_peak is None or temp_c > summary_temp_peak):
            summary_temp_peak = temp_c
        if fps and (summary_fps_min is None or fps < summary_fps_min):
            summary_fps_min = fps

        sample_count += 1

        # churn memory on every sample to keep it active
        _churn_mem()

        metrics = {
            "ok": True,
            "running": True,
            "elapsed_s": elapsed,
            "remaining_s": remaining,
            "duration_s": duration_s,
            "cpu_pct": cpu_pct,
            "mem_used_mb": mem_used,
            "mem_total_mb": mem_total,
            "mem_alloc_mb": mem_mb,
            "cpu_temp_c": temp_c,
            "cpu_freq_mhz": freq_mhz,
            "gl_fps": fps,
            "gl_running": _gl_alive(),
            "throttled": throttle_count > 2,   # more than 2 samples throttled = flag it
            "throttle_count": throttle_count,
            "sample_count": sample_count,
            # summary (updated live)
            "summary": {
                "duration_s": duration_s,
                "temp_peak_c": summary_temp_peak,
                "fps_min": summary_fps_min,
                "throttle_events": throttle_count,
                "stable": throttle_count == 0,
            }
        }
        _write_metrics(metrics)
        time.sleep(1)

    # --- teardown ---
    _cpu_stop.set()
    _stop_gl()
    _release_mem()

    # write final "done" metrics
    elapsed = round(time.time() - start_t, 1)
    with _fps_lock:
        fps = _fps_val[0]
    mem_used, _ = _read_mem_mb()
    final = {
        "ok": True,
        "running": False,
        "elapsed_s": elapsed,
        "remaining_s": 0,
        "duration_s": duration_s,
        "gl_running": False,
        "throttled": throttle_count > 2,
        "summary": {
            "duration_s": elapsed,
            "temp_peak_c": summary_temp_peak,
            "fps_min": summary_fps_min,
            "throttle_events": throttle_count,
            "stable": throttle_count == 0,
            "completed": not _stop_flag.is_set(),
        }
    }
    _write_metrics(final)

    try:
        os.unlink(STATUS_F)
    except Exception:
        pass

if __name__ == "__main__":
    main()
