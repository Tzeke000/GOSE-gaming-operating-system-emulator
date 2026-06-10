"""Confinement for the AI agent's shell (``system.run``).

Why this exists
---------------
``system.run`` hands an admin-tier AI a root shell in the VM. The vm_server has
its own OS-protection guard, but the *agent's* path had none. This module
confines that path so a (possibly over-permissioned or buggy) AI shell can do
normal read/diagnostic work but CANNOT read the auth tokens or destroy the OS,
while the owner's dev path can opt out.

Mechanism (verified live on the target kernel 6.18.16, Batocera)
----------------------------------------------------------------
The plan was reuse-first (landrun / bubblewrap). Live probing showed:

* **Landlock is UNAVAILABLE** — ``landlock_create_ruleset(NULL,0,VERSION)``
  returns ``ENOSYS`` and ``/sys/kernel/security`` is empty. The kernel was built
  without ``CONFIG_SECURITY_LANDLOCK``, so a static ``landrun`` binary would fail
  identically. landrun is therefore impossible here, not merely missing.
* ``bwrap`` / ``firejail`` / ``unshare`` / ``nsenter`` are **not installed.**
* **Mount + user namespaces DO work** (``unshare(CLONE_NEWNS)`` succeeds;
  ``max_user_namespaces`` > 0), CPython is full, and the agent runs as root with
  ``CAP_SYS_ADMIN``.

So we hand-roll a bubblewrap-equivalent jail using the same kernel primitives
bubblewrap uses: a private **mount namespace** in which we

1. bind an inaccessible ``0o000`` file over each protected token path (the real
   file is no longer reachable in this namespace), and
2. bind-remount the protected OS directories **read-only**,

then **drop all capabilities** (incl. ``CAP_DAC_READ_SEARCH``,
``CAP_DAC_OVERRIDE``, ``CAP_SYS_ADMIN``) and set ``NO_NEW_PRIVS`` before exec.
Dropping ``CAP_SYS_ADMIN`` is what makes it a real jail and not theatre: the
confined shell, though still uid 0, can no longer ``umount`` the token shadow or
remount the OS read-write to undo the policy, and ``NO_NEW_PRIVS`` blocks
regaining privilege via setuid binaries. Dropping the DAC caps means even uid 0
is subject to the ``0o000`` mode on the shadow file -> ``EACCES``.

Honesty note: this is a genuine kernel-enforced mount/capability jail for the
common threat (a shell that reads files / runs tools), short of a full
``pivot_root`` rebuild of the root filesystem. A deny-list guard
(:func:`guard_command`) backs it up and is the *only* layer if namespace setup
degrades (e.g. inside an unprivileged container). The guard is honestly a
heuristic backstop, not the jail.
"""
from __future__ import annotations

import ctypes
import os
import re
import sys
from typing import List

# ---------------------------------------------------------------------------
# Policy: the critical invariant. Kept here so system.py and the tests share it.
# ---------------------------------------------------------------------------
TOKEN_PATHS: List[str] = [
    "/userdata/system/gose/token",
    "/userdata/system/gose/ai_tokens.json",
    "/userdata/system/gose/ai_grants.json",
]
# Writable on this box (root '/' is a rw overlay, not a RO squashfs), so a
# read-only bind-remount is genuinely load-bearing here.
RO_PATHS: List[str] = [
    "/usr", "/etc", "/bin", "/sbin", "/lib", "/lib64", "/boot",
    "/userdata/gose-ui",            # the GOSE shell/UI dir
    "/userdata/system/gose/agent",  # the agent's own code
    "/userdata/system/.ssh",        # root's SSH dir (root home == /userdata/system):
                                    # writing authorized_keys here grants root SSH, so an
                                    # admin-token system.run could escalate to root and
                                    # clobber everything above. RO-bind it so only an
                                    # out-of-band holder of the existing key (dev) gets in.
]
_SHADOW_FILE = "/tmp/.gose_sandbox_blocked"


# ---------------------------------------------------------------------------
# Deny-list backstop (heuristic — NOT the jail). Unit-testable without a VM.
# ---------------------------------------------------------------------------
_TOKEN_RE = re.compile(
    r"/userdata/system/gose/(?:token|ai_tokens\.json|ai_grants\.json)(?![\w./-])"
)
_DESTRUCTIVE = re.compile(
    r"(?:^|[\s;&|`(])(?:rm|rmdir|mv|dd|mkfs\S*|shred|truncate|chmod|chown|"
    r"chattr|ln|tee|cp|install|unlink|wipefs|sed\s+-i)(?=[\s])"
)
_REDIRECT_TO = re.compile(r">>?\s*([^\s;&|]+)")
_PROTECTED_WRITE = tuple(RO_PATHS)


class GuardDenied(Exception):
    """Raised by :func:`guard_command` when the deny-list rejects a command."""


def _hits_protected_write(cmd: str) -> bool:
    # A destructive verb anywhere AND a protected OS path mentioned -> refuse.
    if _DESTRUCTIVE.search(cmd) and any(p in cmd for p in _PROTECTED_WRITE):
        return True
    # Output redirection into a protected OS path -> refuse.
    for m in _REDIRECT_TO.finditer(cmd):
        tgt = m.group(1)
        if any(tgt == p or tgt.startswith(p + "/") for p in _PROTECTED_WRITE):
            return True
    return False


def guard_command(cmd: str) -> None:
    """Backstop deny-list. Raises :class:`GuardDenied` on a policy hit.

    This is a heuristic that runs in the agent process *before* spawning, so the
    critical invariant (no token reads, no OS clobber) holds even if the kernel
    namespace layer degrades. It is deliberately conservative about the token
    paths (any mention is refused) and about destructive writes to OS paths.
    """
    if _TOKEN_RE.search(cmd):
        raise GuardDenied("blocked: command references a protected token path")
    if _hits_protected_write(cmd):
        raise GuardDenied("blocked: destructive operation on a protected OS path")


# ---------------------------------------------------------------------------
# Command wrapping: build the argv that re-execs THIS file as a confiner.
# ---------------------------------------------------------------------------
def wrap_command(cmd: str) -> List[str]:
    """Return argv that runs *cmd* under confinement.

    ``[python, <this file>, "--confine", cmd]``. The child sets up the mount
    namespace + drops caps, then ``exec``s ``/bin/sh -c cmd``. Kept tiny and
    pure so tests can assert the shape without a VM.
    """
    helper = os.path.abspath(__file__)
    return [sys.executable, helper, "--confine", cmd]


# ---------------------------------------------------------------------------
# Kernel plumbing (child side; only runs when re-exec'd with --confine).
# ---------------------------------------------------------------------------
CLONE_NEWNS = 0x00020000
MS_RDONLY = 1
MS_REMOUNT = 32
MS_BIND = 4096
MS_PRIVATE = 1 << 18
MS_REC = 16384

PR_SET_NO_NEW_PRIVS = 38
PR_CAPBSET_DROP = 24
_NR_capset = 126                       # x86_64
_LINUX_CAP_V3 = 0x20080522


def _libc() -> ctypes.CDLL:
    lib = ctypes.CDLL(None, use_errno=True)
    lib.syscall.restype = ctypes.c_long
    return lib


def _mount(lib, src, tgt, fstype, flags, data=None):
    r = lib.mount(
        src.encode() if src else None,
        tgt.encode() if tgt else None,
        fstype.encode() if fstype else None,
        ctypes.c_ulong(flags),
        data.encode() if data else None,
    )
    if r != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e), tgt)


def _drop_all_caps(lib) -> None:
    """NO_NEW_PRIVS + empty bounding/effective/permitted/inheritable sets.

    Must run AFTER the mounts (they need CAP_SYS_ADMIN). After this the shell is
    uid 0 with no capabilities: cannot umount/remount, cannot DAC-override.
    """
    lib.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
    for cap in range(0, 64):
        lib.prctl(PR_CAPBSET_DROP, cap, 0, 0, 0)   # EINVAL past last cap — ignored

    class _Hdr(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]

    class _Data(ctypes.Structure):
        _fields_ = [("effective", ctypes.c_uint32),
                    ("permitted", ctypes.c_uint32),
                    ("inheritable", ctypes.c_uint32)]

    hdr = _Hdr(_LINUX_CAP_V3, 0)
    data = (_Data * 2)()               # two 32-bit words cover the 64-bit cap range
    lib.syscall(_NR_capset, ctypes.byref(hdr), ctypes.byref(data))


def apply_confinement() -> None:
    """Enter a private mount namespace, shadow tokens, RO-remount OS paths, drop
    caps. Raises OSError if the namespace itself can't be created (caller then
    falls back to deny-list-only + a degraded note)."""
    lib = _libc()

    # 0o000 shadow file — even uid 0 can't read it once DAC caps are dropped.
    fd = os.open(_SHADOW_FILE, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o000)
    os.close(fd)
    try:
        os.chmod(_SHADOW_FILE, 0o000)
    except OSError:
        pass

    if lib.unshare(CLONE_NEWNS) != 0:                       # may raise via errno
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e), "unshare(CLONE_NEWNS)")
    # Don't let our private mounts propagate back to the host namespace.
    _mount(lib, None, "/", None, MS_REC | MS_PRIVATE)

    for p in TOKEN_PATHS:                                   # hide the secrets
        if os.path.exists(p):
            try:
                _mount(lib, _SHADOW_FILE, p, None, MS_BIND)
            except OSError:
                pass
    for p in RO_PATHS:                                      # freeze the OS
        if os.path.exists(p):
            try:
                _mount(lib, p, p, None, MS_BIND)
                _mount(lib, None, p, None, MS_REMOUNT | MS_BIND | MS_RDONLY)
            except OSError:
                pass

    os.environ["HOME"] = "/tmp"
    os.environ["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    _drop_all_caps(lib)                                     # last: revoke power


def _confine_and_exec(cmd: str) -> None:
    try:
        apply_confinement()
    except OSError as e:
        # Honest degradation: namespace unavailable (e.g. unprivileged
        # container). The deny-list guard already ran in the parent; do what
        # hardening we still can and announce that the jail is degraded.
        sys.stderr.write(
            f"[sandbox] degraded: namespace setup failed ({e}); "
            f"deny-list guard still enforced\n")
        try:
            os.environ["HOME"] = "/tmp"
            os.environ["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
            _drop_all_caps(_libc())
        except Exception:                                  # noqa: BLE001
            pass
    os.execv("/bin/sh", ["/bin/sh", "-c", cmd])


def main(argv: List[str]) -> int:
    if len(argv) >= 3 and argv[1] == "--confine":
        _confine_and_exec(argv[2])
        return 127                                         # execv replaces us; unreachable
    sys.stderr.write("usage: sandbox.py --confine <cmd>\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
