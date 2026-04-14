#!/bin/bash
# enable-agent.sh — re-enable a disabled/cooldown agent
# Usage: ./scripts/enable-agent.sh <agent-name>

set -euo pipefail

AGENT_NAME="${1:?Usage: $0 <agent-name>}"

MARKER="/tmp/agent-${AGENT_NAME}.cooldown"

if [[ -f "$MARKER" ]]; then
  rm -f "$MARKER"
  echo "✅ Agent '${AGENT_NAME}' re-enabled (cooldown removed)"
else
  echo "ℹ️  Agent '${AGENT_NAME}' was not in cooldown"
fi
