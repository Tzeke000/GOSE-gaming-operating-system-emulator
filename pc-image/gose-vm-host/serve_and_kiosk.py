import paramiko, time
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("127.0.0.1", port=2222, username="root", password="linux",
          look_for_keys=False, allow_agent=False, timeout=30)
def run(cmd):
    i, o, e = c.exec_command(cmd, timeout=25); return (o.read() + e.read()).decode()
sftp = c.open_sftp(); sftp.put(r"D:\gose-vm\gose_vm_server.py", "/userdata/gose-ui/gose_vm_server.py")
run("pkill -f gose_vm_server; sleep 1")
# foreground test to see any real error
print("test:", run("cd /userdata/gose-ui && timeout 3 python3 -u gose_vm_server.py 2>&1 | head -8").strip()[:400])
# start backgrounded with nohup
run("cd /userdata/gose-ui && nohup python3 -u gose_vm_server.py >/userdata/gose-ui/server.log 2>&1 & echo started")
time.sleep(2)
print("listening 8780:", run("(ss -ltn 2>/dev/null||netstat -ltn) | grep 8780 || echo NO").strip()[:120])
print("server.log:", run("cat /userdata/gose-ui/server.log 2>&1").strip()[:200])
print("curl status:", run("curl -s http://127.0.0.1:8780/status.json 2>&1 | head -c 200 || echo 'no curl'").strip()[:220])
# point kiosk at http for icons + live dials
run("pkill -f kiosk.py; sleep 1")
run("DISPLAY=:0 setsid python3 /userdata/gose-ui/kiosk.py "
    "http://127.0.0.1:8780/gose-home.html >/userdata/gose-ui/kiosk.log 2>&1 </dev/null &")
time.sleep(11)
print("kiosk:", (run("pgrep -af kiosk.py | grep -v grep | head -1").strip() or "NONE")[:110])
print("kiosk log:", run("tail -3 /userdata/gose-ui/kiosk.log").strip()[:300])
c.close()
