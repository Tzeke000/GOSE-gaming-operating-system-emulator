import paramiko, time
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("127.0.0.1", port=2222, username="root", password="linux",
          look_for_keys=False, allow_agent=False, timeout=30)
def run(cmd, t=30):
    i, o, e = c.exec_command(cmd, timeout=t); return (o.read() + e.read()).decode()
sftp = c.open_sftp()
M = r"D:\GOSE-gaming-operating-system-emulator\gui\mockup"
pushes = [
    (r"D:\gose-vm\gose_vm_server.py", "/userdata/gose-ui/gose_vm_server.py"),
    (r"D:\gose-vm\kiosk.py",          "/userdata/gose-ui/kiosk.py"),
    (M + r"\gose-files.html",         "/userdata/gose-ui/gose-files.html"),
]
for src, dst in pushes:
    with open(src, "rb") as f:
        data = f.read().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    with sftp.open(dst, "wb") as rf:
        rf.write(data)
    print("pushed", dst)

# restart the UI server (threaded now + new caching + /fs/sizes route)
run("pkill -f gose_vm_server.py; sleep 1")
run("cd /userdata/gose-ui && nohup python3 -u gose_vm_server.py >/userdata/gose-ui/server.log 2>&1 &")
time.sleep(2)
print("server:", (run("pgrep -af gose_vm_server.py | grep -v grep | head -1").strip() or "NONE")[:90])

# --- verify: speed of /fs/list (should be near-instant now, no du) ---
print("\n[fs/list /userdata timing]")
print(run(r"curl -s -o /dev/null -w 'time=%{time_total}s\n' 'http://127.0.0.1:8780/fs/list?path=/userdata'", t=30).strip())
print("[fs/list returns dirs with bytes:null]")
print(run(r"curl -s 'http://127.0.0.1:8780/fs/list?path=/userdata' | head -c 300", t=15).strip()[:300])
print("\n[fs/sizes (the lazy du) timing]")
print(run(r"curl -s -o /dev/null -w 'time=%{time_total}s\n' 'http://127.0.0.1:8780/fs/sizes?path=/userdata'", t=90).strip())

# --- verify: cache headers (static cached, html/json no-store) ---
print("\n[cache-control: themes.css]")
print(run(r"curl -s -D - -o /dev/null http://127.0.0.1:8780/assets/themes.css | grep -i cache", t=10).strip())
print("[cache-control: gose-home.html]")
print(run(r"curl -s -D - -o /dev/null http://127.0.0.1:8780/gose-home.html | grep -i cache", t=10).strip())
print("[cache-control: status.json]")
print(run(r"curl -s -D - -o /dev/null http://127.0.0.1:8780/status.json | grep -i cache", t=10).strip())

# clear WebKit cache so the new no-store/cache policy + kiosk bg take cleanly
run("rm -rf /userdata/system/.cache/kiosk.py 2>/dev/null; rm -rf /userdata/system/.config/*/WebKitCache 2>/dev/null; true")

# reload the kiosk so the dark-background fix takes effect (session loop relaunches it)
run("pkill -f kiosk.py; sleep 3")
time.sleep(8)
print("\nkiosk:", (run("pgrep -af kiosk.py | grep -v grep | head -1").strip() or "NONE")[:110])
c.close()
print("\nDONE")
