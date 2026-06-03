# GOSE Agent

The device-side daemon that lets your AI agents (Ava / Wren / Iris) **control the
whole Odin 2** — play games and fix the OS — over Wi-Fi/Ethernet or a USB cable.

See `../docs/03-architecture.md` and `../docs/05-ai-control-protocol.md` for the
full design and protocol.

## What it does
| Capability | Real backend (on device) | Mock backend (CI / container) |
|-----------|--------------------------|-------------------------------|
| `input.*`  | virtual gamepad via `uinput` (python-evdev) | records events |
| `system.*` | shell, health status, services | shell works; status degrades gracefully |
| `games.*`  | enumerate + launch emulators | enumerates; launch runs "dry" |
| `screen.*` | grim/scrot/fbgrab/framebuffer | tiny placeholder PNG |

Backends auto-select by probing the environment, so the exact same code runs in
this repo's tests and on the handheld.

## Run it
```bash
cd agent
# dev (loopback only, mock-friendly):
python3 -m gose_agent
# expose over Wi-Fi/USB with auth:
GOSE_AGENT_TOKEN=$(openssl rand -hex 16) GOSE_AGENT_HOST=0.0.0.0 python3 -m gose_agent
```

## Drive it
```bash
python3 client/cli.py ping
python3 client/cli.py run "uname -a"
python3 client/cli.py tap a
python3 client/cli.py launch psp "God of War"
# from another machine / the AI host:
GOSE_HOST=192.168.1.50 GOSE_TOKEN=... python3 client/cli.py status
```
Or from Python (this is what the AI bridge imports):
```python
from gose_client import GoseClient
with GoseClient("192.168.1.50", 8731, token="...") as c:
    c.launch("psp", "God of War"); c.tap("start")
```

## Test
```bash
python3 -m unittest discover -s tests -v   # 0 external deps
```

## Configuration (env or JSON via GOSE_AGENT_CONFIG)
| Env | Default | Meaning |
|-----|---------|---------|
| `GOSE_AGENT_HOST` | `0.0.0.0` | bind address |
| `GOSE_AGENT_PORT` | `8731` | TCP port |
| `GOSE_AGENT_TOKEN` | _(none)_ | required for non-loopback clients |
| `GOSE_AGENT_ALLOW_SHELL` | `true` | enable `system.run` (OS-fix path) |
| `GOSE_AGENT_ROMS_DIR` | `/storage/roms` | systems = subdirs |
| `GOSE_AGENT_FORCE_MOCK` | `false` | force mock backends |

`launch_templates` (JSON config) maps a system to a launch command, e.g.
`{"launch_templates": {"psp": "ppsspp {game}"}}`. `{game}` is the ROM path.

## On the Odin 2 — real backends `[needs hardware]`
- Install python-evdev: `pip3 install evdev` (or the distro package).
- Ensure `/dev/uinput` is writable by the agent (run as root, or udev rule). The
  agent then registers a **"GOSE Virtual Gamepad"** that emulators treat as a real
  controller.
- Provide real `launch_templates` for each system (match the distro's emulator
  commands) so `games.launch` starts titles for real.
- Screen capture: install `grim` (Wayland) or `scrot` (X11), else it falls back to
  the framebuffer.
- Run it as a service (see `../scripts/install-agent.sh`).

## The "cable" path — USB gadget networking `[needs hardware]`
When tethered by USB-C, put the Odin in **USB gadget mode** and bring up a USB
network device so the tower gets a `usb0` link to the handheld; the agent already
listens on `0.0.0.0`, so nothing else changes. Outline (configfs, run on device):
```bash
modprobe libcomposite
# ... configure a g_ether / ECM gadget via /sys/kernel/config/usb_gadget/...
# bring up usb0 with a static IP on both ends (e.g. 10.55.0.1 device / .2 host)
```
Fallback: a USB **serial** gadget speaking the same JSON-lines protocol. Both are
documented further once validated on hardware.

## Security
- Token required for any non-loopback peer. Bind to LAN/USB; for internet access,
  prefer an SSH tunnel over exposing the port.
- `system.run` is intentionally powerful (it's how the AI repairs the OS). Disable
  with `GOSE_AGENT_ALLOW_SHELL=false` if you want a play-only agent.
- TLS arrives when we move the transport to WebSocket (see decision log).
