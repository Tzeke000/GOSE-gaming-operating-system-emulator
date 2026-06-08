import socket, json, glob, os

# SECURITY: never hardcode the token. Read it from env, else the per-install token
# file the agent uses (this probe runs in-guest, so the file is local).
def _token():
    t = (os.environ.get("GOSE_AGENT_TOKEN") or "").strip()
    if t:
        return t
    try:
        return open(os.environ.get("GOSE_TOKEN_FILE", "/userdata/system/gose/token")).read().strip()
    except OSError:
        return ""

TOK = _token()

def call(args):
    s = socket.create_connection(("127.0.0.1", 8731), 4); s.settimeout(4)
    req = dict(id=1, op="system.status", args={})
    req.update(args)
    s.sendall((json.dumps(req) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        d = s.recv(65536)
        if not d:
            break
        buf += d
    s.close()
    return buf.decode().strip()[:500]

print("WITH token:", call({"token": TOK}))
print("NO token:  ", call({}))

# what token does the running agent actually have?
for pid in ["1432"]:
    try:
        env = open("/proc/%s/environ" % pid, "rb").read().split(b"\x00")
        print("AGENT ENV:", [e.decode() for e in env if b"GOSE" in e])
    except Exception as ex:
        print("env err", ex)
print("token files:", glob.glob("/userdata/**/agent.token", recursive=True))
