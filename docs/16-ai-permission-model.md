# 16 — AI Permission & Elevation Model `[CUSTOM]`

> Status: **design proposal, 2026-06-05 (for the owner's review before build).**
> This is the security layer UNDER the AI Hub (`docs/14-ai-hub.md`) and the
> credential half of the agent transport (`docs/12-agent-connection-spec.md`).
> Reuse-first: most of this is an existing capability-token pattern, not new crypto.

## The owner's requirements (verbatim intent, 2026-06-05)
1. An AI can be granted **Admin permission to GOSE**, toggled on/off by a human,
   **like Windows/UAC.** If the AI has elevated perms it's good; if not, **it cannot
   give itself admin — a human must grant first.** After that, it's trusted.
2. The AI **shouldn't have to re-ask every boot** — the grant is **saved, encrypted,
   and the AI holds it**, so it can connect ("hack in") over the **AI port via Wi-Fi
   or Bluetooth** without a fresh approval each time.
3. This sits under multiplayer (human + AI) and AI-plays-from-its-own-machine /
   the Odin 2 (`docs/14`).

## The core idea (one sentence)
A human grants an AI a **scoped, revocable capability token bound to that AI's own
key**; the token can only be *narrowed* by its holder, never widened — so the AI can
**ask** for more but can **never self-elevate** — and the AI stores it **encrypted**
and presents it on every connection, so no re-approval is needed until the human
revokes.

## Permission tiers (= scopes on the existing agent/MCP transport)
Map directly onto the tools the GOSE agent (port 8731) + MCP already expose:

| Tier | Scope name | Grants | Maps to |
|------|-----------|--------|---------|
| **Observe** | `gose:observe` | read-only: screenshot, list games, read game state | `gose_screenshot`, `gose_list_games`, `gose_state_read`, `/status.json` |
| **Play** | `gose:play` | launch/stop games, send controller input | `gose_launch/stop/tap/axis/run`, `/launch` |
| **Admin** | `gose:admin` | full OS: shell, settings, install, network, power | `/term/exec`, `/sys/power`, `/store/install`, `/net/*`, `/bt`, `/fs/op` |

A freshly-paired AI defaults to **Observe** (safe). **Play** and **Admin** require an
explicit human grant. This *is* the UAC tiering — admin is never inferred.

## Why the AI can't self-elevate (the enforcement, not just a rule)
The token is a **macaroon / holder-of-key capability** (Google's macaroon design;
the same shape DeepMind's 2026 "delegation capability tokens" use for agents). Two
properties do the work:
- **Attenuation-only:** a holder can add restrictions (caveats) but **cannot remove
  them or broaden scope** without the root signing key — which lives only on GOSE.
  So an AI holding a `gose:play` token literally cannot mint itself `gose:admin`.
- **Holder-of-key:** the token is bound to the AI's public key (ed25519). A token
  copied by a different agent fails — presenting it requires proving possession of
  the matching private key. (Same lesson as macOS TCC binding grants to a signed
  identity.)
The only place `gose:admin` is ever born is a **human action in GOSE Settings.**

## Grant + revoke flow (OAuth device-grant shaped)
This is the standard "a headless device needs a human to approve it" pattern
(RFC 8628), adapted:
1. AI connects over the AI port (Wi-Fi/BT) and authenticates its **identity key**.
   First contact = `gose:observe` only.
2. AI **requests** a higher tier it lacks → GOSE shows a pending request + a short
   code in **Settings → AI & Remote → "AI Access"** (the human-facing UAC surface).
   The AI cannot proceed; it waits.
3. **Human approves on the device** (or a phone on the same network), choosing the
   tier (Observe / Play / Admin). This is the only birthplace of elevation.
4. GOSE mints a **scoped capability token** bound to the AI's key (+ a TTL) and a
   long-lived **refresh credential**, and writes a **grant record** to a small
   SQLite store: `(agent_pubkey, name, scopes, granted_at, revoked=false)`.
5. **Revoke** = the human flips the AI's toggle in the same screen → `revoked=true`
   + rotate the refresh credential. GOSE checks the revoked flag on **every**
   privileged call, so revocation is **immediate**, not next-boot.

## The saved encrypted grant (no re-ask each boot — the owner's point 2)
- **The AI holds:** its ed25519 private key + the refresh credential, stored
  **encrypted at rest** (age / libsodium sealed file) on the AI's own machine. At
  startup it decrypts locally, derives a short-lived access token, and connects.
- **No human re-approval** is needed as long as the grant record on GOSE still says
  `revoked=false`. That's the "encrypted thing the AI has, connects over the port
  every boot" you described — done safely (the human grant, not device sealing, is
  the security boundary).
- **GOSE holds:** the grant SQLite DB + the root signing key in an age-encrypted
  file unlocked at boot (TPM-sealed later if the Odin 2 has one; software-encrypted
  otherwise).

## Where it plugs into what already exists
- **Agent token (today):** `GOSE_TOKEN` is one shared secret = all-or-nothing. This
  model **replaces it with per-agent, per-scope tokens** — the agent's `server.py`
  auth check becomes "valid signature + scope covers this tool + not revoked."
- **MCP:** one scope per tool/tool-group; an out-of-scope call returns
  `403 insufficient_scope` naming the needed scope → that *is* the AI's elevation
  request (MCP's own step-up flow).
- **Settings → AI & Remote:** the current stub rows (Remote control / Allowed agents
  / Require approval / Pairing token) become the **real grant/revoke UI** — a roster
  of AI accounts, each with a tier dropdown (Observe/Play/Admin) and an instant
  revoke toggle.
- **OOBE AI block** (`gose-oobe.html`) already collects agent name + connection
  methods + default scope — it becomes the first grant.
- **Multiplayer / Odin 2 / LAN+BT (`docs/14`):** the AI joins from its own machine
  or the device over the AI port; the tier it holds decides whether it can drive the
  whole OS, just one game, or only observe — same token, enforced per tool.

## Reuse (per "research for anything already built")
- **Capframe** (https://github.com/capframe/capframe, MIT) — the closest existing
  building block: mints **macaroon-style, ed25519 holder-of-key, revocable scoped
  tokens** with a CLI (`bind --agent X --tools … --ttl`), instant `revoke`, and
  signed audit receipts, with a Python **Guard** that enforces at the tool-call
  boundary. Its model is almost exactly this design. **Evaluate Capframe's Guard to
  wrap the GOSE agent's tool handlers before writing a bespoke token format.**
- Patterns borrowed (not whole systems): OAuth 2.0 Device Grant (RFC 8628) for the
  approval flow; MCP OAuth scopes for per-tool gating; macOS TCC's
  `(identity, scope, allowed)` table for the grant store; Home Assistant's
  refresh-token-revokes-all for the kill switch; age/libsodium for at-rest encryption.
- Considered + set aside: Linux **polkit** (native UAC analog but CVE-heavy — keep
  any surface tiny); **Cerbos** (great policy engine, too heavy for a handheld with
  3 tiers); a self-rolled token format (don't — Capframe already solved it).

## Build phases (proposed)
1. **Grant store + tiers** — SQLite `(pubkey, name, scopes, revoked)`; agent auth
   checks scope+revoked instead of the single shared token. (Back-compat: keep
   `GOSE_TOKEN` working as an `admin` grant during transition.)
2. **Settings → AI Access UI** — real roster + per-agent tier + revoke toggle (the
   UAC surface). Demoable without the full crypto.
3. **Capability tokens** — adopt Capframe (or macaroons) for holder-of-key,
   attenuation-only tokens + the request/step-up flow.
4. **Encrypted local grant** — age-sealed keyfile on the AI side; auto-connect each
   boot; human revoke kills it.
5. **Wire to multiplayer/Hub** — tier decides OS-scope vs game-scope per join.

## Open questions for the owner
- Default tier for a brand-new paired AI: **Observe** (safe, my rec) vs **Play**?
- Should **Admin** grants auto-expire (re-confirm every N days) or be permanent
  until revoked? (Windows admin is permanent-until-changed; a gaming device could go
  either way.)
- One toggle per AI, or per-tier toggles (Observe/Play/Admin as three switches)?

Related: `docs/12-agent-connection-spec.md`, `docs/14-ai-hub.md`,
`docs/05-ai-control-protocol.md`. Memory: `project_gose_2026-06-03`.

## Client-side credential storage (spec only — built 2026-06-06: server side; client side is guidance)

What's actually **enforced** today is all server-side: the per-AI token is a bearer
secret minted by the Hub (`/ai/grant`), checked by the agent on every message
(re-read from `ai_tokens.json`, never cached), and killed instantly by revoke
(`/ai/revoke` → token vanishes from the map → next op fails `ERR_AUTH`). Every op a
guest AI runs — allowed or denied — lands in `/userdata/system/gose/ai_audit.jsonl`.

What's **recommended** (not enforced — GOSE can't reach into the AI's machine) for
any client that stores its token:

- **Encrypt at rest with the OS keystore**, not a plaintext file: Windows **DPAPI**
  (`CryptProtectData` / Python `keyring`), Linux **libsecret**/Secret Service,
  macOS **Keychain**. The token is a bearer secret — whoever holds the bytes IS
  that AI to GOSE.
- **Never** commit it, log it, or put it in env files that sync (dotfiles repos).
- **Rotation = revoke + regrant.** There is no refresh flow; the owner revokes in
  the Hub (or `POST /ai/revoke`) and grants again, which mints a fresh token. Do
  this on any suspected leak; it's cheap and instant.
- Treat `ERR_AUTH` as "re-pair", not "retry": the grant is gone, ask the owner
  (op `pair.request {name, tier}` files a request the owner sees in the Hub).

Honest boundary: a malicious process on the AI's own machine that can read its
keystore can steal the token. GOSE's defense there is the blast-radius cap (tier +
seat pinning + audit + instant revoke), not client-side crypto it can't verify.
