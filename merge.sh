#!/bin/bash
# merge.sh — auto-merge PRs labeled ready-to-merge

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS_YML="${SCRIPT_DIR}/agents.yml"

# Load env vars if .env exists
[[ -f "${SCRIPT_DIR}/.env" ]] && source "${SCRIPT_DIR}/.env"

if [[ ! -f "$AGENTS_YML" ]]; then
  echo "ERROR: agents.yml not found at $AGENTS_YML" >&2
  exit 1
fi

REPO_PATH=$(grep 'repo_path:' "$AGENTS_YML" | head -1 | sed 's/.*repo_path: *"\(.*\)"/\1/')

cd "$REPO_PATH"

export GH_TOKEN="${CODER_GH_TOKEN:?CODER_GH_TOKEN is not set}"

APPROVED_PRS=$(gh pr list --label "ready-to-merge" --json number --jq '.[].number')

for PR in $APPROVED_PRS; do
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Merging PR #$PR"
  gh pr merge "$PR" --squash --auto
done
