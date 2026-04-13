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

TOKEN_MAP = {
    "coding": "${CODER_GH_TOKEN}",
    "review": "${REVIEWER_GH_TOKEN}",
}

# Find the agent block
pattern = rf'(- name: {re.escape(name)}\n\s+role: )\S+'
if not re.search(pattern, content):
    pattern = rf'(name: {re.escape(name)}\n\s+role: )\S+'
if not re.search(pattern, content):
    print(f"Agent '{name}' not found", file=sys.stderr)
    sys.exit(1)

# Replace role
content = re.sub(pattern, rf'\g<1>{role}', content, count=1)

# Replace GH_TOKEN in the same agent block
if role in TOKEN_MAP:
    # Match GH_TOKEN line that follows "- name: <agent>" within ~6 lines
    token_pattern = rf'(- name: {re.escape(name)}\n(?:.*\n){{1,6}}?\s+GH_TOKEN: )"[^"]*"'
    if re.search(token_pattern, content):
        content = re.sub(token_pattern, rf'\g<1>"{TOKEN_MAP[role]}"', content, count=1)

with open(path, "w") as f:
    f.write(content)
print(f"✅ Agent '{name}' role set to '{role}', GH_TOKEN set to {TOKEN_MAP.get(role, 'unchanged')}")
PYEOF
