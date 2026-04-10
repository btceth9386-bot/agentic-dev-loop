#!/bin/bash
# status.sh — print current pipeline state

set -euo pipefail

AGENTS_YML="$(dirname "$0")/../agents.yml"
STATE_BASE=$(python3 -c "import yaml; c=yaml.safe_load(open('$AGENTS_YML')); print(c['pipeline']['state_base'])")
STATE_BASE="${STATE_BASE/#\~/$HOME}"
CURRENT_DIR="$(dirname "$STATE_BASE")/current"

echo "=== Active Locks ==="
ls /tmp/agent-*.lock 2>/dev/null | while read -r lock; do
  content=$(cat "$lock")
  ts=$(echo "$content" | cut -d: -f1)
  issue=$(echo "$content" | cut -d: -f2)
  agent=$(basename "$lock" | sed 's/^agent-//' | sed "s/-${issue}\.lock$//")
  age=$(( $(date +%s) - ts ))
  echo "  $agent → issue #$issue (${age}s ago)"
done || echo "  (none)"

echo ""
echo "=== Current Issue States ==="
if [ -d "$CURRENT_DIR" ]; then
  ls "$CURRENT_DIR" 2>/dev/null | while read -r entry; do
    target=$(readlink "$CURRENT_DIR/$entry" 2>/dev/null || echo "broken symlink")
    state=$(basename "$target" .log | sed 's/^[0-9]*-//')
    echo "  $entry → $state"
  done
else
  echo "  (no state directory found)"
fi
