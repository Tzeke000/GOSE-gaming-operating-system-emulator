# 32 — Play-map registry (#117): how any AI finds out how to play

**Problem it solves.** A paired AI is (often) memory-less between sessions. Without
baked knowledge it has to *rediscover* how to play each game from scratch — which
paddle it's on, what "up" does, where the score lives, when the game is over. That
rediscovery cost Wren ~2 hours the first Pong night. The play-map registry bakes that
knowledge into the OS so the next AI just reads it and plays.

**Where it lives.** `agent/gose_agent/play_maps/<id>.json`, baked into the image.
Loaded read-only at agent start by `capabilities/playmap.py` (`PlayMapRegistry`).
Distinct from `profiles/` (pure RAM-field maps for the NCI reader) — a play-map answers
the *higher-level* question "how do I actually play this?" and usually cross-links a
`ram_profile`.

**How an AI finds it (the "any AI can find it" path).** Through the agent, over the
normal token-authed channel:

- `games.playmaps` → list every play-map (id, name, system, crc, controls keys).
- `games.playmap {id: "<id>"}` → the full map for one game.

So a freshly-paired AI's first move in a play session is: `games.playmaps` → pick the
game → `games.playmap` → follow it. No host files, no memory required.

**Schema (required keys enforced by `_validate`).** `id`, `name`, `system`,
`controls`, `ram_fields`, `game_flow`. Optional-but-recommended: `core`, `crc`,
`launch`, `seats`, `ram_profile`, `play_methods`, `ai_play_notes`. Malformed maps are
logged and skipped, never crash the agent.

## The general play loop (game-agnostic)

1. **Read the map** (`games.playmap`). Trust `seats`/`controls`/`game_flow`, but treat
   any label as *verify-on-contact* — config labels have lied before (seat→player and
   score_left/right have both been flipped historically). Nudge an input, watch the RAM
   move, confirm before committing.
2. **Launch with the right seats.** `POST /launch {system, game}`; pass an explicit
   `players: [<eventpath>, ...]` (P1 first) only when you need a specific seating
   (e.g. AI-vs-AI). `/lobby/state` shows the authoritative order *before* you launch.
3. **Read the board** over the RetroArch NCI (UDP `127.0.0.1:55355`,
   `READ_CORE_RAM <addr> <count>`). On a clean single launch this is 100% reliable;
   "flaky NCI" is a symptom of multiple emulator instances / rushed relaunches, not an
   inherent fault — launch once.
4. **Drive** via the agent input ops (`input.button` for the dev pad; `input.pt_open`
   + `input.pt_event` to create and drive extra seated pads).
5. **Detect game flow** from `game_flow` (serve / game-over / new-game), and tear down
   cleanly (close any pads you opened; write the `#112` `.gameover` flag so the next
   launch starts fresh, not on a frozen finished match).

## Worked example: `pong1k2p`

`play_maps/pong1k2p.json` is the reference, fully self-contained, with a
`play_methods` block giving exact steps for **two** modes:

- **`vs_human`** — default launch (humans-first order → human=P1=LEFT, AI=P2=RIGHT);
  the AI drives the right paddle via `input.button` (no seat). Verified: Wren 9-0 vs Zeke.
- **`ai_vs_ai`** — create two `pt_open` pads (Xbox360 identity → correct es_input binds),
  launch with an explicit `players` order so they take P1/P2 and the physical pad is
  excluded, drive each via `input.pt_event` (ABS_HAT0Y up/down, BTN_START to start), read
  scores off `$14`/`$15`, stop at 9, close the pads. Verified end-to-end 2026-06-10.

Both methods, the RAM addresses, the "up decreases paddle_y" quirk, and the game-over
rule are in the JSON — enough for a memory-less AI to play with zero rediscovery.

## Remote AIs and realtime latency — run an in-guest loop

This pattern applies to any game where the input timing is tighter than the
remote round-trip.

**The problem.** When an AI drives a game via the remote MCP/agent (each
`input.button` or `state.read` is a network round-trip to the host), latency
is ~1-2 s per call.  For any game where the action window is shorter than that
— Pong's ball crosses the screen in ~1 s — the AI is always late and loses
badly.  Confirmed live 2026-06-14 on Pong: remote-path AI lost 9-0; in-guest
loop rallied a full match.

**The fix: run a small loop inside the VM.**  The VM's loopback connects to
the agent on `localhost:8731` with sub-ms latency.  A 20-50-line Python loop
reading state + sending input at 20-33 Hz is enough for any realtime arcade
game.

**How to deploy and run the in-guest loop:**

1. SSH into the VM as root (`ssh root@127.0.0.1 -p 2222`) and create a
   directory for AI-play scripts:
   ```
   mkdir -p /userdata/gose-ui/ai-play
   ```
2. Copy the runner from the host (`D:\Wren\scratch\vmput.py` for SFTP, or
   `scp` equivalent):
   ```
   py -3.11 D:\Wren\scratch\vmput.py <local_runner.py> /userdata/gose-ui/ai-play/<runner.py>
   ```
3. Run it inside the VM (via SSH):
   ```
   GOSE_AI_TOKEN=<play-or-admin-token> python3 /userdata/gose-ui/ai-play/<runner.py>
   ```

**Auth inside the VM (critical).**  A guest connection to `127.0.0.1:8731` is
*not* auto-admin — the open-loopback shortcut applies to the host/dev side
only.  Send a valid PLAY or ADMIN token.  OBSERVE tier can read state but
cannot send input (paddle stays frozen).

**Reference impl.** `agent/gose_agent/play_maps/runners/pong1k2p_runner.py` —
predict-the-bounce loop for Pong P2; token from `GOSE_AI_TOKEN` env var or
argv; no hardcoded secrets.

## Adding a new game

Author `play_maps/<id>.json` with the required keys; verify every field empirically
against the live display (not config labels); cross-link a verified `ram_profile`. Keep
`play_methods` self-contained (no dependency on host-only files) so any AI can replay it.
