#!/bin/bash
# rotate-role.sh — swap an agent's role in agents.yml
# Usage: ./scripts/rotate-role.sh <agent-name> <new-role>

set -euo pipefail

AGENT_NAME="${1:?Usage: $0 <agent-name> <new-role>}"
NEW_ROLE="${2:?Usage: $0 <agent-name> <new-role>}"
AGENTS_YML="$(dirname "$0")/../agents.yml"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${SCRIPT_DIR}/.venv/bin/python3"
[[ -x "$PYTHON" ]] || PYTHON="python3"

$PYTHON - "$AGENTS_YML" "$AGENT_NAME" "$NEW_ROLE" <<'PYEOF'
import sys, re

path, name, role = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path) as f:
    content = f.read()

# Match "- name: <agent>\n    role: <old>" pattern and replace role value
pattern = rf'(- name: {re.escape(name)}\n\s+role: )\S+'
if not re.search(pattern, content):
    # Also try "name:" not at start of block (indented differently)
    pattern = rf'(name: {re.escape(name)}\n\s+role: )\S+'
if not re.search(pattern, content):
    print(f"Agent '{name}' not found", file=sys.stderr)
    sys.exit(1)

new_content = re.sub(pattern, rf'\g<1>{role}', content, count=1)
with open(path, "w") as f:
    f.write(new_content)
print(f"✅ Agent '{name}' role set to '{role}'")
PYEOF
