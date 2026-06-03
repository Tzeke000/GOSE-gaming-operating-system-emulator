"""Run the GOSE Agent:  python3 -m gose_agent  (from the agent/ directory).

Config via env (GOSE_AGENT_*) or a JSON file (GOSE_AGENT_CONFIG). See config.py.
"""
from __future__ import annotations

from .config import AgentConfig
from .server import AgentServer


def main():
    cfg = AgentConfig.load()
    AgentServer(cfg).run()


if __name__ == "__main__":
    main()
