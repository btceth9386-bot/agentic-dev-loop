# Agentic Loop

A local multi-agent CI/CD pipeline (macOS/Linux) that autonomously handles GitHub Issues through implementation, review, and merge.

## Project Structure

```
agentic-loop/
  dispatcher.py          # Main orchestrator (entry point, Python)
  merge.sh               # Auto-merge cronjob script (Bash)
  agents.yml             # Agent + role configuration (YAML)
  config/
    crontab.example      # Crontab setup reference
  scripts/
    rotate-role.sh       # CLI helper to swap agent roles
    status.sh            # Print current pipeline state
```

## How It Works

The Dispatcher (`dispatcher.py`) is a stateless Python process invoked every 3 minutes via crontab. Each invocation:

1. Loads `agents.yml` (validates config, expands `${VAR}` env vars)
2. Validates `.gitignore` contains required entries
3. Polls GitHub for issues matching each role's `pickup_label`
4. Calculates available capacity per role (sum of `max_concurrent` minus active lockfiles)
5. Assigns issues to agents via round-robin rotation
6. For each issue: creates git worktree, writes `ISSUE.md`, spawns agent CLI subprocess
7. Processes results: transitions labels, writes state logs, sends notifications

A separate `merge.sh` cronjob (offset by 90s) merges PRs labeled `ready-to-merge`.

## Label State Machine

```
todo → in-progress → pr-opened → reviewing → ready-to-merge → [merged/closed]
                                           → changes-requested → in-progress (retry, max 3)
                                           → human-review-required (after 3 failed attempts)
```

- `todo`: Human applies when issue is ready for pipeline
- Labels are the single source of truth (no local DB)

## Key Files in Workspace

When working on an issue, you are in a git worktree at `~/.agent-pipeline/<repo>/workspaces/issue-<N>/`. This is a full repo checkout on branch `agent/issue-<N>`.

- `ISSUE.md` — Injected by dispatcher. Contains issue number, title, body, comments. Use the issue number for PR titles: `Fix #<number>: <description>`
- `AGENTS.md` — This file. Project context for agent CLIs.
- `CLAUDE.md` — Symlink to AGENTS.md (Claude CLI compatibility).

## Coding Role Guidelines

When implementing an issue:

1. Read `ISSUE.md` for the issue details and number
2. Implement the requested changes
3. Commit, push, and create a PR with title format: `Fix #<issue_number>: <description>`
4. The closing keyword in the PR title auto-closes the issue on merge
5. Exit with code 0 on success, non-zero on failure

## Review Role Guidelines

When reviewing a PR:

1. Read `ISSUE.md` for context on what the PR should accomplish
2. Review the code changes in the PR
3. Either approve or request changes
4. Exit with code 0 on completion

## Configuration (agents.yml)

```yaml
pipeline:
  repo_path: "/path/to/your/repo"
  workspace_base: "~/.agent-pipeline/your-repo-name/workspaces"
  state_base: "~/.agent-pipeline/your-repo-name/state"

agents:
  - name: kiro-cli
    role: coding
    command: "kiro-cli --resume --agent senior --no-interactive"
    max_concurrent: 2
    cooldown_minutes: 0

  - name: claude
    role: review
    command: "claude --dangerously-skip-permissions -p 'You are a code reviewer. Read ISSUE.md and AGENTS.md then begin.'"
    max_concurrent: 1
    cooldown_minutes: 0

roles:
  coding:
    pickup_label: "todo"
    label_on_start: "in-progress"
    label_on_done: "pr-opened"
  review:
    pickup_label: "pr-opened"
    label_on_start: "reviewing"
    label_on_done: "ready-to-merge, changes-requested"

notifications:
  telegram:
    token: "${TELEGRAM_BOT_TOKEN}"
    chat_id: "${TELEGRAM_CHAT_ID}"
  discord:
    webhook_url: "${DISCORD_WEBHOOK_URL}"
```

## Dispatcher Modules

| Module | Responsibility |
|--------|---------------|
| `load_config()` | Parse YAML, expand `${VAR}`, validate fields |
| `validate_gitignore()` | Check `.gitignore` has required entries |
| `poll_issues()` | Query GitHub for issues by pickup label |
| `transition_label()` | Atomic label swap on GitHub issues |
| `create_workspace()` / `cleanup_workspace()` | Git worktree lifecycle |
| `write_issue_context()` | Write `ISSUE.md` to worktree |
| `acquire_lock()` / `release_lock()` | Lockfile concurrency control (`/tmp/agent-<name>-<N>.lock`) |
| `pick_agent()` | Round-robin agent selection per role |
| `run_agent()` | Spawn agent CLI subprocess |
| `write_state_log()` | YAML state log with sequential indexing |
| `notify()` | Telegram/Discord notifications |

## State & Observability

- State logs: `~/.agent-pipeline/<repo>/state/issue-<N>/<index>-<state>.log` (YAML)
- Current state: `~/.agent-pipeline/<repo>/current/issue-<N>` (symlinks)
- Runtime logs: `/tmp/agentic-loop.log`, `/tmp/agentic-loop.error.log`
- Attempt count: derived from `*-changes-requested.log` file count per issue
