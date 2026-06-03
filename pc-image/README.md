# GOSE-PC image build

Builds the **GOSE-PC** virtual machine image = **Batocera x86_64** (base) + the
**GOSE layer** (`gose-layer/`), and packages an importable **`.ova`**. This is the
"download GOSE and run it on your PC" path (ADR-0013) — a fast, faithful x86_64
preview of the Odin 2, run in a VM. See `docs/11-pc-app-and-input.md`.

## Pieces
| File | What it does | Runs here? |
|------|--------------|-----------|
| `build-gose-pc.sh` | Orchestrator: download base → inject layer → emit `.img` + `.ova` | `--dry-run` ✓; real build needs network + root + qemu-img |
| `gose-layer/` | Files copied onto Batocera userdata (agent autostart, config, splash) | ✓ (committed) |
| `make_ova.py` | Pure OVF descriptor + `.ova` packaging (VirtualBox/VMware) | ✓ (unit-tested) |

## Build it
```bash
./build-gose-pc.sh --dry-run                    # preview every step, no side effects
BATOCERA_IMG_URL=<real-url> BATOCERA_SHA256=<sha> sudo ./build-gose-pc.sh
```
Pin a real Batocera x86_64 release via `BATOCERA_IMG_URL` + `BATOCERA_SHA256`
before a real build (the defaults are placeholders — `[verify]`). Tunables:
`GROW_TO`, `MEMORY_MB`, `CPUS`, `OUT_OVA`.

## Run it
```bash
python3 ../scripts/gose_vm.py --image build/gose-pc-x86_64.img --share ~/roms   # QEMU
# or import GOSE-PC.ova into VirtualBox/VMware: File > Import Appliance
```

## Status
- ✅ Layer, OVA packager (tested), and orchestrator (dry-run verified).
- ⬜ `[needs build]` a real run on a Linux host with a pinned Batocera release →
  publish `GOSE-PC.img` + `GOSE-PC.ova`.
- ⬜ `[next]` the Windows-like EmulationStation theme in `gose-layer/themes/gose/`.
