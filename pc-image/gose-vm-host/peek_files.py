import paramiko, time
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("127.0.0.1", port=2222, username="root", password="linux",
          look_for_keys=False, allow_agent=False, timeout=30)
def run(cmd, t=30):
    i, o, e = c.exec_command(cmd, timeout=t); return (o.read() + e.read()).decode()
# launch Files kiosk ON TOP of the running home (don't kill home — session loop owns it)
run("DISPLAY=:0 nohup python3 /userdata/gose-ui/kiosk.py "
    "http://127.0.0.1:8780/gose-files.html >/dev/null 2>&1 &")
time.sleep(6)
print("kiosks:", run("pgrep -af kiosk.py | grep -v grep | wc -l").strip())
c.close()
