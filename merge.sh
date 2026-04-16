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

REPO_PATH=$(grep -v '^ *#' "$AGENTS_YML" | grep 'repo_path:' | head -1 | sed 's/.*repo_path: *"\(.*\)"/\1/')

cd "$REPO_PATH"

export GH_TOKEN="${CODER_GH_TOKEN:?CODER_GH_TOKEN is not set}"

ISSUE_NUMBERS=$(gh issue list --label "ready-to-merge" --json number --jq '.[].number' || true)

# Simple notification helper — best-effort, never fatal
_notify() {
  local msg="$1"
  if [[ -n "${DISCORD_WEBHOOK_URL:-}" ]]; then
    curl -s -o /dev/null -X POST -H "Content-Type: application/json" \
      -d "{\"content\": \"$msg\"}" "$DISCORD_WEBHOOK_URL" 2>/dev/null || true
  fi
}

for ISSUE in $ISSUE_NUMBERS; do
  PR=$(gh pr list --head "agent/issue-${ISSUE}" --json number --jq '.[0].number')
  if [[ -z "$PR" || "$PR" == "null" ]]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) No PR found for issue #${ISSUE}, skipping"
    continue
  fi

  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Merging PR #${PR} for issue #${ISSUE}"
  if gh pr merge "$PR" --squash; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) PR #${PR} merged successfully"
    _notify "🎉 PR #${PR} merged for issue #${ISSUE}"
    # Clean up worktree and local branch after successful merge
    WORKSPACE_BASE=$(grep -v '^ *#' "$AGENTS_YML" | grep 'workspace_base:' | head -1 | sed 's/.*workspace_base: *"\(.*\)"/\1/')
    WORKSPACE="${WORKSPACE_BASE/#\~/$HOME}/issue-${ISSUE}"
    if [[ -d "$WORKSPACE" ]]; then
      git worktree remove "$WORKSPACE" --force 2>/dev/null || true
    fi
    git branch -D "agent/issue-${ISSUE}" 2>/dev/null || true
  else
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) PR #${PR} merge failed, checking for conflicts"
    MERGEABLE=$(gh pr view "$PR" --json mergeable --jq '.mergeable' 2>/dev/null || echo "UNKNOWN")
    if [[ "$MERGEABLE" == "CONFLICTING" ]]; then
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) PR #${PR} has merge conflicts, sending back to coder"
      GH_TOKEN="${REVIEWER_GH_TOKEN:-$CODER_GH_TOKEN}" gh pr review "$PR" --request-changes --body "Merge conflicts with main after another PR was merged. Please rebase and resolve." 2>/dev/null || true
      gh issue edit "$ISSUE" --remove-label "ready-to-merge" --add-label "changes-requested" 2>/dev/null || true
      _notify "🔀 PR #${PR} has merge conflicts — changes requested on issue #${ISSUE}"
    else
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) PR #${PR} merge failed (mergeable=$MERGEABLE), skipping"
    fi
  fi
done
