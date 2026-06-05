import paramiko, time
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("127.0.0.1", port=2222, username="root", password="linux",
          look_for_keys=False, allow_agent=False, timeout=30)
def run(cmd, t=25):
    i, o, e = c.exec_command(cmd, timeout=t); return (o.read() + e.read()).decode()
sftp = c.open_sftp()
pushes = [
    (r"D:\GOSE-gaming-operating-system-emulator\gui\mockup\gose-home.html", "/userdata/gose-ui/gose-home.html"),
    (r"D:\GOSE-gaming-operating-system-emulator\gui\mockup\gose-library.html", "/userdata/gose-ui/gose-library.html"),
    (r"D:\gose-vm\gose_vm_server.py", "/userdata/gose-ui/gose_vm_server.py"),
]
for src, dst in pushes:
    with open(src, "rb") as f:
        data = f.read().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    with sftp.open(dst, "wb") as rf:
        rf.write(data)
    print("pushed", dst)

# restart the UI server (code changed: new /games.json route)
run("pkill -f gose_vm_server.py; sleep 1")
run("cd /userdata/gose-ui && nohup python3 -u gose_vm_server.py >/userdata/gose-ui/server.log 2>&1 &")
time.sleep(2)
print("server:", (run("pgrep -af gose_vm_server.py | grep -v grep | head -1").strip() or "NONE")[:90])

# verify endpoints from inside the guest
print("games.json:", run("curl -s http://127.0.0.1:8780/games.json | head -c 400", t=15).strip()[:400])

# reload the kiosk so the new home/library load (session loop relaunches it)
run("pkill -f kiosk.py; sleep 3")
time.sleep(8)
print("kiosk:", (run("pgrep -af kiosk.py | grep -v grep | head -1").strip() or "NONE")[:110])
c.close()
