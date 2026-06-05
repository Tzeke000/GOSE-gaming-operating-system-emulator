import paramiko
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("127.0.0.1", port=2222, username="root", password="linux",
          look_for_keys=False, allow_agent=False, timeout=30)
def run(cmd, t=60):
    i, o, e = c.exec_command(cmd, timeout=t); return (o.read() + e.read()).decode()

B = "http://127.0.0.1:8780"
pages = ["gose-home.html","gose-library.html","gose-files.html","gose-term.html","gose-ai.html",
         "gose-settings.html","gose-taskman.html","gose-store.html","gose-widgets.html",
         "gose-apps.html","gose-splice.html","gose-setup.html","gose-lock.html","gose-browser.html",
         "gose-notify.html"]
print("=== PAGES (HTTP status) ===")
for p in pages:
    code = run("curl -s -o /dev/null -w '%%{http_code}' %s/%s" % (B, p), t=15).strip()
    flag = "" if code == "200" else "   <<< NOT 200"
    print("  %-22s %s%s" % (p, code, flag))

print("\n=== JSON ENDPOINTS (ok + timing) ===")
eps = ["status.json","games.json","storage.json","procs.json","queue.json","net.json",
       "apps.json","store/catalog","fs/list?path=/userdata","fs/sizes?path=/userdata",
       "splice/videos"]
for e_ in eps:
    out = run(r"curl -s -o /tmp/r.json -w '%%{http_code} %%{time_total}s' '%s/%s'" % (B, e_), t=90).strip()
    ok = run(r"python3 -c \"import json;d=json.load(open('/tmp/r.json'));print(d.get('ok'))\" 2>/dev/null", t=10).strip()
    print("  %-30s http=%s ok=%s" % (e_, out, ok))

print("\n=== server.log tail (errors?) ===")
print(run("tail -n 15 /userdata/gose-ui/server.log 2>/dev/null", t=10))
c.close()
