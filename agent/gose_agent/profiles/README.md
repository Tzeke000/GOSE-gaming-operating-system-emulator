# Game profiles — the RAM maps that make state-reading work

A profile maps an emulator's memory addresses to **named, typed fields** so the AI
gets `{"mario_x": 1024.0, "stars": 7, "health": 8}` instead of pixels. One JSON
file per game. See `../capabilities/gamestate.py` and
`../../../docs/08-game-state-interface.md`.

## Schema
```jsonc
{
  "name": "Super Mario 64",
  "system": "n64",
  "core": "mupen64plus_next",     // which libretro core this was mapped against
  "endian": "big",                // "big" (N64) or "little" (default)
  "read_method": "core_memory",   // READ_CORE_MEMORY (recommended)
  "match": {                      // used by state.attach auto-detect
    "game_substr": "super mario 64",
    "crc": ""                     // optional CRC32 from GET_STATUS
  },
  "notes": "Addresses are starting points — verify on hardware.",
  "fields": [
    { "name": "mario_x", "address": "0x33B1AC", "type": "float32" },
    { "name": "stars",   "address": "0x207624", "type": "u16" },
    { "name": "health",  "address": "0x33B21E", "type": "u8", "scale": 0.125 }
  ]
}
```

### Field types
`u8 s8 u16 s16 u32 s32 float32`. Optional per-field: `endian` (overrides profile),
`scale` (multiply decoded value), `bool` (coerce to true/false).

## ⚠️ Addresses must be verified
Memory addresses are **game + core specific** and the values committed here are
**unverified starting points** pulled from public RAM-map conventions. The way to
confirm/fix them on the device:
1. Launch the game in RetroArch with `network_cmd_enable=true`.
2. Use the **RetroAchievements Memory Inspector** (or `state.read_raw`) to watch
   addresses change as the game state changes, and lock down the real offsets.
3. Update the profile and commit. Then `state.read` returns trustworthy fields.

Core support varies: cores must expose a **system memory map** to the Network
Command Interface. Confirmed-working: **Mupen64Plus-Next (N64)**, **Mesen (NES)**.
Many cores only implement the achievements read API and will report
"no memory map defined".
