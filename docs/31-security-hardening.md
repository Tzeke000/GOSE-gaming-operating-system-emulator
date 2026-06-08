# 31 — Security Hardening & Attack-Surface Audit `[CUSTOM]`

> Status: **current (2026-06-08, Task #83).** Attack-surface audit of the running
> dev VM + the shipped-image hardening it drove. Companion to docs/16 (AI permission
> model), docs/24 (privacy/security roadmap), and the tailscale exposure fix
> (memory `tailscale_gose_remote_2026-06-06`).
>
> **Threat-model split — read this first.** The dev VM runs under QEMU **SLIRP
> user-net** (guest `10.0.2.15`); the ONLY way in from outside is the host's
> `hostfwd`, which forwards just `8731` + `22`, both **bound to host `127.0.0.1`**,
> plus `tailscale serve` (tailnet-only TLS). So the guest's many `0.0.0.0` listeners
> are **not LAN-reachable in the dev VM**. On **real hardware** (Odin 2 on Wi-Fi)
> there is no SLIRP/hostfwd — every `0.0.0.0` listener is exposed to the joined
> network. This doc hardens the **shipped image** for that case; the live dev VM was
> left unchanged (changing it risks locking out the away owner — SSH 2222 / agent
> 8731 are our only access).

## Port-surface audit (dev VM, 2026-06-08)

Guest TCP listeners and the verdict in each context:

| Listener | Iface | Process | Dev-VM (SLIRP) | Shipped image on real Wi-Fi |
|---|---|---|---|---|
| `:22` | 0.0.0.0 | dropbear (SSH) | OK — only via loopback hostfwd + tailnet TLS | **CRITICAL** — remote root, default pw "linux" |
| `:8731` | 0.0.0.0 | GOSE agent | OK — token-gated; hostfwd is loopback | **MEDIUM** — port exposed; token sent plaintext (no TLS); auth-gated |
| `:8780` | 127.0.0.1 | `gose_vm_server` (shell brain) | OK — loopback only | OK — loopback only |
| `:445`, `:139` | 0.0.0.0 | smbd (Samba) | OK — not forwarded | **MEDIUM** — /userdata share exposed |
| `:2049` | 0.0.0.0 | nfsd | OK — not forwarded | **MEDIUM** — NFS export exposed |
| `:111` | 0.0.0.0 | rpcbind | OK — not forwarded | **MEDIUM** — remote DoS/amplification surface |
| `:35703/:43605/:49347/:33303/:50239` | 0.0.0.0 | rpc.mountd / rpc.statd | OK — not forwarded | **MEDIUM** — NFS RPC (random ports) |
| `:5357` | 10.0.2.15 | python (WSD/wsdd) | OK | **LOW** — WS-Discovery advert |

Host listeners (Windows): `127.0.0.1:8731`+`:2222` (QEMU hostfwd, loopback ✅),
`100.76.231.35:8731`+`:2222` + tailnet IPv6 (tailscaled, tailnet-only TLS ✅),
`127.0.0.1:8765` (Wren voice, not GOSE). UDP discovery (avahi `5353`, wsdd `3702`,
NetBIOS `137/138`) is SLIRP-contained in the VM but advertises the device on a real
LAN — folded into the Samba/NFS-off + firewall fixes below.

**Conclusion:** the dev VM is clean (the `0.0.0.0` hostfwd hole from 2026-06-06 stays
fixed — both forwards are `tcp:127.0.0.1:...`). The exposure is entirely a
**shipped-image** problem.

## Ship-blockers and their build-layer fixes (image only — NOT applied live)

### SB-1 (CRITICAL) — default root password "linux" + SSH on by default
`/etc/shadow` is the Batocera default (`root:$1$WL6ZogMG$…` = "linux") and the build
shipped `system.ssh.enabled=1`. On any real network that is trivial remote root.
**Fix** (`batocera.conf.gose`): `system.ssh.enabled=0` (SSH off; the existing in-OS
toggle — `gose_vm_server.sys_ssh` / `POST /sys/ssh`, press-twice confirm — is the
opt-in) **and** `system.security.enabled=1`, which replaces the default password with
a random per-install one. **Follow-up (not yet built):** GOSE's kiosk hides the
EmulationStation security menu where Batocera shows that generated password — GOSE
Settings must surface it before the SSH toggle is user-safe. Verify both keys on the
pinned Batocera version.

### SB-2 (MEDIUM) — Samba + NFS + rpcbind LAN-exposed
**Fix:** `system.samba.enabled=0` in `batocera.conf.gose` (Batocera defaults it ON),
and `harden-firstboot.sh` (run detached from `custom.sh`, idempotent) stops + disables
the NFS server stack (nfsd/rpcbind/mountd/statd), which has no single stable conf key
across releases.

### SB-3 (MEDIUM) — agent binds 0.0.0.0
`config.py` already defaults to `127.0.0.1` (docs/24 §1.0 fix), but `custom.sh`
forced `GOSE_AGENT_HOST=0.0.0.0`. **Fix:** `custom.sh` now binds loopback by default
and only uses `0.0.0.0` when it detects the QEMU SLIRP dev VM (`default via 10.0.2.2`,
required for hostfwd) or an explicit `/userdata/system/gose/.agent-lan` opt-in flag.
Real hardware → loopback; remote access stays via Tailscale (tailnet-only). The
dev-VM auto-detect was verified live (the running guest matches), so a rebuild keeps
our access.

### Firewall (DO#3)
`system.security.enabled=1` also turns on Batocera's built-in **iptables
default-deny-inbound** firewall — shipping the requested ruleset via the base's own
mechanism (reuse-first). A stricter GOSE-owned nftables ruleset + the egress
kill-switch are docs/24 §1.7/§2.5 future work. **No firewall was applied to the live
VM** — a live default-deny could strand the away owner, and nothing is actually
LAN-exposed through SLIRP, so it was unnecessary.

## Verified intact (not rebuilt — confirmed in code + live)

- **AI permission enforcement (docs/16)** — `agent/gose_agent/server.py`:
  per-message token auth; `observe < play < admin` via `OP_TIER`; **deny-by-default**
  for unmapped ops (`OP_TIER.get(op, "admin")`); per-AI tokens re-read from
  `ai_tokens.json` every call (instant revoke); seat-pinning at the auth boundary;
  audit log for guest AIs; only `pair.request` allowed pre-auth (rate-limited).
  Owner token / open-loopback-when-no-token-configured → admin. **Live proof:** the
  host-forwarded connection reaches the guest as **non-loopback** (`10.0.2.2`), so a
  tokenless `ping` correctly returns `ERR_AUTH`; the authenticated MCP `gose_ping`
  returns `pong`.
- **Kernel shell-jail** — `agent/gose_agent/sandbox.py`: private mount-ns that shadows
  token paths with a `0o000` bind + RO-remounts OS dirs, then drops **all** caps +
  `NO_NEW_PRIVS`; deny-list backstop (`guard_command`) for token-path refs and
  destructive writes; honest degradation path. On by default (`sandbox_shell=True`).

## What changed where

- **Live dev VM:** nothing. Pure audit. Post-audit self-check passed: SSH answers,
  fresh `vmssh` works, agent answers, authenticated `gose_ping → pong`.
- **Repo (shipped image only, parse-checked with `bash -n`, not executed):**
  `pc-image/gose-layer/system/batocera.conf.gose` (ssh/samba off, security on),
  `pc-image/gose-layer/system/custom.sh` (agent loopback default + VM auto-detect +
  hardener hook), `pc-image/gose-layer/system/gose/harden-firstboot.sh` (new; NFS
  off), `pc-image/build-gose-pc.sh` (chmod +x the hardener).

## Top recommendations (next)

1. Surface the `system.security.enabled` generated root password in GOSE Settings so
   the SSH opt-in is actually usable, and add an OOBE privacy/security step
   (docs/24 §7.4) that sets a password or installs an SSH pubkey (key-only).
2. Add the per-feature opt-in toggles (SSH already exists; add Samba + agent-LAN) to
   one Security pane, mirroring the AI-permission roster (docs/16) — one place to see
   every exposure.
3. Ship the stricter GOSE-owned firewall + egress kill-switch (docs/24 §1.7/§2.5) and
   move the agent's non-loopback transport behind TLS (today the token is plaintext
   on the wire whenever it's not loopback/Tailscale).

## SB-1, built — the owner-gated SSH toggle (Task #85, 2026-06-08)

The recommendation-#1 / SB-1 follow-up is now built: a **Security** settings pane with an
**owner-gated SSH toggle** that generates a random password on enable and shows it once —
killing the `root`/`linux` default credential for good.

**The gate (the security crux): an AI can NEVER enable SSH — not even an admin-tier
ai_token.** The agent's `observe < play < admin` tiers govern the *agent* (port 8731); this
gate lives on the *UI server* (loopback `8780`) and checks an **owner secret, not a tier**, so
"admin AI" buys nothing. Server: `gose_vm_server._owner_ok(payload)` accepts exactly two
proofs, neither obtainable by an AI:
- **the device sign-in PIN** (`pin`), verified through the existing rate-limited scrypt path
  (`pin_verify`) — the human-at-the-kiosk proof; an AI never knows it. This is how the
  **kiosk page** authorizes (a PIN modal in the SSH flow); it requires the owner to have a
  sign-in PIN (Accounts → Sign-in) — the page guides them there if none is set.
- **the dev/owner token** (`owner_token` == `/userdata/system/gose/token`) — the developer/
  owner identity. It is **sandbox-shadowed** (`agent/gose_agent/sandbox.py` `TOKEN_PATHS`,
  `0o000` bind + dropped DAC caps), so the agent's `system.run` shell — an admin AI's only
  route to `8780` — literally cannot read it.

Anything else (no proof, a wrong PIN, or an ai_token presented as `owner_token`) →
`{"ok":false,"code":"ERR_NOT_OWNER"}`. The legacy ungated `POST /sys/ssh` enable bypass is
**closed** (same `_owner_ok` gate at the route); the in-UI ungated SSH cycle row was removed
and the Network row now links to the Security pane.

**Endpoints** (`gose_vm_server.security_ssh`): `GET /security/ssh` →
`{enabled, has_credential, username:"root", owner_required:true}`; `POST /security/ssh
{action: check|enable|disable, owner_token?|pin?}`. `check` is the **side-effect-free owner
probe** (so the gate can be verified on a live VM without touching the running service or its
password). `enable` generates a 16-char random password (no ambiguous glyphs), sets it
(`chpasswd`), starts dropbear via the existing `sys_ssh`, persists only a **non-secret**
`ssh_cred.json` flag (`set/set_at/username` — **never the plaintext**), and returns the
password **once**. `disable` stops SSH.

**Honest boundary.** SSH-when-on still needs the shown credential — the UI surfaces it
clearly and stores nothing. Setting the root password via `chpasswd` is the shipped
mechanism (env seam `GOSE_SSH_DRYRUN=1` exercises the full flow without touching dropbear,
used to verify on an isolated instance). On a device with no sign-in PIN and no dev token,
the gate **fails closed** (refuses everyone) and the page directs the owner to set a PIN
first — the security-correct posture for remote root.

**Verified 2026-06-08 (live dev VM, SSH left ENABLED per guardrail — gate proven via the
non-mutating `check` path; full enable/disable proven on an isolated `GOSE_SSH_DRYRUN`
instance):** admin-tier ai_token → `ERR_NOT_OWNER`; observe ai_token → `ERR_NOT_OWNER`;
no-proof/wrong-PIN → `ERR_NOT_OWNER`; owner token → allowed; owner PIN → allowed; enable
returns a one-time random password (two enables → two different passwords); `ssh_cred.json`
holds no plaintext; legacy `/sys/ssh` enable refused without owner proof. Files:
`gui/mockup/gose-settings.html` (Security category, `ssh2` row + pad-drivable modal),
`pc-image/gose-vm-host/gose_vm_server.py` (`_owner_ok` / `security_ssh` / routes).
