#!/usr/bin/env bash
# SessionStart hook: prime each (ephemeral) web session with project orientation
# and a fast health check of the agent. Output is injected as session context.
# Must never fail the session — always exit 0.
set +e
cd "$(dirname "$0")/../.." 2>/dev/null || exit 0

echo "=== GOSE project primer (auto-loaded) ==="
echo "Read CLAUDE.md first; ROADMAP.md for live status; STRUCTURE.md for what-lives-where."
echo "Base distro decision: ROCKNIX first (stable on Odin 2), Batocera v42 spare."
echo "Dev branch: main"
echo

# Fast agent health check (pure stdlib, ~0.1s). Summarize only.
if command -v python3 >/dev/null 2>&1 && [ -d agent/tests ]; then
  out="$(cd agent && python3 -m unittest discover -s tests 2>&1)"
  if echo "$out" | grep -q "^OK"; then
    ran="$(echo "$out" | grep -oE 'Ran [0-9]+ tests' | head -1)"
    echo "Agent self-test: PASS ($ran)"
  else
    echo "Agent self-test: FAILED — investigate before building on it:"
    echo "$out" | tail -5
  fi
fi

echo
echo "Immediate next actions (see ROADMAP.md):"
echo " 1. Confirm Odin 2 variant + start with ROCKNIX."
echo " 2. Polish the GOSE-PC VM edition (controller-first; docs/27 is the input law)."
echo " 3. Steam packaging prep once the OS is polished (license audit: docs/19)."
exit 0
