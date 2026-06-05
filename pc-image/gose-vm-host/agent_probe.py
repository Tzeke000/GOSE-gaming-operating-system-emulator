import socket, json, glob

TOK = "***REMOVED-DEV-TOKEN***"

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
