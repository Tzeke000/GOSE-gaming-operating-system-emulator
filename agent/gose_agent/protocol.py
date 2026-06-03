"""Wire protocol: newline-delimited JSON. See docs/05-ai-control-protocol.md.

Pure stdlib so it can be imported anywhere (agent, client, tests).
"""
from __future__ import annotations

import json
from typing import Any, Dict

# Error codes (kept in sync with the protocol doc).
ERR_AUTH = "ERR_AUTH"
ERR_BADREQ = "ERR_BADREQ"
ERR_UNKNOWN_OP = "ERR_UNKNOWN_OP"
ERR_ARGS = "ERR_ARGS"
ERR_BACKEND = "ERR_BACKEND"
ERR_TIMEOUT = "ERR_TIMEOUT"
ERR_DENIED = "ERR_DENIED"


class AgentError(Exception):
    """Raised by capabilities/dispatch; carries a protocol error code."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def encode(obj: Dict[str, Any]) -> bytes:
    """Serialize a message to a single newline-terminated UTF-8 line."""
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def decode_line(line: str) -> Dict[str, Any]:
    """Parse one line into a dict, raising AgentError(ERR_BADREQ) on bad input."""
    line = line.strip()
    if not line:
        raise AgentError(ERR_BADREQ, "empty line")
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        raise AgentError(ERR_BADREQ, f"invalid json: {e}") from e
    if not isinstance(obj, dict):
        raise AgentError(ERR_BADREQ, "message must be a JSON object")
    return obj


def ok_response(req_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": req_id, "ok": True, "result": result}


def err_response(req_id: Any, code: str, message: str = "") -> Dict[str, Any]:
    return {"id": req_id, "ok": False, "error": message or code, "code": code}


def event(name: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {"event": name, "data": data}
