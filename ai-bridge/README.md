# AI Bridge — Ava / Wren / Iris ↔ GOSE Agent  `[CUSTOM]` 🧱 blocked

This is the thin adapter that connects **your AI agents** to the device. It maps
their intents into `GoseClient` calls (`launch`, `tap`, `run`, `status`,
`screenshot`, ...) and streams device state/screens back to them.

```
Ava / Wren / Iris  ──their API──▶  ai-bridge  ──GoseClient──▶  GOSE Agent ──▶ Odin 2
   (your server)                  (this dir)   (JSON-lines)      (daemon)
```

## Status: blocked on the agent spec
The GOSE Agent + `gose_client` fully define and exercise **our** side — the device
is controllable today via the CLI/SDK. What's missing is **how Ava/Wren/Iris
expose themselves**:

- Transport: HTTP REST? WebSocket? gRPC? message queue?
- Auth: API key? OAuth? mTLS?
- Message format: function-calling/tool schema? freeform text + an LLM that emits
  GOSE ops? streaming?
- Direction: do the agents call us (webhook), or do we long-poll/subscribe them?

Drop that spec in `docs/` and this bridge becomes a small, well-defined adapter.

## What's here now
`bridge.py` — a runnable **reference adapter** showing the shape: it exposes a
minimal "intent" function (`handle_intent`) that translates structured intents
into GOSE Agent calls, plus a fake agent connector you replace with the real
Ava/Wren/Iris client. This lets us build and test the device side of the bridge
before the agent API is finalized.

## Two integration patterns (pick when the spec lands)
1. **Tool/function-calling** (recommended if the agents are LLM tool-callers):
   expose GOSE ops as tools; the agent picks `gose.launch{...}`, `gose.tap{...}`,
   `gose.run{...}`; the bridge executes and returns results + screenshots so the
   agent can "see" and continue.
2. **Intent translation**: the agent emits high-level intents ("play God of War on
   PSP", "wifi is broken, fix it"); the bridge decomposes them into op sequences.
