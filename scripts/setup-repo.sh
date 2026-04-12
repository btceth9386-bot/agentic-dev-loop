#!/bin/bash
# setup-repo.sh — prepare a GitHub repo for use with agentic-dev-loop
# Usage: ./scripts/setup-repo.sh <owner/repo>
# Example: ./scripts/setup-repo.sh myorg/my-project

set -euo pipefail

REPO="${1:?Usage: $0 <owner/repo>}"

echo "Setting up $REPO for agentic-dev-loop..."

# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------
echo ""
echo "Creating labels..."

create_label() {
  local name="$1" color="$2" desc="$3"
  gh label create "$name" --color "$color" --description "$desc" --repo "$REPO" --force
}

create_label "todo"                   "0075ca" "Ready for pipeline pickup"
create_label "in-progress"            "e4e669" "Coding agent working"
create_label "pr-opened"              "d93f0b" "Awaiting review"
create_label "reviewing"              "f9d0c4" "Review agent working"
create_label "ready-to-merge"         "0e8a16" "Approved, queued for merge"
create_label "changes-requested"      "e11d48" "Review requested changes"
create_label "human-review-required"  "b60205" "Escalated — needs human"

echo "Labels created."

# ---------------------------------------------------------------------------
# Repo settings — squash merge only
# ---------------------------------------------------------------------------
echo ""
echo "Configuring repo merge settings (squash only)..."
gh repo edit "$REPO" --enable-squash-merge
# Note: gh cli does not support disabling merge-commit/rebase via flags.
# To restrict to squash-only, go to: Settings → General → Pull Requests

echo "Merge settings updated."

# ---------------------------------------------------------------------------
# Checklist
# ---------------------------------------------------------------------------
echo ""
echo "Done! Manual steps remaining:"
echo "  1. Add AGENTS.md to the repo root (use: kiro-cli /code summary)"
echo "  2. Add .gitignore with: ISSUE.md, .kiro/, .claude/, .codex/, .copilot/, .gemini/"
echo "  3. Update agents.yml: set repo_path to the local clone of $REPO"
