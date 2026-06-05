import paramiko
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("127.0.0.1", port=2222, username="root", password="linux",
          look_for_keys=False, allow_agent=False, timeout=30)
def run(cmd):
    i, o, e = c.exec_command(cmd, timeout=40); return (o.read() + e.read()).decode()
sftp = c.open_sftp()
# push gose-session.sh (CR-stripped)
with open(r"D:\gose-vm\gose-session.sh", "rb") as f:
    s = f.read().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
with sftp.open("/userdata/gose-ui/gose-session.sh", "wb") as rf:
    rf.write(s)
run("chmod +x /userdata/gose-ui/gose-session.sh")
# back up ES-standalone before editing
run("[ -f /usr/bin/emulationstation-standalone.orig ] || cp /usr/bin/emulationstation-standalone /usr/bin/emulationstation-standalone.orig")
# swap the ES launch -> our GOSE session
es = run("cat /usr/bin/emulationstation-standalone")
old = "emulationstation ${GAMELAUNCHOPT} --exit-on-reboot-required --windowed ${CUSTOMESOPTIONS}"
new = "sh /userdata/gose-ui/gose-session.sh"
if old in es:
    with sftp.open("/usr/bin/emulationstation-standalone", "w") as rf:
        rf.write(es.replace(old, new))
    run("chmod +x /usr/bin/emulationstation-standalone")
    print("SWAPPED ES launch -> GOSE")
else:
    print("!! old launch line NOT found - aborted")
print("verify:", run("grep -n 'gose-session' /usr/bin/emulationstation-standalone").strip()[:140])
# remove the over-ES kiosk launch from custom_service (now redundant; avoid double-kiosk)
cs = run("cat /userdata/system/services/custom_service")
if "start-shell.sh" in cs:
    cs2 = "\n".join(l for l in cs.split("\n") if "start-shell.sh" not in l)
    with sftp.open("/userdata/system/services/custom_service", "w") as rf:
        rf.write(cs2)
    run("chmod +x /userdata/system/services/custom_service")
    print("removed start-shell from custom_service")
# stop current over-ES kiosk/watchdog
run("pkill -f start-shell.sh; pkill -f kiosk.py 2>/dev/null")
print("overlay:", run("batocera-save-overlay 2>&1 | tail -1").strip())
c.close()
