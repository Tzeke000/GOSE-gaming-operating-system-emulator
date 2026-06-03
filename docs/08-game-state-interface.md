# Game-State Interface — "Mineflayer for retro games" `[CUSTOM]`

Goal (Zeke, 2026-06-03): let the AI play/observe games **without screenshots** by
reading the game's **state directly from emulator memory** — positions, scores,
health, board state — for tractable games like Pong, chess, and Mario 64. Not
every game can offer this (just like Mineflayer only works for Minecraft); we
target the cases where a RAM map exists.

## How it works
RetroArch exposes a **Network Command Interface (NCI)** on **UDP 55355** (enable
`network_cmd_enable=true`). It can read/write the running game's memory:
- `READ_CORE_MEMORY <addr> <count>` — via the system memory map.
- `READ_CORE_RAM <addr> <count>` — via achievement/RAM-array offsets.
- `WRITE_CORE_MEMORY` / `WRITE_CORE_RAM`, `GET_STATUS` (detect the running game).

The GOSE Agent's `state` capability (`agent/gose_agent/capabilities/gamestate.py`)
talks this protocol, then decodes raw bytes through a **per-game profile** (a RAM
map) into named, typed fields. The AI gets `{"ball_y": 132, "score": 3}` and acts
via `input.*` — a tight perceive→decide→act loop, no pixels.

See it run: `cd agent && python3 examples/pong_no_screenshots.py` (an AI returns a
ball every rally using only memory reads).

## We don't reinvent the wheel — we adopt the ecosystem
Researched 2026-06-03; decision in ADR-0006. Prior art we build on:

| Project | What it gives us | How we use it |
|---------|------------------|---------------|
| **stable-retro** (Farama, gym-retro fork) | RAM maps for **hundreds of games** as `data.json` (`address` + numpy-style `type` like `">u4"`) | Our profile engine **natively accepts stable-retro type descriptors**; `agent/tools/import_stable_retro.py` converts their maps into GOSE profiles. The hard part (finding addresses) is reused. |
| **pyraco** (PyPI) | A Python client for the RetroArch NCI | Reference + optional drop-in transport. Our built-in client is zero-dep so it always works on the device; pyraco can replace it. |
| **mcp-retroarch** | An **MCP server** bridging Claude/MCP clients to RetroArch (memory, savestates, screenshots, frame-advance) | Validates the approach and is a strong fit for **how Ava/Wren/Iris connect** (MCP). Plan: expose the GOSE Agent over MCP too (it controls the *whole device*, a superset of RetroArch-only). |
| **RetroAchievements** memory maps / Memory Inspector | Verified addresses + a tool to find/verify them | The recommended way to verify/fix profile addresses on hardware. |

## Profiles (RAM maps)
One JSON per game in `agent/gose_agent/profiles/` (schema + caveats in that
folder's README). Accepts both readable types (`u16`, `float32`) and stable-retro
descriptors (`>u4`, `<i2`, `|u1`). Each profile declares `read_method`
(`core_memory` vs `core_ram`) because the two commands use **different address
spaces** — stable-retro offsets map to `core_ram`.

## Honest limitations
- **Core support varies.** Cores must expose memory to the NCI. Confirmed-working:
  **Mupen64Plus-Next (N64 → Mario 64)** and **Mesen (NES)**. Many cores expose
  only the achievements read API; some none. We surface clear errors when a core
  has "no memory map defined".
- **Addresses are game+core specific** and must be **verified on hardware** — the
  committed Mario 64 addresses are community-sourced starting points, not yet
  device-verified.
- **Writing memory ≠ playing.** The Pong demo *writes* the paddle to show the
  read/decide/act loop in software; on the real device the "act" step is
  `input.button` injection (the agent's input capability), not a memory poke.
- This complements, not replaces, `screen.capture` — vision is the fallback for
  games with no usable RAM map.

## Protocol ops (added)
`state.profiles`, `state.attach {profile?}` (auto-detects via GET_STATUS),
`state.read {profile?}`, `state.status`, `state.read_raw {address,count,method}`,
`state.write_raw {address,data,method}`. Full schemas in `05-ai-control-protocol.md`.

## Sources
- RetroArch NCI: https://docs.libretro.com/development/retroarch/network-control-interface/
- stable-retro: https://github.com/Farama-Foundation/stable-retro · integration docs: https://stable-retro.farama.org/integration/
- pyraco: https://github.com/sopoforic/pyraco
- mcp-retroarch: https://glama.ai/mcp/servers/dmang-dev/mcp-retroarch
- RetroAchievements Memory Inspector: https://docs.retroachievements.org/developer-docs/memory-inspector.html
