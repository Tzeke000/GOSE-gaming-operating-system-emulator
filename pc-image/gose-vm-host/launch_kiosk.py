import paramiko, time
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("127.0.0.1", port=2222, username="root", password="linux",
          look_for_keys=False, allow_agent=False, timeout=30)
def run(cmd):
    i, o, e = c.exec_command(cmd, timeout=25); return (o.read() + e.read()).decode()
run("rm -f /tmp/suspend.please")
xpid = run("pgrep -x X").strip().split()[0] if run("pgrep -x X").strip() else ""
xcmd = run("cat /proc/%s/cmdline" % xpid).replace("\x00", " ").strip() if xpid else ""
print("X pid:", xpid, "cmd:", xcmd[:160] or "NO_X")
disp, xauth = ":0", ""
parts = xcmd.split()
for i, p in enumerate(parts):
    if p == "-auth" and i + 1 < len(parts):
        xauth = parts[i + 1]
    if p.startswith(":") and len(p) <= 3:
        disp = p
if not xauth:
    for cand in ["/root/.Xauthority", "/etc/sdl2-on-x.auth"]:
        if run("test -f %s && echo Y || echo N" % cand).strip() == "Y":
            xauth = cand; break
print("DISPLAY=%r XAUTHORITY=%r" % (disp, xauth))
run("pkill -f kiosk.py; sleep 1")  # clear any old kiosk first
pre = "DISPLAY=%s " % disp + (("XAUTHORITY=%s " % xauth) if xauth else "")
run(pre + "setsid python3 /userdata/gose-ui/kiosk.py "
    "file:///userdata/gose-ui/gose-home.html >/userdata/gose-ui/kiosk.log 2>&1 </dev/null &")
time.sleep(10)
print("kiosk:", (run("pgrep -af kiosk.py | grep -v grep | head -1").strip() or "NONE")[:140])
print("log:", run("tail -5 /userdata/gose-ui/kiosk.log").strip()[:500])
c.close()
