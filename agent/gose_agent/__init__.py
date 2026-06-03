"""GOSE Agent — the device-side daemon that lets an AI control the Odin 2.

Runs on the handheld under ROCKNIX/Batocera. Exposes input injection, shell,
game launching, system status, and screen capture over a small JSON-lines TCP
protocol (see docs/05-ai-control-protocol.md). Every capability has a real
backend and a mock backend, auto-selected, so the agent runs and is testable on
any Linux — including CI / a cloud container — without real hardware.
"""

__version__ = "0.1.0"
