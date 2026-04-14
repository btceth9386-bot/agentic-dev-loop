#!/bin/bash
# disable-agent-cooldown.sh — remove cooldown from an agent (re-enable it)
# Usage: ./scripts/disable-agent-cooldown.sh <agent-name>

set -euo pipefail

AGENT_NAME="${1:?Usage: $0 <agent-name>}"

MARKER="/tmp/agent-${AGENT_NAME}.cooldown"

if [[ -f "$MARKER" ]]; then
  rm -f "$MARKER"
  echo "✅ Agent '${AGENT_NAME}' re-enabled (cooldown removed)"
else
  echo "ℹ️  Agent '${AGENT_NAME}' was not in cooldown"
fi
