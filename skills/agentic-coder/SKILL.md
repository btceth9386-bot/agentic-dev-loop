---
name: agentic-coder
description: >
  Use this skill when the user says "you are a coder" or when tasked with implementing
  a GitHub Issue autonomously in a pipeline context. This skill guides the agent through
  reading the issue spec, implementing the changes, writing tests, committing, pushing,
  and opening a pull request — all without human interaction. Activate when working
  inside an agentic pipeline worktree where ISSUE.md and AGENTS.md are present.
---

# Agentic Coder Skill

You are a coding agent operating inside an automated pipeline. Your job is to implement
the GitHub Issue described in `ISSUE.md`, then commit, push, and open a PR — all
autonomously. Do not ask for confirmation. Exit 0 on success, non-zero on failure.

## Step 1 — Read context

1. Read `AGENTS.md` for project conventions, architecture, and coding guidelines.
2. Read `ISSUE.md` for the issue number, title, body, and any comments.
   - Note the issue number — you will need it for the PR title.
3. If a `## Pull Request` section exists in `ISSUE.md`, read it for prior review feedback
   that must be addressed in this implementation.

## Step 2 — Understand the codebase

1. Explore the repository structure to understand the existing patterns.
2. Identify files likely to be affected by the issue.
3. Check existing tests to understand the testing conventions used.

## Step 3 — Check for existing PR and review feedback

If `ISSUE.md` contains a `## Pull Request` section:
- A PR already exists for this branch. You are on a **retry** — do not create a new PR.
- Read the `## Review Feedback (changes requested)` section carefully.
- **Always check the PR's merge status**, even if the local branch looks clean:
  ```bash
  gh pr view <pr_number> --json mergeable --jq '.mergeable'
  ```
  If the result is `CONFLICTING`, you must rebase onto `origin/main`:
  ```bash
  git fetch origin
  GIT_EDITOR=true GIT_TERMINAL_PROMPT=0 git rebase origin/main
  ```
  Resolve conflicts, `git add` resolved files, then `git rebase --continue`.
  Push with `git push --force-with-lease`.
- If review feedback mentions code issues, fix them.
- After addressing all feedback, commit, push (`--force-with-lease` after rebase), and verify:
  ```bash
  sleep 5  # wait for GitHub to recompute
  gh pr view <pr_number> --json mergeable --jq '.mergeable'
  ```
  If still `CONFLICTING`, fetch and rebase again — `origin/main` may have moved since your last fetch.

If no `## Pull Request` section exists, this is a fresh implementation — proceed to Step 4.

## Step 4 — Plan before coding

Before writing any code, briefly plan:
- What files need to change?
- What is the minimal change that satisfies the issue?
- What tests need to be added or updated?

Prefer minimal, focused changes. Do not refactor unrelated code.

## Step 5 — Implement

1. Make the changes required by the issue.
2. Follow the conventions in `AGENTS.md` strictly (naming, structure, patterns).
3. Handle edge cases and errors explicitly.
4. Do not leave debug code, commented-out blocks, or TODOs.

## Step 6 — Write tests

1. Add or update tests to cover the new behaviour.
2. Follow the existing test conventions (framework, file location, naming).
3. Run the test suite and confirm all tests pass before committing.
   - If tests cannot be run (missing deps, env), note it in the PR body.

## Step 7 — Commit

Follow the `conventional-commit` skill conventions. Each commit should be a single,
stable change. Determine the type from the issue:

| Issue type | Commit type |
|------------|-------------|
| New feature / enhancement | `feat` |
| Bug fix | `fix` |
| Docs only | `docs` |
| Tests only | `test` |
| Refactor | `refactor` |

Infer `<scope>` from the primary module or directory changed (e.g. `dispatcher`, `config`, `workspace`).

```
<type>(<scope>): <subject in imperative mood>
```

Example:
```
feat(dispatcher): add per-agent env map for credential isolation
```

Do not include the issue number in the commit message — it belongs in the PR title.

## Step 8 — Push and open PR

1. If the branch has merge conflicts with `origin/main`, rebase first:
   ```bash
   git fetch origin
   GIT_EDITOR=true GIT_TERMINAL_PROMPT=0 git rebase origin/main
   ```
   - Resolve any conflicts, then:
   ```bash
   git add <resolved files>
   GIT_EDITOR=true GIT_TERMINAL_PROMPT=0 git rebase --continue
   ```
   - Never use `git merge` — always rebase.
   - After rebase, you must use `git push --force-with-lease` (rebase rewrites history).

2. Push the branch to origin. Use `--force-with-lease` if you rebased.

3. **If a PR already exists** (retry case from Step 3): do NOT create a new PR. Just push — the existing PR updates automatically. Exit 0.
4. **If no PR exists** (fresh implementation), create the PR:
   - **Do NOT use `--draft`** — create a regular (ready for review) PR
   - Title: `feat(<scope>): <short description> (#<issue_number>)` or `fix(...)` for bugs
   - Example: `feat(dispatcher): add per-agent env map (#42)`
   - Body: use `resolve #<issue_number>` (feature) or `fix #<issue_number>` (bug)
   - Also include: what changed, how to test, any known limitations

## Rules

- **Never** push directly to `main`.
- **Never** modify `ISSUE.md`, `AGENTS.md`, or pipeline config files (`agents.yml`, `dispatcher.py`).
- If the issue is ambiguous, make a reasonable assumption and document it in the PR body.
- Exit 0 only after the PR is successfully created. Exit non-zero on any unrecoverable error.
