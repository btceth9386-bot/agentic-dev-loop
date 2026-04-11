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

## Step 3 — Plan before coding

Before writing any code, briefly plan:
- What files need to change?
- What is the minimal change that satisfies the issue?
- What tests need to be added or updated?

Prefer minimal, focused changes. Do not refactor unrelated code.

## Step 4 — Implement

1. Make the changes required by the issue.
2. Follow the conventions in `AGENTS.md` strictly (naming, structure, patterns).
3. Handle edge cases and errors explicitly.
4. Do not leave debug code, commented-out blocks, or TODOs.

## Step 5 — Write tests

1. Add or update tests to cover the new behaviour.
2. Follow the existing test conventions (framework, file location, naming).
3. Run the test suite and confirm all tests pass before committing.
   - If tests cannot be run (missing deps, env), note it in the PR body.

## Step 6 — Commit

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

## Step 7 — Push and open PR

1. Push the branch to origin.
2. Determine PR type from the issue:
   - New feature / enhancement → `feat`
   - Bug fix → `fix`

3. Create the PR with title:
   ```
   feat(<scope>): <short description> (#<issue_number>)
   ```
   or
   ```
   fix(<scope>): <short description> (#<issue_number>)
   ```
   Example: `feat(dispatcher): add per-agent env map (#42)`

4. In the PR body, use the appropriate closing keyword:
   - Feature: `resolve #<issue_number>`
   - Bug fix: `fix #<issue_number>`

5. Also include in the PR body:
   - What was changed and why
   - How to test it
   - Any known limitations or follow-up items

## Rules

- **Never** push directly to `main`.
- **Never** modify `ISSUE.md`, `AGENTS.md`, or pipeline config files (`agents.yml`, `dispatcher.py`).
- If the issue is ambiguous, make a reasonable assumption and document it in the PR body.
- Exit 0 only after the PR is successfully created. Exit non-zero on any unrecoverable error.
