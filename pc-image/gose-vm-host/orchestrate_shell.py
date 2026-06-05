import paramiko
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("127.0.0.1", port=2222, username="root", password="linux",
          look_for_keys=False, allow_agent=False, timeout=30)
def run(cmd):
    i, o, e = c.exec_command(cmd, timeout=40); return (o.read() + e.read()).decode()
sftp = c.open_sftp()
# push start-shell.sh CR-stripped (CRLF breaks /bin/sh)
with open(r"D:\gose-vm\start-shell.sh", "rb") as f:
    sh = f.read().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
with sftp.open("/userdata/gose-ui/start-shell.sh", "wb") as rf:
    rf.write(sh)
run("chmod +x /userdata/gose-ui/start-shell.sh")
# also push the latest UI server + kiosk in case
for fn in ("gose_vm_server.py", "kiosk.py"):
    sftp.put(r"D:\gose-vm\%s" % fn, "/userdata/gose-ui/%s" % fn)
# patch custom_service to launch the shell at boot
cs = run("cat /userdata/system/services/custom_service")
print("already patched:", "start-shell.sh" in cs)
if "start-shell.sh" not in cs:
    line = ("    [ -f /userdata/gose-ui/start-shell.sh ] && setsid sh "
            "/userdata/gose-ui/start-shell.sh </dev/null >/dev/null 2>&1 &\n")
    new = cs.replace("start)\n", "start)\n" + line, 1).replace("\r\n", "\n").replace("\r", "\n")
    with sftp.open("/userdata/system/services/custom_service", "w") as rf:
        rf.write(new)
    run("chmod +x /userdata/system/services/custom_service")
print("grep:", run("grep -n start-shell /userdata/system/services/custom_service").strip()[:160])
print("syntax:", run("sh -n /userdata/system/services/custom_service && echo OK || echo FAIL").strip())
print("overlay:", run("batocera-save-overlay 2>&1 | tail -1").strip())
c.close()
