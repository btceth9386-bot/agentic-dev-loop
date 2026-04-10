#!/bin/bash
# rotate-role.sh — swap an agent's role in agents.yml
# Usage: ./scripts/rotate-role.sh <agent-name> <new-role>

set -euo pipefail

AGENT_NAME="${1:?Usage: $0 <agent-name> <new-role>}"
NEW_ROLE="${2:?Usage: $0 <agent-name> <new-role>}"
AGENTS_YML="$(dirname "$0")/../agents.yml"

python3 - "$AGENTS_YML" "$AGENT_NAME" "$NEW_ROLE" <<'PYEOF'
import sys, yaml
path, name, role = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path) as f:
    config = yaml.safe_load(f)
found = False
for agent in config["agents"]:
    if agent["name"] == name:
        agent["role"] = role
        found = True
        break
if not found:
    print(f"Agent '{name}' not found", file=sys.stderr)
    sys.exit(1)
with open(path, "w") as f:
    yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
print(f"✅ Agent '{name}' role set to '{role}'")
PYEOF
