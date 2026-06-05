import paramiko, time
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("127.0.0.1", port=2222, username="root", password="linux",
          look_for_keys=False, allow_agent=False, timeout=30)
def run(cmd):
    i, o, e = c.exec_command(cmd, timeout=30); return (o.read() + e.read()).decode()
sftp = c.open_sftp()
for fn in ("kiosk.py", "start-shell.sh", "gose_vm_server.py"):
    with open(r"D:\gose-vm\%s" % fn, "rb") as f:
        data = f.read().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    with sftp.open("/userdata/gose-ui/%s" % fn, "wb") as rf:
        rf.write(data)
run("chmod +x /userdata/gose-ui/start-shell.sh")
# stop the game + any old shell/kiosk
run("pkill -f emulatorlauncher; pkill -f retroarch; pkill -f kiosk.py; pkill -f start-shell.sh; sleep 2")
# ensure UI server up
run("pgrep -f gose_vm_server >/dev/null || (cd /userdata/gose-ui && nohup python3 -u gose_vm_server.py >>shell.log 2>&1 &)")
# start the stabilized shell watchdog
run("setsid sh /userdata/gose-ui/start-shell.sh </dev/null >/dev/null 2>&1 &")
time.sleep(12)
print("kiosk:", (run("pgrep -af kiosk.py | grep -v grep | head -1").strip() or "NONE")[:110])
print("watchdog:", (run("pgrep -af start-shell.sh | grep -v grep | head -1").strip() or "NONE")[:90])
c.close()
