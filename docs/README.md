# docs/ — index

Every numbered doc, one line each, with an honest status. Numbering gaps
(**13, 20, 22**) never existed in git history — numbers were skipped, nothing
was deleted. (A comment in `pc-image/gose-vm-host/gose_vm_server.py` cites a
"docs/20" for the Store; that doc was never written — the Store's license rules
live in docs/19.)

Status legend: **current** = trust it as written · **design** = approved/pending
spec, build may be partial · **historical** = preserved for the record, parts
superseded by later commits (flagged below, not rewritten).

| Doc | What | Status |
|-----|------|--------|
| [00-project-brief.md](00-project-brief.md) | The owner's original handoff brief — source of truth for *intent*; do not edit. | current (preserved) |
| [01-research-findings.md](01-research-findings.md) | Verified Odin 2 Linux research (ROCKNIX/Batocera, bootloader, GPU), 2026-06-03, sourced. | current |
| [02-os-install-runbook.md](02-os-install-runbook.md) | Stock Android → Linux-on-SD flash runbook, Android untouched. | current `[needs hardware]` |
| [03-architecture.md](03-architecture.md) | System diagram: AI agents ↔ GOSE Agent ↔ front-end/emulators. | current |
| [04-decision-log.md](04-decision-log.md) | **All ADRs**, append-only, newest first — the *why* behind everything. | current |
| [05-ai-control-protocol.md](05-ai-control-protocol.md) | Agent control protocol v0: newline-JSON over TCP (requests/responses/events). | current |
| [06-gui-plan.md](06-gui-plan.md) | Early GUI plan: ES-theme (Path A) vs Godot app (Path B). | **historical** — the shipped shell became the web kiosk (`gui/mockup` pages in `kiosk.py`), neither path as written; see docs/23 |
| [07-controllers.md](07-controllers.md) | Controller support matrix (Xbox/PS/Switch/8BitDo) + on-device drivers. | current matrix; **input-path parts superseded** by docs/27 + the input-level passthrough (commits cec3bdf/6994770) |
| [08-game-state-interface.md](08-game-state-interface.md) | "Mineflayer for retro": read emulator RAM via RetroArch NCI, no screenshots. | current |
| [09-toolchain.md](09-toolchain.md) | Curated open-source tools to adopt (reuse-first philosophy). | current |
| [10-boot-menu.md](10-boot-menu.md) | Layered boot model + the GOSE Boot Menu ("BIOS", L1+R1). | current |
| [11-pc-app-and-input.md](11-pc-app-and-input.md) | GOSE on PC = a QEMU VM (ADR-0013) + boot-time input chooser. | current |
| [12-agent-connection-spec.md](12-agent-connection-spec.md) | How AI agents connect — **RESOLVED 2026-06-04** (MCP/stdio; unblocked ai-bridge question). | current |
| [14-ai-hub.md](14-ai-hub.md) | AI Hub vision (multi-agent room); requirements captured, design deferred. | design (deferred) |
| [15-brand.md](15-brand.md) | Brand: the GOSE Core crystal — symbol, palette, usage. | current |
| [16-ai-permission-model.md](16-ai-permission-model.md) | AI permission/elevation model (UAC-like grants, capability tokens). | design (proposal 2026-06-05) |
| [17-os-roadmap.md](17-os-roadmap.md) | "What GOSE still needs" gap inventory. | current as of 2026-06-05 — the 06-06/06-07 build waves (widgets, game-bar, store tabs, passthrough, OOBE) closed several items; cross-check ROADMAP.md |
| [18-roadmap-build-plans.md](18-roadmap-build-plans.md) | Buildable specs per roadmap area (9-agent design fan-out). | design reference |
| [19-license-audit.md](19-license-audit.md) | **License audit** — paid-distribution / Steam ship-blocker review, per-core verdicts. Do not edit findings. | current (ship-blocker) |
| [21-widget-standard.md](21-widget-standard.md) | Desktop widget standard (`widget.js`/`widget.css` contract). | current (implemented 2026-06-06) |
| [23-windowing-design.md](23-windowing-design.md) | Windowing/multitasking architecture + phased plan (WinBox, suspend/resume). | design (approved; wave-1 build pending) |
| [24-os-needs-and-privacy.md](24-os-needs-and-privacy.md) | Privacy-first OS-needs research + adopt-don't-reinvent roadmap. | current (2026-06-06; some privacy fixes since shipped) |
| [25-first-boot-oobe.md](25-first-boot-oobe.md) | First-boot ladder, OOBE wizard, preloaded services, default app set. | design approved; OOBE build in progress (commits 7c1b1c2, c4aed9c) |
| [26-assets-and-sound.md](26-assets-and-sound.md) | The owner's brand art + sound set and the sound manager that plays it. | current |
| [27-controller-standard.md](27-controller-standard.md) | **The controller standard**: one button language, one input path; page-level pad reads are bugs. | current (adopted 2026-06-07) — note: the §2 diagram's "physical pad ──(usb-redir)" label predates cec3bdf; physical pads now arrive via input-level passthrough (`pad_passthrough.py`) |
| [31-security-hardening.md](31-security-hardening.md) | Attack-surface audit + shipped-image hardening (root pw, SSH/Samba/NFS off, agent loopback, firewall). | current (2026-06-08) |
| [asset-prompts/](asset-prompts/README.md) | Ready-to-paste AI prompts for motion/audio brand assets (boot anim, trailer, VO). | current |

Other doc-shaped files elsewhere: `../CLAUDE.md` (project memory, read first),
`../ROADMAP.md` (live status), `../STRUCTURE.md` (what-lives-where map), and the
per-directory READMEs (`agent/`, `mcp/`, `ai-bridge/`, `gui/mockup/`,
`pc-image/`, `pc-image/gose-vm-host/`, `pc-image/dist/`).
