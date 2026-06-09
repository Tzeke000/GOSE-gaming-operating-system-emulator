#!/usr/bin/env python3
"""GOSE GPU Capability Probe — runs once, writes JSON, caches result.

Probes:
  1. glxinfo  — OpenGL version, renderer string, vendor (mesa-utils, always present
                on Batocera; degrades gracefully if absent)
  2. vulkaninfo — Vulkan present? device name? (vulkan-tools; may not be present;
                  in the QEMU/virgl dev VM this will return no-Vulkan — that IS
                  the correct answer, not an error)
  3. glxgears — short FPS sample (~5 s) reusing the same tool the stress test uses.
                Only runs if DISPLAY=:0 is reachable. Skipped + reported "unavailable"
                if the tool is missing or the display is absent.

Output JSON written to CACHE_F (/tmp/gose-gpu-cap.json) and echoed to stdout.

All paths may return "unavailable" — the caller never crashes on a missing tool.
The probe is designed to be called by gose_vm_server.py /system/gpu and cached for
the session (the numbers don't change while the VM is running).
"""
import os, sys, json, subprocess, re, time, shutil

CACHE_F   = "/tmp/gose-gpu-cap.json"
DISPLAY   = os.environ.get("DISPLAY", ":0")
TIMEOUT_S = 10  # per-tool timeout

# ---- helpers ----

def _run(cmd, timeout=TIMEOUT_S, env=None):
    """Run a command; return (stdout+stderr, returncode). Never raises."""
    try:
        e = dict(os.environ)
        if env:
            e.update(env)
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=e)
        return r.stdout.decode("utf-8", errors="replace") + r.stderr.decode("utf-8", errors="replace"), r.returncode
    except subprocess.TimeoutExpired:
        return "", -1
    except Exception as ex:
        return str(ex), -1

def _tool(name):
    """Return full path to tool or None if not found."""
    return shutil.which(name)

# ---- probe 1: glxinfo ----

def probe_glx():
    """Parse OpenGL vendor, renderer, version from glxinfo."""
    result = {
        "ok": False,
        "tool": "glxinfo",
        "vendor": "unavailable",
        "renderer": "unavailable",
        "version": "unavailable",
        "mesa_version": "unavailable",
        "direct_render": "unavailable",
    }
    if not _tool("glxinfo"):
        result["error"] = "glxinfo not found (mesa-utils not installed)"
        return result

    out, code = _run(["glxinfo"], env={"DISPLAY": DISPLAY})
    if not out.strip():
        result["error"] = "glxinfo returned no output"
        return result

    m = re.search(r"OpenGL vendor string:\s*(.+)", out)
    if m:
        result["vendor"] = m.group(1).strip()

    m = re.search(r"OpenGL renderer string:\s*(.+)", out)
    if m:
        result["renderer"] = m.group(1).strip()

    # prefer Compatibility Profile version if available, else Core Profile
    m = re.search(r"OpenGL version string:\s*(.+)", out)
    if m:
        result["version"] = m.group(1).strip()
        # extract just the 4.3 part
        vm = re.search(r"([\d]+\.[\d]+)", result["version"])
        if vm:
            result["gl_version_short"] = vm.group(1)

    # Mesa version
    m = re.search(r"Mesa\s+([\d.]+)", out)
    if m:
        result["mesa_version"] = "Mesa " + m.group(1)

    m = re.search(r"direct rendering:\s*(\S+)", out)
    if m:
        result["direct_render"] = m.group(1)

    result["ok"] = True
    return result

# ---- probe 2: vulkaninfo ----

def probe_vulkan():
    """Detect Vulkan presence and device name."""
    result = {
        "ok": True,
        "tool": "vulkaninfo",
        "vulkan_present": False,
        "device_name": "unavailable",
        "api_version": "unavailable",
    }
    if not _tool("vulkaninfo"):
        result["vulkan_present"] = False
        result["note"] = "vulkaninfo not found (vulkan-tools not installed)"
        return result

    out, code = _run(["vulkaninfo", "--summary"], env={"DISPLAY": DISPLAY}, timeout=8)

    # Vulkan NOT present: error lines contain "Failed to detect any valid GPUs"
    if "Failed to detect" in out or "ERROR_INITIALIZATION_FAILED" in out:
        result["vulkan_present"] = False
        result["note"] = "No Vulkan-capable GPU detected (virgl/VM path — expected on Windows host)"
        return result

    # Vulkan present: parse device name and API version
    result["vulkan_present"] = True
    m = re.search(r"deviceName\s*=\s*(.+)", out)
    if m:
        result["device_name"] = m.group(1).strip()
    m = re.search(r"apiVersion\s*=\s*(.+)", out)
    if m:
        result["api_version"] = m.group(1).strip()
    return result

# ---- probe 3: glxgears FPS sample ----

def probe_fps():
    """Run glxgears for 5 seconds and return FPS. Returns None if unavailable."""
    result = {
        "ok": True,
        "tool": "glxgears",
        "fps": None,
        "sample_s": 5,
    }
    if not _tool("glxgears"):
        result["ok"] = False
        result["fps"] = None
        result["note"] = "glxgears not found"
        return result

    # glxgears -display :0 runs until killed; we use timeout 6s to get one 5s sample
    out, code = _run(
        ["timeout", "6", "glxgears", "-display", DISPLAY],
        timeout=8,
        env={"DISPLAY": DISPLAY},
    )
    # glxgears output: "1880 frames in 5.0 seconds = 375.990 FPS"
    m = re.search(r"=\s*([\d.]+)\s*FPS", out)
    if m:
        result["fps"] = round(float(m.group(1)), 1)
    else:
        result["note"] = "could not parse FPS from glxgears output"
    return result

# ---- verdict ----

def _verdict(glx, vk, fps_probe):
    """One-line capability verdict for humans and the setup wizard."""
    renderer  = glx.get("renderer", "unknown")
    gl_ver    = glx.get("gl_version_short", "?")
    vulkan    = vk.get("vulkan_present", False)
    fps_val   = fps_probe.get("fps")

    fps_str = f"{fps_val:.0f} fps" if fps_val is not None else "n/a"

    if vulkan:
        vk_dev = vk.get("device_name", "unknown device")
        return f"Vulkan present ({vk_dev}) + OpenGL {gl_ver} via {renderer} — {fps_str} GL FPS"
    elif glx.get("ok"):
        # virgl or other software path
        if "virgl" in renderer.lower():
            return (
                f"OpenGL {gl_ver} via virgl (host GPU passed through software path) — "
                f"no Vulkan — {fps_str} GL FPS. "
                f"Light/retro games only on this VM; modern Steam needs bare-metal."
            )
        else:
            return (
                f"OpenGL {gl_ver} via {renderer} — no Vulkan — {fps_str} GL FPS"
            )
    else:
        return "GPU capability unavailable — glxinfo not installed or display not reachable"

# ---- capability tier (for wizard / auto-branch logic) ----

def _tier(glx, vk):
    """
    Returns: "vulkan" | "opengl" | "none"
    The setup wizard uses this to branch on run-mode GPU path.
    """
    if vk.get("vulkan_present"):
        return "vulkan"
    if glx.get("ok") and glx.get("gl_version_short"):
        return "opengl"
    return "none"

# ---- main ----

def run_probe():
    t0 = time.time()

    glx       = probe_glx()
    vk        = probe_vulkan()
    fps_probe = probe_fps()

    cap = {
        "ok": True,
        "probed_at": t0,
        "elapsed_s": round(time.time() - t0, 2),
        "gl": {
            "available":     glx.get("ok", False),
            "vendor":        glx.get("vendor", "unavailable"),
            "renderer":      glx.get("renderer", "unavailable"),
            "version":       glx.get("version", "unavailable"),
            "version_short": glx.get("gl_version_short", "unavailable"),
            "mesa":          glx.get("mesa_version", "unavailable"),
            "direct_render": glx.get("direct_render", "unavailable"),
            "error":         glx.get("error"),
        },
        "vulkan": {
            "available":   vk.get("vulkan_present", False),
            "device_name": vk.get("device_name", "unavailable"),
            "api_version": vk.get("api_version", "unavailable"),
            "note":        vk.get("note"),
        },
        "fps": {
            "gl_fps":   fps_probe.get("fps"),
            "tool":     fps_probe.get("tool", "glxgears"),
            "note":     fps_probe.get("note"),
        },
        "verdict":  _verdict(glx, vk, fps_probe),
        "tier":     _tier(glx, vk),
    }

    # write cache
    try:
        tmp = CACHE_F + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cap, f)
        os.replace(tmp, CACHE_F)
    except Exception:
        pass

    return cap

if __name__ == "__main__":
    result = run_probe()
    print(json.dumps(result, indent=2))
