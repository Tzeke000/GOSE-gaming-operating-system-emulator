# GOSE Toolchain — open-source tools worth pulling in

Curated 2026-06. The philosophy (per CLAUDE.md): **don't reinvent.** Flash a mature
distro, then lean on existing projects. Tags: **[adopt now]** = use/integrate this
build · **[on device]** = install during device setup · **[later]** = when relevant ·
**[ref]** = learn from / optional.

## 1. Base OS & front-end
| Tool | Why | Tag |
|------|-----|-----|
| **ROCKNIX** (github.com/ROCKNIX/distribution) | Stable Linux on all 3 Odin 2 variants; our primary base. | [on device] |
| **Batocera** (batocera.org) | SM8550 image; biggest emulation library; second base. | [on device] |
| **batocera-emulationstation** (github.com/batocera-linux/batocera-emulationstation) | The front-end we theme for the Windows look (XML theme v7). | [later] |
| **EmulationStation-DE / ES-DE** (gitlab.com/es-de) | Alt front-end; different theme engine (not Batocera-compatible). | [ref] |

## 2. Emulation cores / engines
| Tool | Covers | Tag |
|------|--------|-----|
| **RetroArch + libretro cores** | NES…PS1, plus the memory interface our game-state feature uses. | [on device] |
| **PPSSPP** (github.com/hrydgard/ppsspp) | **PSP — the flagship requirement**; runs great upscaled. | [on device] |
| **PCSX2** | PS2. | [on device] |
| **Dolphin** | GameCube / Wii. | [on device] |
| **DuckStation** | PS1 (best-in-class). | [on device] |
| **mupen64plus-next** (libretro) | **N64** — exposes a memory map to RetroArch's NCI (Mario 64 game-state!). | [on device] |
| **Mesen** (libretro) | NES — also exposes a memory map for game-state. | [on device] |
| **melonDS / Flycast / mGBA** | DS / Dreamcast / GBA. | [on device] |
| **Eden** (Switch) | Current active Switch fork after Yuzu/Citron DMCAs. **Best-effort, not bundled** (legal hygiene; user provides). | [later] |

## 3. Light PC games (translation layer)
| Tool | Why | Tag |
|------|-----|-----|
| **Box64 / Box86** (github.com/ptitSeb/box64) | x86→ARM translation; the core of running PC games on the Snapdragon. | [on device] |
| **Wine** + **DXVK** | Windows API + D3D→Vulkan. Best for pre-2013 / DX9 titles. | [on device] |
| **Winlator** (github.com/brunodev85/winlator) | Packages Wine+Box64+DXVK; optimized for Snapdragon. Android-oriented — **[ref]** for the Linux setup recipe. | [ref] |

## 4. Input (native pad + mouse/keyboard + PS5)
| Tool | Why | Tag |
|------|-----|-----|
| **xpadneo** (github.com/atar-axis/xpadneo) | Full Xbox controller support incl. rumble over BT. | [on device] |
| **hid-playstation** (mainline kernel) | Native DualShock 4 / **DualSense (PS5)** support. | [on device] |
| **AntiMicroX** (github.com/AntiMicroX/antimicrox) | **Maps a gamepad to mouse + keyboard with per-app auto-profiles** — the key to driving the *Windows-style desktop pointer* with the Odin pad. | [adopt now] |
| **joycond** / **hid-nintendo** | Switch Pro / Joy-Con pairing. | [on device] |
| **evtest / evdev / sdl2** | Inspect + inject input; SDL2 is what most emulators read pads through. | [on device] |
See `07-controllers.md` for the full input architecture.

## 5. Media / box art / assets pipeline
| Tool | Why | Tag |
|------|-----|-----|
| **Skyscraper** (github.com/muldjord/skyscraper) | Scrapes box art / screenshots / metadata and writes gamelist.xml — feeds the GUI library views. | [adopt now] |
| **mpv / ffmpeg** | Video previews, screen recording, transcoding. | [on device] |

## 6. AI / control (our custom layer + ecosystem)
| Tool | Why | Tag |
|------|-----|-----|
| **MCP Python SDK** (github.com/modelcontextprotocol/python-sdk) | The standard Ava/Wren/Iris/Claude speak. Our `mcp/gose_mcp_server.py` is zero-dep, but the SDK is the reference. | [ref] |
| **mcp-retroarch** (glama.ai/mcp/servers/dmang-dev/mcp-retroarch) | Existing MCP server for RetroArch; validated our approach. | [ref] |
| **stable-retro** (github.com/Farama-Foundation/stable-retro) | Hundreds of RAM maps for the game-state interface; we import them. | [adopt now] |
| **pyraco** (github.com/sopoforic/pyraco) | Python RetroArch NCI client; optional transport. | [ref] |
| **python-evdev** (github.com/gvalkov/python-evdev) | Real `uinput` injection backend for the agent. | [on device] |
| **anthropic SDK** | If a GOSE-side agent ever calls Claude directly. | [later] |

## 7. Dev tooling (for building GOSE itself)
| Tool | Why | Tag |
|------|-----|-----|
| **pytest / ruff / black** | Tests + lint + format for the agent/bridge code. | [adopt now] |
| **Godot 4** (github.com/godotengine/godot) | Candidate for a custom gamepad-first front-end (Path B). | [later] |
| **SSH (dropbear/openssh)** | The console/tinkering path; also a connection path for the AI. | [on device] |

## 8. Graphic design / theming
| Tool | Why | Tag |
|------|-----|-----|
| **Inkscape** (CLI-capable) | SVG icons/tiles for the Windows theme + system logos. | [adopt now] |
| **GIMP / Krita** | Wallpapers, box-art touch-ups, raster assets. | [adopt now] |
| **ImageMagick** | Batch art processing in the asset pipeline (resize/pad/format). | [adopt now] |
| **Blender** | 3D logo / animated boot splash, render marketing shots. | [later] |
| **Lucide / Phosphor icons**, **Inter / Manrope fonts** | Clean, license-friendly UI icon + font sets for the desktop. | [adopt now] |

## Immediate integration shortlist
1. **Skyscraper** → wire into `scripts/setup-device.sh` to populate libraries.
2. **AntiMicroX** → ship a GOSE profile so the Odin pad drives the desktop pointer.
3. **stable-retro importer** (done: `agent/tools/import_stable_retro.py`) → seed profiles.
4. **Design assets** (Inkscape/ImageMagick) → produce the Windows theme tiles/icons.
5. **Dev**: add `ruff` + `pytest` config so contributions stay clean.
