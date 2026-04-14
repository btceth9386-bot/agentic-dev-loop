# agentic-loop — Spec

## Overview

A local multi-agent CI/CD pipeline (macOS / Linux) that autonomously handles GitHub Issues through implementation, review, and merge — with human oversight preserved at issue validation checkpoints.

---

## Pipeline Flow

```
+----------------------------+
| Human or Agent opens Issue |
+----------------------------+
             |
             v
+--------------------------------+
| Dispatcher detects new issue   |
| (crontab polling, every 3 min) |
+--------------------------------+
             |
             | label: in-progress
             v
+--------------------------------------+
| Coding Agent implements              |
| (kiro-cli / codex)                   |
| git commit + push + gh pr create     |
+--------------------------------------+
             |
             | label: pr-opened
             v
+--------------------------------------+
| Review Agent reviews PR              |
| (claude / other)                     |
+--------------------------------------+
             |
      +------+--------+
      |               |
  [approved]   [changes-requested]
      |               |
      |               | label: changes-requested
      |               v
      |      +---------------------------+
      |      | Coding Agent fixes        |
      |      | (same workspace, --resume)|
      |      +---------------------------+
      |               |
      |        attempt <= 3?
      |          +----+----+
      |        [yes]      [no]
      |          |         |
      |          |         v
      |          |  +------------------------------+
      |          |  | label: human-review-required |
      |          |  | (human takes over)           |
      |          |  +------------------------------+
      |          |
      |    (back to review)
      |
      | label: ready-to-merge
      v
+---------------------+
| Cronjob merges PR   |
+---------------------+
             |
             v
+-------------------+
| Issue closed      |
+-------------------+
             |
             v
+-------------------------------+
| Human validates closed issue  |
+-------------------------------+
             |
         [reopen]
             |
             v
   (new cycle begins)
```

---

## Infrastructure

### Event Detection

- **Method**: GitHub CLI polling (not webhook)
- **Scheduler**: `crontab` (every 3 minutes, works on macOS and Linux)
- **No public server or domain required**

### Why Polling over Webhook

- Long-term local process, no fixed IP or domain
- Label-as-state provides natural mutex, eliminating most polling race conditions
- Simpler infrastructure (zero external dependencies)

### Race Condition Prevention

- Single dispatcher process (no multiple pollers)
- Dispatcher atomically labels issue `in-progress` before spawning agent
- Per-agent lockfile prevents concurrent assignment to same agent
- Next poll skips any issue already carrying a state label

---

## Label State Machine

```
(no label)              → new, eligible for pickup
in-progress             → coding agent working
pr-opened               → awaiting review
reviewing               → review agent working
changes-requested       → fixer agent working
ready-to-merge          → cronjob target
human-review-required   → escalated, needs human
```

Label transitions are the single source of truth for issue state.
State survives dispatcher restarts (stored in GitHub, not locally).

---

## Agent Configuration

### `agents.yml`

```yaml
pipeline:
  repo_path: "/path/to/your/repo"
  workspace_base: "~/.agent-pipeline/your-repo-name/workspaces"
  state_base: "~/.agent-pipeline/your-repo-name/state"

agents:
  - name: kiro-cli
    role: coding
    command: "kiro-cli --agent senior --no-interactive"
    max_concurrent: 2
    cooldown_minutes: 30

  - name: claude
    role: review
    command: "claude --dangerously-skip-permissions -p ''"
    max_concurrent: 1
    cooldown_minutes: 30

  - name: codex
    role: coding
    command: "codex --no-interactive"
    max_concurrent: 1
    cooldown_minutes: 30

roles:
  coding:
    trigger: "issue.opened, issue.reopened"
    label_on_start: "in-progress"
    label_on_done: "pr-opened"
  review:
    trigger: "pull_request.opened, pull_request.synchronize"
    label_on_start: "reviewing"
    label_on_done: "ready-to-merge, changes-requested"
```

### Role Rotation

- `role` field can be changed at any time without restarting dispatcher
- Dispatcher reloads `agents.yml` on every poll cycle
- Use rotation to balance token consumption across providers

### Rate Limit Handling

- `cooldown_minutes` triggers after rate limit detection
- Dispatcher automatically rotates to next available agent with same role
- Notification sent when rotation occurs
- Manual control: `scripts/enable-agent-cooldown.sh` / `scripts/disable-agent-cooldown.sh`

---

## Workspace Design

### Per-Issue Git Worktree

Each issue gets an isolated git worktree and branch. `~/.agent-pipeline` is a plain directory (no `.git`), placed under home to support multiple repos:

```
~/.agent-pipeline/
  your-repo-name/
    workspaces/
      issue-42/              ← git worktree (repo root), branch: agent/issue-42
        .git                 ← file (not folder), points back to main repo .git
        src/                 ← repo code (agent develops here)
        tests/
        README.md
        ISSUE.md             ← issue context written by dispatcher
        .kiro/               ← kiro session (auto-resume)
        .claude/             ← claude session (auto-resume)
        .codex/              ← codex session (auto-resume)
      issue-38/
        ...
    state/
      issue-42/
        01-in-progress.log
        02-pr-opened.log
        03-reviewing.log
        04-changes-requested.log
        05-in-progress.log
        06-ready-to-merge.log
    current/
      issue-42 -> symlink to latest state log
```

### Workspace Lifecycle

**Why git worktree instead of plain `mkdir`?**

A plain folder cannot run `git commit`, `git push`, or `gh pr create` — agent CLIs need a real git repo context. `git worktree` creates a full checkout in a separate directory, on a dedicated branch, sharing the main repo's `.git` object store. No full clone needed per issue.

`git worktree add` must be run from inside the main repo (`repo_path`):

```bash
# Dispatcher runs this from repo_path
cd /path/to/your/repo
git worktree add -b agent/issue-42 \
  ~/.agent-pipeline/your-repo-name/workspaces/issue-42 origin/main
```

The resulting `~/.agent-pipeline/.../issue-42/` is a normal directory. Its `.git` is a pointer file (not a folder) pointing back to the main repo's `.git`. Agent CLI runs from this directory as its cwd — identical to a developer `cd`-ing into the repo root.

**Parallel coding agents — race condition resolved:**

Each issue has its own worktree on its own branch, so multiple coding agents working on different issues simultaneously never share the same working directory or branch:

```
Agent 1 (kiro-cli) → cwd: workspaces/issue-42/  (branch: agent/issue-42)
Agent 2 (codex)    → cwd: workspaces/issue-38/  (branch: agent/issue-38)
                              ↑
                    fully isolated, no file conflicts
```

Lockfile only needs to prevent two agents grabbing the same issue — filesystem-level race conditions are already eliminated by worktree isolation.

**Cleanup (prevent disk bloat):**

Worktrees are cleaned up at pipeline termination points. State logs are kept (plain text, negligible size).

```python
def cleanup_workspace(issue_number):
    workspace = f"~/.agent-pipeline/your-repo-name/workspaces/issue-{issue_number}"
    branch = f"agent/issue-{issue_number}"

    # Remove worktree (the large part — full repo checkout)
    subprocess.run(
        ["git", "worktree", "remove", workspace, "--force"],
        cwd=config["pipeline"]["repo_path"]
    )
    # Delete branch
    subprocess.run(
        ["git", "branch", "-d", branch],
        cwd=config["pipeline"]["repo_path"]
    )
    # Remove current symlink
    os.remove(f"~/.agent-pipeline/your-repo-name/current/issue-{issue_number}")

    notify(f"🧹 Issue #{issue_number} workspace cleaned up")
```

Cleanup triggers:

| Trigger | Action |
|---------|--------|
| PR merged → issue closed | `cleanup_workspace(issue_number)` |
| attempt >= 3 → `human-review-required` | `cleanup_workspace(issue_number)` |

**Human reopen after cleanup:**

Worktree is recreated from `main`. State logs survive and new log sequence continues from next index (e.g. `07-in-progress.log`).

```python
def create_workspace(issue_number):
    workspace = f"~/.agent-pipeline/your-repo-name/workspaces/issue-{issue_number}"

    if os.path.exists(workspace):
        return workspace  # still exists, resume directly

    # Recreate after cleanup (e.g. human reopen)
    subprocess.run([
        "git", "worktree", "add", "-b",
        f"agent/issue-{issue_number}",
        workspace, "main"
    ], cwd=config["pipeline"]["repo_path"])

    return workspace
```

### Session Resume

- Each CLI stores its session inside the workspace directory (`.kiro/`, `.claude/`, etc.)
- Dispatcher always runs agent CLI with `cwd=workspaces/issue-N` (repo root)
- Resume is automatic — no session ID tracking required
- Works uniformly across all CLI tools regardless of their internal resume mechanism

### Issue Context Injection

Dispatcher writes issue content to workspace before executing agent:

```bash
gh issue view 42 --json title,body,comments > \
  ~/.agent-pipeline/your-repo-name/workspaces/issue-42/ISSUE.md
```

Agent CLI reads `ISSUE.md` from cwd (repo root). No CLI-specific flags needed for context passing.

---

## Dispatcher Design

### Responsibilities

| Concern | Owner |
|---------|-------|
| Issue detection & polling | dispatcher.py |
| Workspace create / cleanup | dispatcher.py |
| Lockfile management | dispatcher.py |
| State log writing | dispatcher.py |
| Label transitions | dispatcher.py |
| Rate limit / agent rotation | dispatcher.py |
| Notifications | dispatcher.py |
| Code implementation / review | agent CLI (subprocess) |
| Session resume | agent CLI (auto, via cwd) |

### Agent Selection (pick_agent)

- Filter agents by `role`
- Skip agents with active lockfile
- Round-robin across available agents (token balance)
- Skip agents in cooldown (rate-limited)

### Lockfile Format

```
/tmp/agent-kiro-cli.lock
content: "<unix_timestamp>:<issue_number>"
```

Lockfiles older than 30 minutes are treated as stale and auto-released.

### State Log Format

```yaml
# ~/.agent-pipeline/state/issue-42/04-changes-requested.log
timestamp: 2024-01-15T10:23:45
issue: 42
agent: claude
role: review
prev_state: reviewing
curr_state: changes-requested
attempt: 2
stdout: |
  ... full agent CLI output ...
stderr: |
  ... errors if any ...
```

Logs are written by dispatcher using `capture_output=True` from subprocess. Agent CLIs have no awareness of the logging system.

---

## Agent CLI Requirements

For an agent CLI to be compatible with this pipeline:

| Requirement | Detail |
|-------------|--------|
| Non-interactive mode | Must complete without user input (`--no-interactive` or equivalent) |
| Correct exit codes | `0` = success, non-zero = failure |
| Self-contained execution | Must be able to read ISSUE.md, modify code, commit, push, and open PR/review autonomously |
| cwd-based session | Session data stored in current working directory (not global `~/.config/`) |

---

## Scheduler (crontab)

Compatible with macOS and Linux. Both dispatcher and auto-merge run via crontab:

```bash
crontab -e
```

```cron
# Run dispatcher every 3 minutes
*/3 * * * * /usr/bin/python3 /path/to/dispatcher.py >> /tmp/agentic-loop.log 2>> /tmp/agentic-loop.error.log

# Run auto-merge every 3 minutes (offset by 90s to avoid overlap)
*/3 * * * * sleep 90 && /bin/bash /path/to/merge.sh >> /tmp/agentic-loop-merge.log 2>&1
```

> **Note**: crontab minimum interval is 1 minute. 3 minutes is a safe default balancing responsiveness and GitHub API rate limit consumption.

---

## Auto-merge Cronjob

Separate from dispatcher. Targets `ready-to-merge` label only:

```bash
#!/bin/bash
# merge.sh — run via launchd or crontab

APPROVED_PRS=$(gh pr list --label "ready-to-merge" --json number --jq '.[].number')

for PR in $APPROVED_PRS; do
  gh pr merge $PR --squash --auto
done
```

---

## Notifications

Notifications are sent by dispatcher at key state transitions only (not every step):

```python
NOTIFY_ON = [
    "changes-requested",
    "ready-to-merge",
    "human-review-required",
    # optional:
    "in-progress",
    "pr-opened"
]
```

### Telegram

```bash
curl -s -X POST "https://api.telegram.org/bot$TOKEN/sendMessage" \
  -d chat_id="$CHAT_ID" \
  -d text="$MESSAGE" \
  -d parse_mode="Markdown"
```

### Discord

```bash
curl -s -X POST "$DISCORD_WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d "{\"content\": \"$MESSAGE\"}"
```

### Status Report (optional, every N minutes)

Periodic summary of all agent and issue states, driven by lockfile inspection and `current/` symlinks.

---

## Observability

```bash
# All issues current state
ls -la ~/.agent-pipeline/current/

# Full history of one issue
ls ~/.agent-pipeline/state/issue-42/

# Read specific state detail
cat ~/.agent-pipeline/state/issue-42/04-changes-requested.log

# Live dispatcher log
tail -f /tmp/agentic-loop.log
```

---

## Repository Structure (suggested)

```
agentic-loop/
  dispatcher.py          ← main orchestrator
  merge.sh               ← auto-merge cronjob script
  agents.yml             ← agent + role configuration
  config/
    crontab.example      ← crontab setup reference (macOS + Linux)
  docs/
    spec.md              ← this file
  scripts/
    rotate-role.sh       ← CLI helper to swap agent roles
    monitor.sh            ← pipeline monitoring dashboard
```

---

## Key Design Principles

- **Single dispatcher**: only one process writes labels and assigns work
- **File system as database**: all state in `~/.agent-pipeline/`, survives restarts
- **GitHub labels as state machine**: visible in UI, no external DB needed
- **Config-driven agent roles**: swap roles by editing `agents.yml`, no code changes
- **Workspace isolation**: per-issue git worktree prevents agents from interfering
- **Human-in-the-loop at validation only**: automation handles execution, human handles judgment (reopen, human-review-required)
