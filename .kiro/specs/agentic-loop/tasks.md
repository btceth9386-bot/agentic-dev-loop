# Implementation Plan: Agentic Loop

## Overview

Implement the agentic-loop pipeline as three artifacts: `dispatcher.py` (Python orchestrator), `agents.yml` (configuration), and `merge.sh` (auto-merge script). Tasks are ordered so each builds on the previous, starting with configuration and core modules, then wiring the main orchestration loop, and finishing with the merge script and observability helpers.

## Tasks

- [ ] 1. Scaffold project structure and configuration
  - [ ] 1.1 Create project directory structure and `agents.yml` with example config
    - Create `agentic-loop/` directory with `dispatcher.py`, `merge.sh`, `agents.yml`, `config/crontab.example`
    - Define the `agents.yml` schema with `pipeline`, `agents`, `roles`, and `notifications` sections per the data model
    - Include example agent entries (kiro-cli, claude, codex) and role definitions (coding, review) with `pickup_label`, `label_on_start`, `label_on_done`
    - Add YAML comment on `command` field noting users should include resume flags if desired
    - _Requirements: 3.1, 3.4_

  - [ ] 1.2 Implement `load_config()` — YAML parsing, env var expansion, and validation
    - Parse `agents.yml` using PyYAML
    - After parsing, recursively expand `${VAR_NAME}` patterns in all string values using `os.environ` (regex: `r'\$\{[A-Za-z_][A-Za-z0-9_]*\}'`). Raise a descriptive error if a referenced env var is not set. Leave strings without `${...}` patterns unchanged.
    - Validate required agent fields: `name`, `role`, `command`, `max_concurrent`
    - Validate optional agent fields: `cooldown_minutes`
    - Validate required role fields: `pickup_label`, `label_on_start`, `label_on_done`
    - Raise descriptive errors on missing fields or invalid YAML syntax
    - _Requirements: 3.1, 3.2, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12_

  - [ ] 1.3 Implement `validate_gitignore()` — .gitignore presence, entry checks, and AGENTS.md warning
    - Verify `.gitignore` exists at `repo_path`
    - Validate it contains all Required_Gitignore_Entries: `ISSUE.md`, `.kiro/`, `.claude/`, `.codex/`, `.copilot/`, `.gemini/`
    - Fail with descriptive error listing missing entries
    - Check if `AGENTS.md` exists at `repo_path`; if missing, log a warning "⚠️ AGENTS.md not found in repo_path — agent CLIs may lack project context." and continue (do not fail)
    - _Requirements: 3.13, 3.14, 3.15, 3.16, 3.17, 3.18_

  - [ ]* 1.4 Write unit tests for `load_config()` and `validate_gitignore()`
    - Test valid config loading, missing agent fields, missing role fields, invalid YAML
    - Test `${VAR_NAME}` expansion: env var set → value substituted, env var not set → descriptive error raised, strings without `${...}` → unchanged
    - Test .gitignore present with all entries, missing file, missing entries
    - Test AGENTS.md present (no warning), AGENTS.md missing (warning logged, no error raised)
    - _Requirements: 3.1–3.18_

- [ ] 2. Checkpoint — Ensure config module tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 3. Implement GitHub interaction module
  - [ ] 3.1 Implement `poll_issues()` — query GitHub for issues by label
    - Run `gh issue list --label <label> --json number,title,labels` from `repo_path`
    - Filter out issues carrying any state label other than the pickup label
    - _Requirements: 1.1, 1.2, 1.6_

  - [ ] 3.2 Implement `transition_label()` — atomic label swap
    - Use `gh issue edit <number> --remove-label <old> --add-label <new>` from `repo_path`
    - _Requirements: 1.5, 2.3_

  - [ ] 3.3 Implement `fetch_issue_context()` — retrieve issue details for ISSUE.md
    - Run `gh issue view <number> --json title,body,comments` from `repo_path`
    - Format output as markdown with issue number prominently included
    - _Requirements: 6.1, 6.3_

  - [ ] 3.4 Implement `post_assignment_comment()` — post agent assignment comment on GitHub issue
    - Run `gh issue comment <number> --body "<message>"` from `repo_path`
    - Format: `🤖 Assigned to **<agent_name>** (<role>) — attempt <N>` for normal assignments
    - Format: `🤖 Assigned to **<agent_name>** (<role>) — retry attempt <N>` for retry assignments
    - Accept `is_retry` flag to select the appropriate format
    - _Requirements: 7.6, 8.6, 9.6_

  - [ ] 3.5 Implement `fetch_pr_context()` — fetch PR details for ISSUE.md enrichment
    - Run `gh pr list --head agent/issue-<number> --json number,url,title --limit 1` from `repo_path` to check if a PR exists for the issue's branch
    - If no PR exists, return `None`
    - If a PR exists, run `gh pr view <pr_number> --json number,url,title,reviews,comments` from `repo_path`
    - Format output as markdown: PR number, URL, title, and review comments (with author and body)
    - _Requirements: 6.4, 6.6_

  - [ ]* 3.6 Write unit tests for GitHub interaction module
    - Mock `subprocess.run` calls to `gh` CLI
    - Test poll filtering, label transition command construction, context formatting
    - Test `post_assignment_comment()` command construction for normal and retry formats
    - Test `fetch_pr_context()`: PR exists (returns formatted markdown), no PR exists (returns None), PR with review comments
    - _Requirements: 1.1, 1.2, 1.5, 1.6, 2.3, 6.1, 6.3, 6.4, 6.6, 7.6, 8.6, 9.6_

- [ ] 4. Implement lockfile module
  - [ ] 4.1 Implement `acquire_lock()`, `release_lock()`, `get_active_locks()`, `count_active_locks()`
    - Create lockfiles at `/tmp/agent-<name>-<issue_number>.lock` with `<unix_timestamp>:<issue_number>`
    - Support multiple concurrent lockfiles per agent up to `max_concurrent`
    - Auto-release lockfiles older than 30 minutes (stale threshold)
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [ ]* 4.2 Write unit tests for lockfile module
    - Test acquire, release, stale detection, multi-lock counting
    - _Requirements: 10.1–10.5_

- [ ] 5. Implement workspace module
  - [ ] 5.1 Implement `create_workspace()` — git worktree creation and reuse
    - Run `git worktree add -b agent/issue-<number> <workspace_path> main` from `repo_path`
    - Reuse existing worktree if path already exists
    - _Requirements: 5.1, 5.2, 5.3, 5.6_

  - [ ] 5.2 Implement `cleanup_workspace()` — worktree removal, branch deletion, symlink cleanup
    - Run `git worktree remove` and `git branch -d` from `repo_path`
    - Remove `current/issue-<number>` symlink; preserve state logs
    - _Requirements: 5.4, 5.5_

  - [ ] 5.3 Implement `write_issue_context()` — write ISSUE.md to worktree
    - Write formatted issue context (title, body, comments, issue number) to `ISSUE.md` in worktree root
    - Accept optional `pr_context` parameter; if provided, append PR details (number, URL, title, review comments) after a horizontal rule separator
    - Always overwrite existing `ISSUE.md` to ensure latest content
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

  - [ ]* 5.4 Write unit tests for workspace module
    - Mock git subprocess calls; test create, reuse, cleanup, ISSUE.md writing
    - Test `write_issue_context()` with and without PR context
    - Test that ISSUE.md is overwritten (not appended) on repeated calls
    - _Requirements: 5.1–5.6, 6.1–6.6_

- [ ] 6. Checkpoint — Ensure all module tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Implement agent selection and execution modules
  - [ ] 7.1 Implement `calculate_role_capacity()` — available slots per role
    - Sum `max_concurrent` across agents with the role, subtract active lockfile count
    - _Requirements: 1.3_

  - [ ] 7.2 Implement `pick_agent()` — round-robin agent selection
    - Filter by role, skip agents at max concurrency, skip agents in cooldown
    - Round-robin across remaining candidates
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.7_

  - [ ] 7.3 Implement `run_agent()` — spawn agent CLI subprocess
    - Run agent's `command` via `subprocess.run()` with `cwd=workspace_path` and `capture_output=True`
    - Always uses the same `command` for initial runs and retries (resume flags are part of the command)
    - _Requirements: 7.1, 7.4, 8.1, 8.5, 16.1, 16.2_

  - [ ]* 7.4 Write unit tests for agent selection and execution
    - Test capacity calculation, round-robin ordering, cooldown skipping
    - _Requirements: 1.3, 4.1–4.5, 4.7, 7.1, 7.4, 16.1, 16.2_

- [ ] 8. Implement state logging module
  - [ ] 8.1 Implement `write_state_log()` — YAML state log with sequential indexing
    - Write log to `state_base/issue-<number>/<index>-<state>.log` in YAML format
    - Include timestamp, issue number, agent name, role, prev_state, curr_state, attempt, stdout, stderr
    - Update `current/issue-<number>` symlink to latest log
    - _Requirements: 11.1, 11.2, 11.3_

  - [ ] 8.2 Implement `get_attempt_count()` and `get_next_log_index()`
    - Count `*-changes-requested.log` files for attempt tracking
    - Derive next sequential index supporting continuation after cleanup/reopen
    - _Requirements: 9.3, 11.4, 11.5_

  - [ ]* 8.3 Write unit tests for state logging module
    - Test log writing, index sequencing, symlink updates, attempt counting, reopen continuation
    - _Requirements: 9.3, 11.1–11.5_

- [ ] 9. Implement notification module
  - [ ] 9.1 Implement `notify()` — Telegram and Discord notifications
    - POST to Telegram Bot API when configured (token + chat_id)
    - POST to Discord webhook URL when configured
    - Send to both channels when both configured; silently skip unconfigured
    - Trigger on `changes-requested`, `ready-to-merge`, `human-review-required`
    - Also notify on agent rotation due to rate limiting
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 4.6_

  - [ ]* 9.2 Write unit tests for notification module
    - Mock HTTP calls; test Telegram-only, Discord-only, both, neither
    - _Requirements: 13.1–13.4, 4.6_

- [ ] 10. Checkpoint — Ensure all module tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 11. Implement main orchestration loop
  - [ ] 11.1 Implement `main()` — single poll cycle orchestration
    - Load and validate config (reload every cycle)
    - Validate .gitignore
    - For each role: poll issues, calculate capacity, pick up issues up to capacity
    - For each picked-up issue: transition label, create/reuse workspace, fetch PR context via `fetch_pr_context()`, write ISSUE.md (overwrite with issue + PR context), pick agent, post assignment comment via `post_assignment_comment()`, acquire lock, run agent, process result, write state log, update symlink, notify, release lock
    - _Requirements: 1.1, 1.4, 1.5, 1.7, 1.8, 3.3, 6.4, 6.5, 7.6, 8.6_

  - [ ] 11.2 Implement result processing — coding and review outcome handling
    - Coding agent exit 0 → transition `in-progress` to `pr-opened`
    - Coding agent non-zero → log failure, send notification
    - Review agent approve → transition `reviewing` to `ready-to-merge`
    - Review agent changes → transition `reviewing` to `changes-requested`
    - _Requirements: 7.2, 7.3, 8.2, 8.3, 8.4_

  - [ ] 11.3 Implement change-request retry loop
    - Handle `changes-requested` issues: fetch PR context via `fetch_pr_context()`, re-write ISSUE.md with latest issue + PR context (including review comments), spawn coding agent using `command` in same worktree
    - Post retry assignment comment via `post_assignment_comment()` with `is_retry=True` before spawning agent
    - On success, transition back to `pr-opened` for re-review
    - On attempt >= 3, transition to `human-review-required`, clean up workspace, notify
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 6.4, 6.5_

  - [ ] 11.4 Wire runtime logging to `/tmp/agentic-loop.log` and `/tmp/agentic-loop.error.log`
    - Configure Python logging for stdout/stderr file output
    - _Requirements: 14.3_

  - [ ]* 11.5 Write integration tests for the main orchestration loop
    - Mock GitHub CLI and subprocess calls end-to-end
    - Test full poll cycle: issue pickup → assignment comment → agent execution → label transition → state log
    - Test retry loop with retry assignment comment and human-review escalation
    - Test that ISSUE.md is re-written with PR context before review and retry agent invocations
    - Test ISSUE.md without PR context when no PR exists for the branch
    - _Requirements: 1.1–1.8, 6.4–6.6, 7.1–7.4, 7.6, 8.1–8.6, 9.1–9.6_

- [ ] 12. Checkpoint — Ensure orchestration tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 13. Implement merge.sh and crontab config
  - [ ] 13.1 Implement `merge.sh` — auto-merge script
    - List PRs with `ready-to-merge` label using `gh pr list`
    - Merge each with `gh pr merge --squash --auto`
    - Rely on PR closing keywords for automatic issue closure
    - _Requirements: 12.1, 12.2, 12.3, 12.4_

  - [ ] 13.2 Create `config/crontab.example` — crontab setup reference
    - Dispatcher entry: `*/3 * * * *` running `dispatcher.py` with log redirection
    - Merge entry: `*/3 * * * *` with `sleep 90` offset running `merge.sh`
    - _Requirements: 15.1, 15.2, 15.3_

- [ ] 14. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- The design uses Python — all implementation tasks target Python
- No property-based tests are included since the design has no Correctness Properties section
- `merge.sh` is a standalone shell script, not part of `dispatcher.py`
