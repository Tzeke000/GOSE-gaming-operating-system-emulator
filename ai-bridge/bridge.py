"""Reference AI bridge: AI-agent intents -> GOSE Agent calls.

This is a runnable skeleton. The `AgentConnector` is a stand-in for the real
AI-agent client — replace it once the agent's API spec is known (see README).
The GOSE side (`GoseClient`) is real and final.

Run a local demo against a mock agent:
    cd agent && GOSE_AGENT_FORCE_MOCK=1 python3 -m gose_agent &   # in one shell
    python3 ai-bridge/bridge.py --demo                            # in another
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent", "client"))
from gose_client import GoseClient  # noqa: E402


# ---- GOSE op exposed to the AI as a tool schema (pattern 1) -------------------
# When the connected AI agents support tool/function-calling, advertise these and route the
# tool calls straight into handle_intent().
GOSE_TOOLS = [
    {"name": "gose.launch", "args": {"system": "str", "game": "str"}},
    {"name": "gose.tap", "args": {"button": "str"}},
    {"name": "gose.combo", "args": {"buttons": "list[str]"}},
    {"name": "gose.axis", "args": {"axis": "str", "value": "float"}},
    {"name": "gose.run", "args": {"cmd": "str"}},
    {"name": "gose.status", "args": {}},
    {"name": "gose.screenshot", "args": {}},
    {"name": "gose.systems", "args": {}},
    {"name": "gose.list", "args": {"system": "str"}},
    {"name": "gose.stop", "args": {}},
    # game state (read from memory, no screenshots)
    {"name": "gose.state_profiles", "args": {}},
    {"name": "gose.state_attach", "args": {"profile": "str?"}},
    {"name": "gose.state_read", "args": {"profile": "str?"}},
]


def handle_intent(gose: GoseClient, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Translate one AI intent/tool-call into a GOSE Agent call."""
    a = args or {}
    if name == "gose.launch":   return gose.launch(a["system"], a["game"])
    if name == "gose.tap":      return gose.tap(a["button"])
    if name == "gose.combo":    return gose.combo(a["buttons"])
    if name == "gose.axis":     return gose.axis(a["axis"], float(a["value"]))
    if name == "gose.run":      return gose.run(a["cmd"])
    if name == "gose.status":   return gose.status()
    if name == "gose.screenshot": return gose.screenshot()
    if name == "gose.systems":  return gose.systems()
    if name == "gose.list":     return gose.list_games(a["system"])
    if name == "gose.stop":     return gose.stop()
    if name == "gose.state_profiles": return gose.profiles()
    if name == "gose.state_attach":   return gose.attach(a.get("profile"))
    if name == "gose.state_read":     return gose.read_state(a.get("profile"))
    raise ValueError(f"unknown intent '{name}'")


class AgentConnector:
    """STUB for the AI-agent side. Replace with the real client when the spec is known.

    Expected to yield (intent_name, args) tuples from the agent, and accept
    results back. Here we just replay a scripted demo.
    """

    def __init__(self, name: str = "agent"):
        self.name = name

    def demo_intents(self):
        yield ("gose.status", {})
        yield ("gose.systems", {})
        yield ("gose.launch", {"system": "psp", "game": "God of War"})
        yield ("gose.tap", {"button": "start"})
        yield ("gose.screenshot", {})


def run_demo(host: str, port: int, token: str | None):
    connector = AgentConnector("agent")
    with GoseClient(host, port, token=token) as gose:
        for name, args in connector.demo_intents():
            result = handle_intent(gose, name, args)
            preview = {k: (v[:24] + "…" if isinstance(v, str) and len(v) > 24 else v)
                       for k, v in result.items()}
            print(f"[{connector.name}] {name}({args}) -> {preview}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("GOSE_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("GOSE_PORT", "8731")))
    ap.add_argument("--token", default=os.environ.get("GOSE_TOKEN"))
    ap.add_argument("--demo", action="store_true", help="run the scripted demo")
    a = ap.parse_args()
    if a.demo:
        run_demo(a.host, a.port, a.token)
    else:
        print("Provide --demo, or wire AgentConnector to the real AI-agent API.")
        print("Available GOSE tools:", [t["name"] for t in GOSE_TOOLS])


if __name__ == "__main__":
    main()
