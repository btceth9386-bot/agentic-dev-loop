#!/bin/bash
# disable-agent.sh — temporarily disable an agent by setting a long cooldown
# Usage: ./scripts/disable-agent.sh <agent-name> [minutes]
# Default: 1440 minutes (24 hours). Use 0 to re-enable (same as enable-agent-cooldown.sh).

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
  echo "   To re-enable: ./scripts/enable-agent-cooldown.sh ${AGENT_NAME}"
fi
