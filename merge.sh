#!/bin/bash
# merge.sh — auto-merge PRs labeled ready-to-merge

set -euo pipefail

APPROVED_PRS=$(gh pr list --label "ready-to-merge" --json number --jq '.[].number')

for PR in $APPROVED_PRS; do
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Merging PR #$PR"
  gh pr merge "$PR" --squash --auto
done
