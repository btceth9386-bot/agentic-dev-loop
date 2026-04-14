#!/bin/bash
# enable-agent-cooldown.sh — put an agent into cooldown (temporarily stop it)
# Usage: ./scripts/enable-agent-cooldown.sh <agent-name> [minutes]
# Default: 1440 minutes (24 hours). Use 0 to remove cooldown (same as disable-agent-cooldown.sh).

set -euo pipefail

AGENT_NAME="${1:?Usage: $0 <agent-name> [minutes]}"
MINUTES="${2:-1440}"

MARKER="/tmp/agent-${AGENT_NAME}.cooldown"

if [[ "$MINUTES" == "0" ]]; then
  rm -f "$MARKER"
  echo "✅ Agent '${AGENT_NAME}' re-enabled (cooldown removed)"
else
  echo "$(date +%s):${MINUTES}" > "$MARKER"
  echo "⏸️  Agent '${AGENT_NAME}' disabled for ${MINUTES} minutes"
  echo "   Marker: ${MARKER}"
  echo "   To remove cooldown: ./scripts/disable-agent-cooldown.sh ${AGENT_NAME}"
fi
