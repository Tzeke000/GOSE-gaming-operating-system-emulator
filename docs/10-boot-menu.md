# 10 — Boot Menu / "BIOS" (controller-driven, PC-style)

> Goal (owner, 2026-06): "Hold two side buttons at power-on to get into a
> bootloader, like a Windows PC." This doc describes the layered boot model and
> the **GOSE Boot Menu** we build to deliver that feel.

There are **two layers**. Only the upper one is ours to design.

## Layer 0 — Firmware bootloader (fastboot / EDL) · can't restyle
Baked into the Snapdragon 8 Gen 2. Entered with a **fixed Qualcomm/AYN button
combo** held at power-on (volume + power family) or `adb reboot bootloader`.
This is the low-level mode used to **flash GOSE in the first place** (the one-time
`abl` bootloader mod + the fastboot *"switch boot mode"* that lets Linux boot off
SD). We cannot remap its combo or skin it — we only **document** the exact keys.
- Exact Odin 2 combo + commands: **[needs hardware]** — capture in
  `docs/02-install-runbook.md` and `scripts/setup-device.sh` once the unit is here.
- The GOSE Boot Menu offers a **"Fastboot / Flash"** entry that simply reboots
  into this firmware mode, so users rarely need the raw combo.

## Layer 1 — GOSE Boot Menu ("BIOS") · this is the PC-like one we build
A sleek, controller-driven boot picker shown **when the trigger combo is held
during a short window right after power-on** — the analog of tapping `F12`/`DEL`.
If nothing is held, the **default entry auto-boots after a timeout** (a POST-style
countdown you can interrupt by holding the combo or pressing any input).

- **Mockups:** `gui/mockup/bootmenu.html` (navigable) + `bootmenu-concept.png`.
- **Trigger combo (default):** **L1 + R1** held at power-on. Configurable; logical
  button names keep it controller-agnostic. The *exact* physical buttons + whether
  they're readable that early on the Odin 2 is **[needs hardware]** — volume-down is
  the likely low-level fallback since it's read closer to firmware.
- **Timeout (default):** 5 s auto-boot to the default entry.

### Menu entries
| Group | Entry | Action |
|-------|-------|--------|
| Boot device | ROCKNIX (microSD) — **default** | boot the Linux SD |
| | Android (internal) | reboot to stock Android |
| Tools | Recovery | system repair / factory reset |
| | Safe Mode | software render, no overclock |
| | Fastboot / Flash | reboot into Layer-0 firmware bootloader |
| | GOSE Setup | BIOS settings — boot order, timeout, theme |
| | Power Off | shut down |

> Distro choice (ADR-0012): a **single Linux OS = ROCKNIX**, dual-booted with stock
> **Android**. Batocera is a documented fallback only and is not in the menu.
>
> Caveat: switching **between** the ROCKNIX SD and internal Android is not a pure
> software choice on the Odin 2 — it depends on the abl-mod + fastboot boot-mode
> switch. So the Android entry triggers a **reboot into the right firmware mode**
> rather than chain-loading directly. Final command wiring is **[needs hardware]**.

## Detection mechanism
Pure decision logic lives in **`scripts/gose_bootmenu.py`** (unit-tested in
`agent/tests/test_bootmenu.py`, runs in any container via a mock input source):

```
decide(held, elapsed, cfg) -> "menu" | "boot:<entry>" | "wait"
```

- `held ⊇ combo`  → **"menu"** (open the picker; extra buttons are fine)
- `elapsed ≥ timeout` and menu not open → **"boot:<default>"** (auto-boot)
- otherwise → **"wait"** (keep polling, tick the countdown)

Try it:
```
python3 scripts/gose_bootmenu.py --self-test
python3 scripts/gose_bootmenu.py --mock-hold L1,R1      # -> menu
python3 scripts/gose_bootmenu.py --mock-hold "" --elapsed 6   # -> boot:rocknix
```

### On the real device — [needs hardware]
The container can't read `/dev/input`/GPIO at boot, so `read_buttons()` is a stub.
On the Odin 2 it becomes an **early-boot read** of the controller via
`python-evdev` (or GPIO), wired into one of:
- a **systemd unit** ordered before the GUI/EmulationStation target, or
- an **initramfs hook** (earliest, survives a broken rootfs — best for Recovery).

The unit runs the `decide()` loop for the timeout window, draws the menu on the
framebuffer/console when triggered, and execs the chosen action. Because the
logic is already tested, only the I/O glue is left for hardware bring-up.
