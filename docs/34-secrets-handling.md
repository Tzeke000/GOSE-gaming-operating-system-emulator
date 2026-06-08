# 34 — Secrets handling (agent token)

Task #92. Fixes the ship-blocker where the agent admin token was a hardcoded
literal in `pc-image/gose-vm-host/gose_vm_server.py` — it shipped on the disk and
sat in the public repo, so anyone reading GitHub or extracting the image got
agent-ADMIN.

## The rule

The agent token is **never hardcoded and never committed**. It is resolved from
the environment / a local file, and is **generated per-install on first boot** so
every device has its own unique secret.

## Where the token comes from

Resolution order used by both the agent and the in-VM UI server:

1. **`GOSE_AGENT_TOKEN` env** — explicit override (deploy / dev).
2. **A gitignored `.env`** (dev convenience) — `pc-image/gose-vm-host/.env`,
   `/userdata/gose-ui/.env`, or `/userdata/system/gose/.env`. Template:
   `pc-image/gose-vm-host/.env.example` (committed; holds `changeme`, no real secret).
3. **The per-install token file** — `/userdata/system/gose/token`, mode `600`,
   out-of-repo. This is the canonical on-device secret.
4. **First-boot generation** — if no token exists, one is generated with
   `secrets.token_hex(16)` and persisted to the token file.

`pc-image/gose-layer/system/custom.sh` generates the per-install token (step 4)
**before** the agent starts, then exports it to the agent. The UI server
(`gose_vm_server.py`) reads the same file, so the agent and UI converge on one
token. The UI server keeps a defensive self-generate (same file) as a fallback.

## What's in git

- `.gitignore` blocks `.env` / `*.env` (except `.env.example`), `*.bak`, and bare
  `token`.
- `.env.example` is the only committed token file, and it contains no real secret.
- The real per-install token lives only on the device (`/userdata/system/gose/token`).

## Real-hardware installer

`scripts/install-agent.sh` already follows the same pattern (generate once,
persist to `agent.token`, `chmod 600`, feed the systemd unit) — unchanged.

## Follow-ups for Zeke (NOT done here, by design)

- **Rotate the live dev token** (`8bdb…`) via a coordinated relaunch — it has been
  public, so it should be considered compromised even after this fix.
- **Git-history scrub** — the literal is still in past commits. Purge with
  `git filter-repo` (or BFG) and force-push. Left to Zeke (rewrites history).
- The dev VM's host-side tooling (`D:\Wren\.mcp.json`, `experiments/wren_vs_*.py`)
  still carries the dev literal in Wren's *private* repo — dev credential, not a
  shipped/public exposure; can be migrated to a local `.env` later.
