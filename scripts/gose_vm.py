#!/usr/bin/env python3
"""GOSE-PC VM launcher — run GOSE on a desktop as a virtual machine.

GOSE on PC is NOT a web wrapper and NOT an emulation of the ARM device image
(that would be slow). It is a separate **x86_64 GOSE image** (base: Batocera
x86_64 + the GOSE custom layer) run in a **QEMU** virtual machine with hardware
acceleration and a virtio GPU — a fast, faithful preview of the Odin 2 you can
use before the hardware arrives.

This module builds/launches the QEMU command. The command builder + accel
detection are pure and unit-tested (agent/tests/test_vm.py). Actually booting
needs the GOSE-PC image + a host QEMU install, so `--dry-run` prints the command
and real boot is [needs image]. Controller/keyboard passthrough mirrors the
input chooser (scripts/gose_input.py): keyboard/mouse by default, USB passthrough
for a physical controller.
"""
from __future__ import annotations
import argparse
import os
import shutil
import sys
from dataclasses import dataclass, field


def detect_accel(platform=None, has_kvm=None):
    """Best available hypervisor accelerator for the host (falls back to tcg)."""
    platform = platform if platform is not None else sys.platform
    if platform.startswith("linux"):
        if has_kvm is None:
            has_kvm = os.path.exists("/dev/kvm")
        return "kvm" if has_kvm else "tcg"
    if platform == "darwin":
        return "hvf"
    if platform.startswith("win"):
        return "whpx"
    return "tcg"


@dataclass
class VmConfig:
    image: str = "gose-pc-x86_64.img"
    memory: str = "6G"
    cpus: int = 4
    gpu: bool = True                       # virtio-gpu-gl accelerated display
    share_dir: str | None = None           # host folder shared as ROMs/saves (9p)
    usb_controllers: list = field(default_factory=list)  # ["046d:c21d", ...]
    headless: bool = False
    cpu: str | None = None                 # override CPU model; None = auto per accel


def build_qemu_cmd(cfg, accel):
    """Assemble the qemu-system-x86_64 argv for the GOSE-PC VM."""
    cmd = [
        "qemu-system-x86_64",
        "-name", "GOSE-PC",
        "-machine", f"q35,accel={accel}",
        # `-cpu host` passes modern host features (e.g. APX) that WHPX on Windows
        # can't virtualize -> "WHPX: Unexpected VP exit code 4". Allow an override;
        # default stays host for kvm/hvf, qemu64 for tcg.
        "-cpu", cfg.cpu or ("host" if accel != "tcg" else "qemu64"),
        "-smp", str(cfg.cpus),
        "-m", cfg.memory,
        "-drive", f"file={cfg.image},if=virtio,format=raw",
        # user-mode net so the GOSE agent is reachable over TCP (Wi-Fi-like).
        # host 8731 -> guest 8731: the agent binds 8731 by default (config.py;
        # GOSE_AGENT_PORT), which is also where Wren's MCP server
        # (D:\Wren\.mcp.json) connects. host 2222 -> guest 22 lets the GOSE layer
        # be pushed in over SSH on Windows (no Linux loop-mount).
        "-netdev", "user,id=net0,hostfwd=tcp::8731-:8731,hostfwd=tcp::2222-:22",
        "-device", "virtio-net-pci,netdev=net0",
        "-device", "intel-hda", "-device", "hda-output",
    ]
    if cfg.gpu and not cfg.headless:
        cmd += ["-device", "virtio-vga-gl", "-display", "gtk,gl=on"]
    elif cfg.headless:
        cmd += ["-display", "none"]
    else:
        cmd += ["-device", "virtio-vga", "-display", "gtk"]
    if cfg.share_dir:
        cmd += ["-fsdev", f"local,id=fs0,path={cfg.share_dir},security_model=mapped",
                "-device", "virtio-9p-pci,fsdev=fs0,mount_tag=gose-share"]
    if cfg.usb_controllers:
        cmd += ["-usb"]
        for ident in cfg.usb_controllers:
            vendor, _, product = ident.partition(":")
            cmd += ["-device", f"usb-host,vendorid=0x{vendor},productid=0x{product}"]
    return cmd


def _cli(argv=None):
    ap = argparse.ArgumentParser(description="Launch the GOSE-PC virtual machine.")
    ap.add_argument("--image", default="gose-pc-x86_64.img")
    ap.add_argument("--memory", default="6G")
    ap.add_argument("--cpus", type=int, default=4)
    ap.add_argument("--cpu", default=None,
                    help="QEMU CPU model override (e.g. qemu64 for Windows/WHPX; default: host on kvm/hvf)")
    ap.add_argument("--no-gpu", action="store_true")
    ap.add_argument("--share", default=None, help="host folder to share as gose-share (ROMs/saves)")
    ap.add_argument("--controller", action="append", default=[], metavar="VID:PID",
                    help="pass a USB controller through (repeatable)")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the QEMU command and exit")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        assert detect_accel("linux", has_kvm=True) == "kvm"
        assert detect_accel("linux", has_kvm=False) == "tcg"
        assert detect_accel("darwin") == "hvf" and detect_accel("win32") == "whpx"
        cmd = build_qemu_cmd(VmConfig(share_dir="/roms", usb_controllers=["046d:c21d"]), "kvm")
        assert "qemu-system-x86_64" == cmd[0] and "accel=kvm" in " ".join(cmd)
        assert "virtio-9p-pci,fsdev=fs0,mount_tag=gose-share" in cmd
        assert any("usb-host" in c for c in cmd)
        assert "virtio-vga-gl" in build_qemu_cmd(VmConfig(), "kvm")
        assert "none" in build_qemu_cmd(VmConfig(headless=True), "tcg")
        print("self-test OK")
        return 0

    cfg = VmConfig(image=a.image, memory=a.memory, cpus=a.cpus, gpu=not a.no_gpu,
                   share_dir=a.share, usb_controllers=a.controller, headless=a.headless, cpu=a.cpu)
    accel = detect_accel()
    cmd = build_qemu_cmd(cfg, accel)

    if a.dry_run:
        print(" ".join(cmd))
        return 0
    if not os.path.exists(cfg.image):
        print(f"GOSE-PC image not found: {cfg.image}\n"
              f"Build/download it first (see docs/11-pc-app-and-input.md), or use --dry-run.",
              file=sys.stderr)
        return 2
    if shutil.which("qemu-system-x86_64") is None:
        print("qemu-system-x86_64 not found on PATH. Install QEMU, or use --dry-run.", file=sys.stderr)
        return 3
    os.execvp(cmd[0], cmd)  # [needs image] — replaces this process with the VM


if __name__ == "__main__":
    sys.exit(_cli())
