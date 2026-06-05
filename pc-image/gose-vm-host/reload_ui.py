import paramiko, time
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("127.0.0.1", port=2222, username="root", password="linux",
          look_for_keys=False, allow_agent=False, timeout=30)
def run(cmd):
    i, o, e = c.exec_command(cmd, timeout=25); return (o.read() + e.read()).decode()
sftp = c.open_sftp()
pushes = [(r"D:\GOSE-gaming-operating-system-emulator\gui\mockup\gose-home.html", "/userdata/gose-ui/gose-home.html"),
          (r"D:\gose-vm\kiosk.py", "/userdata/gose-ui/kiosk.py")]
for src, dst in pushes:
    with open(src, "rb") as f:
        data = f.read().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    with sftp.open(dst, "wb") as rf:
        rf.write(data)
print("pushed updated home + kiosk")
# reload: kill kiosk -> the gose-session loop relaunches it with new files
run("pkill -f kiosk.py; sleep 3")
time.sleep(9)
print("kiosk:", (run("pgrep -af kiosk.py | grep -v grep | head -1").strip() or "NONE")[:110])
c.close()
