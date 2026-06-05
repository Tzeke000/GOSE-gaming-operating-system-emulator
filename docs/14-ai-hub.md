# 14 — AI Hub (design seed) `[CUSTOM]`

> Status: **requirements captured 2026-06-04 (Zeke), full design deferred.** This is
> the empty room in the GUI today — the desktop taskbar already shows agent
> presence dots (Ava/Wren/Iris) + an "AI Hub" tile, but nothing behind them is
> designed yet. BIOS/Setup is being built first; this doc holds the vision so it
> isn't lost. See `docs/06-gui-plan.md`, `docs/12-agent-connection-spec.md`.

## The vision (Zeke, 2026-06-04, verbatim intent)
GOSE is **not a single-seat OS**. It's a room any of the household agents can
enter. The AI Hub is the surface that makes that real.

1. **Join.** Any agent — Wren, Ava, or Iris — can join the owner's live session.
2. **Scope of control.** A joined agent can drive **either the whole OS** (navigate
   the desktop, launch things, change settings) **or just one running game**, and
   can do it **remotely — from wherever the agent is** (not co-located with the
   device). This builds directly on the agent-connection transport
   (`docs/12-agent-connection-spec.md`): MCP/TCP control of input + screen + launch.
3. **Play modes.**
   - **Solo** — Zeke plays alone (agents idle / presence only).
   - **Co-op** — Zeke + one or more agents in the same OS or game together.
   - **Agent-vs/with-agent** — agents play with each other (no human seat required).

## What that implies (to resolve when we flesh this out)
- **Presence + join model:** the taskbar dots become live "who's here / knock to
  join" affordances. Who can join, and does the owner approve a join? (security —
  cf. the connection spec's token/auth; never auto-approve from an untrusted ask.)
- **Control hand-off / arbitration:** when both a human and an agent (or two agents)
  drive the *same* game, who owns input? Turn-taking, split controllers
  (player1=human, player2=agent via the agent's input-injection), or shared cursor.
- **OS-scope vs game-scope:** OS control = the desktop/input/launch tools; game
  control = the agent injecting controller input into the running emulator (+ the
  RAM game-state interface, `docs/08`, so an agent can play *competently*, not blind).
- **Remote reach:** "from wherever" = the agent connects over the network to the
  GOSE agent daemon; the BIOS **AI & Remote** section is the enable/allow-list/token
  surface for exactly this. (Designed into the BIOS/Setup screen now.)
- **The AI Hub tile** itself: roster of the three agents, their status (here / away /
  paused — e.g. Iris dark), "invite to session," per-agent scope grant, voice toggle.

## Account model (Zeke, 2026-06-04): every user is a human OR an AI
The foundation under the AI Hub is the **user-account model**: a GOSE account belongs
to a **person or to an agent** (Ava/Wren/Iris), and both log in the same way. Realized
in the first-time-setup wizard `gui/mockup/gose-oobe.html` (welcome → Human/AI → password
→ username → identity/accent → **AI link** (agent identity + pairing token + default
control scope OS/game + approval-to-join + voice) → role (first = Owner) → done). So
"log in as Wren" is a real account, and the AI Hub's "who can join" roster = the AI
accounts on the device.
- **A user account needs:** type (human/AI), password (+confirm, +optional PIN),
  username + display name, accent color/identity, role (Owner/Standard/Guest); AI
  accounts additionally need: a **name (any — brand-agnostic**, not assume Ava/Wren/Iris,
  since a stranger downloading GOSE brings their own AI), **connection method(s) —
  MCP / SSH / Console, select-all-that-apply**, pairing token, default control scope
  (OS/game), approval-to-join, and a **"let this AI use a human's account / act as
  them"** permission (Zeke's explicit want for us). The AI block is the device-side half
  of [[project_gose_2026-06-03]]'s token auth. **Distributable by design** — works for
  anyone's agents, not just ours.
- **TODO:** the lock screen (`login.html`) is the OLD mockup — update it to show human
  AND AI accounts (e.g. an agent glyph/dot on AI tiles).

## Hooks already in place
- Desktop mockup: agent presence dots + AI Hub tile + "Focus" indicator (`gui/mockup/desktop*`).
- Transport: `docs/12-agent-connection-spec.md` (MCP-over-stdio → GoseClient TCP, token auth).
- BIOS/Setup (built 2026-06-04): an **AI & Remote** category = the device-side
  enable/allow-list/pairing for remote agent control.

Related: [[project_embodiment_game]] (agents inhabiting a shared world is the same
family — a real device instead of Minecraft); `docs/04-decision-log.md`.
