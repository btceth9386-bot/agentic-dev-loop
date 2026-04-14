#!/bin/bash
# merge.sh — auto-merge PRs for issues labeled ready-to-merge

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

ISSUE_NUMBERS=$(gh issue list --label "ready-to-merge" --json number --jq '.[].number')

for ISSUE in $ISSUE_NUMBERS; do
  PR=$(gh pr list --head "agent/issue-${ISSUE}" --json number --jq '.[0].number')
  if [[ -z "$PR" || "$PR" == "null" ]]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) No PR found for issue #${ISSUE}, skipping"
    continue
  fi

  # Remove worktree before merge so --delete-branch can succeed
  WORKSPACE_BASE=$(grep 'workspace_base:' "$AGENTS_YML" | head -1 | sed 's/.*workspace_base: *"\(.*\)"/\1/')
  WORKSPACE="${WORKSPACE_BASE/#\~/$HOME}/issue-${ISSUE}"
  if [[ -d "$WORKSPACE" ]]; then
    git worktree remove "$WORKSPACE" --force 2>/dev/null || true
  fi

  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Merging PR #${PR} for issue #${ISSUE}"
  if gh pr merge "$PR" --squash --delete-branch; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) PR #${PR} merged successfully"
    # Clean up local branch if it still exists
    git branch -D "agent/issue-${ISSUE}" 2>/dev/null || true
  else
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) PR #${PR} merge failed (conflicts?), skipping"
  fi
done
