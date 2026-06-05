#!/usr/bin/env python3
"""Push the GOSE layer into a running Batocera VM over SSH (Windows-native
substitute for build-gose-pc.sh steps 4/6, which need a Linux loop-mount).

The VM is booted from a *stock* Batocera image; this drops the GOSE layer onto
the live /userdata partition and starts the agent on 5555 (forwarded to host
8731 by scripts/gose_vm.py). Batocera ships SSH with root/linux by default.

Usage:
    py -3.11 inject_gose_layer.py --recon         # connect + probe, no changes
    py -3.11 inject_gose_layer.py --push          # upload layer + start agent

Defaults target the gose_vm.py port-forward: host 127.0.0.1:2222 -> guest 22.
"""
from __future__ import annotations
import argparse
import os
import posixpath
import stat
import sys

import paramiko

REPO = r"D:\GOSE-gaming-operating-system-emulator"
LAYER = os.path.join(REPO, "pc-image", "gose-layer")
AGENT = os.path.join(REPO, "agent")

EXCLUDE_DIRS = {"tests", "__pycache__", ".git", ".pytest_cache"}


def connect(host, port, user, password):
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(host, port=port, username=user, password=password,
                look_for_keys=False, allow_agent=False, timeout=20)
    return cli


def run(cli, cmd):
    _in, out, err = cli.exec_command(cmd)
    rc = out.channel.recv_exit_status()
    return rc, out.read().decode(errors="replace"), err.read().decode(errors="replace")


def recon(cli):
    for label, cmd in [
        ("whoami", "whoami"),
        ("uname", "uname -a"),
        ("python3", "python3 --version 2>&1 || echo NO_PYTHON3"),
        ("userdata", "ls -ld /userdata /userdata/system 2>&1"),
        ("batocera.conf", "test -f /userdata/system/batocera.conf && echo present || echo absent"),
        ("custom.sh", "test -f /userdata/system/custom.sh && echo present || echo absent"),
        ("free space", "df -h /userdata | tail -1"),
    ]:
        rc, o, e = run(cli, cmd)
        print(f"  [{label}] rc={rc} {o.strip() or e.strip()}")


def sftp_mkdirs(sftp, remote_dir):
    parts = remote_dir.strip("/").split("/")
    cur = ""
    for p in parts:
        cur += "/" + p
        try:
            sftp.stat(cur)
        except IOError:
            sftp.mkdir(cur)


def upload_tree(sftp, local_dir, remote_dir):
    sftp_mkdirs(sftp, remote_dir)
    for name in os.listdir(local_dir):
        lp = os.path.join(local_dir, name)
        rp = posixpath.join(remote_dir, name)
        if os.path.isdir(lp):
            if name in EXCLUDE_DIRS:
                continue
            upload_tree(sftp, lp, rp)
        else:
            sftp.put(lp, rp)


def push(cli):
    sftp = cli.open_sftp()
    # 1. custom.sh (agent autostart) + make executable. Normalize CRLF->LF: a
    #    Windows-edited script with \r breaks /bin/sh ("word unexpected (expecting in)").
    print("  -> /userdata/system/custom.sh")
    sftp_mkdirs(sftp, "/userdata/system")
    with open(os.path.join(LAYER, "system", "custom.sh"), "rb") as f:
        sh = f.read().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    with sftp.open("/userdata/system/custom.sh", "wb") as rf:
        rf.write(sh)
    run(cli, "chmod +x /userdata/system/custom.sh")
    # 2. the agent itself
    print("  -> /userdata/system/gose/agent/ (recursive)")
    upload_tree(sftp, AGENT, "/userdata/system/gose/agent")
    # 3. splash assets (best-effort)
    splash = os.path.join(LAYER, "splash")
    if os.path.isdir(splash):
        print("  -> /userdata/splash/")
        upload_tree(sftp, splash, "/userdata/splash")
    # 4. merge gose conf keys
    print("  -> append batocera.conf.gose")
    with open(os.path.join(LAYER, "system", "batocera.conf.gose"), "r", encoding="utf-8") as f:
        conf = f.read()
    run(cli, "touch /userdata/system/batocera.conf")
    # append via a temp file so we don't clobber the existing conf
    tmp = "/tmp/gose.conf.add"
    with sftp.open(tmp, "w") as rf:
        rf.write(conf)
    run(cli, f"cat {tmp} >> /userdata/system/batocera.conf")
    sftp.close()
    # 5. start the agent now (also runs at next boot via custom.sh)
    print("  -> starting gose agent on :8731")
    rc, o, e = run(cli, "/userdata/system/custom.sh start; sleep 1; "
                        "(pgrep -af gose_agent || echo NOT_RUNNING)")
    print(f"     rc={rc} {o.strip() or e.strip()}")
    # persist /userdata changes on Batocera's overlay if applicable
    run(cli, "batocera-save-overlay 2>/dev/null || true")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2222)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", default="linux")
    ap.add_argument("--recon", action="store_true")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args(argv)
    if not (a.recon or a.push):
        ap.error("pass --recon or --push")
    try:
        cli = connect(a.host, a.port, a.user, a.password)
    except Exception as ex:
        print(f"SSH connect failed to {a.host}:{a.port} as {a.user}: {ex}", file=sys.stderr)
        return 1
    try:
        if a.recon:
            print("== recon ==")
            recon(cli)
        if a.push:
            print("== push GOSE layer ==")
            push(cli)
            print("== recon after push ==")
            recon(cli)
    finally:
        cli.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
