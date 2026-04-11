# Design Document: Agentic Loop

## Overview

Agentic Loop is a local multi-agent CI/CD pipeline for macOS/Linux that autonomously processes GitHub Issues through coding, review, and merge stages. The system is built around three artifacts:

- `dispatcher.py` — a stateless Python orchestrator invoked every 3 minutes via crontab
- `agents.yml` — a YAML configuration file defining agents, roles, and pipeline behavior
- `merge.sh` — a shell script that auto-merges approved PRs on a separate cron schedule

The Dispatcher polls GitHub for issues matching per-role pickup labels, assigns agents via round-robin rotation, manages per-issue git worktrees for isolation, enforces lockfile-based concurrency, writes structured state logs, and sends notifications at key transitions. GitHub labels serve as the single source of truth for issue state, eliminating the need for a local database and ensuring state survives restarts.

## Architecture

### High-Level Architecture

```
+------------------+       +----------------------------------+
|     Crontab      |       |   External Services              |
|                  |       |                                  |
| */3 dispatcher.py|       |  GitHub API (gh CLI)             |
| */3 sleep90      |       |  Telegram API                    |
|     merge.sh     |       |  Discord Webhook                 |
+--------+---------+       +----------------------------------+
         |                          ^         ^         ^
         v                          |         |         |
+--------------------------------------------------+   |
|         dispatcher.py (single invocation)        |   |
|                                                  |   |
|  Load agents.yml                                 |   |
|       |                                          |   |
|  Validate config + .gitignore                    |   |
|       |                                          |   |
|  Poll GitHub per role ───────────────────────────+   |
|       |                                          |   |
|  Calculate Role_Capacity                         |   |
|       |                                          |   |
|  Assign issues to agents                         |   |
|       |                                          |   |
|  Create/reuse worktrees ──────┐                  |   |
|       |                       v                  |   |
|  Write ISSUE.md    ~/.agent-pipeline/            |   |
|       |              workspaces/issue-N/          |   |
|  Acquire lockfile    state/issue-N/              |   |
|       |              current/issue-N             |   |
|  Spawn agent subprocess ──────┐                  |   |
|       |                       v                  |   |
|  Process exit code   Agent CLIs (subprocesses)   |   |
|       |                Coding (kiro-cli/codex)   |   |
|  Transition labels ──────────────────────────────+   |
|       |                Review (claude/other)     |   |
|  Write state log                                 |   |
|       |                                          |   |
|  Send notifications ─────────────────────────────+
|       |                                          |
|  Release lockfile                                |
+--------------------------------------------------+
```

### Execution Model

The Dispatcher is stateless — each crontab invocation is a fresh process. It reconstructs state from:

1. GitHub labels (issue state)
2. Lockfiles in `/tmp/` (agent occupancy)
3. State log files on disk (attempt counts, history)

This means the Dispatcher can crash or be killed at any point and the next invocation will resume correctly. The only side effects that must be atomic are label transitions on GitHub.

### Component Interaction

```
Cron ──> dispatcher.py
              |
              ├── load agents.yml ──> Filesystem
              ├── validate .gitignore ──> Filesystem
              ├── poll issues per role pickup_label ──> GitHub (gh CLI)
              ├── count lockfiles per agent ──> Filesystem
              ├── calculate capacity, select issues
              ├── remove pickup_label, apply label_on_start ──> GitHub
              ├── git worktree add (if needed) ──> Filesystem
              ├── gh issue view → ISSUE.md ──> GitHub
              ├── gh pr list/view → PR context (if exists) ──> GitHub
              ├── write ISSUE.md to worktree (overwrite) ──> Filesystem
              ├── create lockfile ──> Filesystem
              ├── subprocess.run(command, cwd=worktree) ──> Agent CLI
              │                                                 |
              │   <── exit code + stdout/stderr ────────────────┘
              ├── apply label_on_done (or error label) ──> GitHub
              ├── write state log ──> Filesystem
              ├── update current/ symlink ──> Filesystem
              ├── remove lockfile ──> Filesystem
              └── notify (Telegram/Discord) ──> GitHub
```


## Components and Interfaces

### Module Structure

```
agentic-loop/
  dispatcher.py          # Main orchestrator (entry point)
  merge.sh               # Auto-merge cronjob script
  agents.yml             # Agent + role configuration
  config/
    crontab.example      # Crontab setup reference
  scripts/
    rotate-role.sh       # CLI helper to swap agent roles
    status.sh            # Print current pipeline state
```

### dispatcher.py — Internal Modules

The dispatcher is a single Python file organized into logical sections. Each section is described as a function group below.

#### 1. Configuration Module

```python
def load_config(config_path: str) -> dict:
    """
    Load and validate agents.yml.
    - Parses YAML
    - Expands ${VAR_NAME} patterns in all string values using os.environ
      (regex: r'\$\{[A-Za-z_][A-Za-z0-9_]*\}')
      Raises descriptive error if a referenced env var is not set.
      Strings without ${...} patterns are left unchanged.
    - Validates required agent fields: name, role, command, max_concurrent
    - Validates optional agent fields: cooldown_minutes
    - Validates required role fields: pickup_label, label_on_start, label_on_done
    - Raises descriptive errors on missing fields or invalid YAML
    Returns parsed config dict.
    """

def validate_gitignore(repo_path: str) -> None:
    """
    Check that .gitignore exists at repo_path and contains all
    Required_Gitignore_Entries: ISSUE.md, .kiro/, .claude/, .codex/, .copilot/, .gemini/
    Raises descriptive error if missing file or entries.

    Also checks whether AGENTS.md exists at repo_path.
    If missing, logs a warning:
      "⚠️ AGENTS.md not found in {repo_path} — agent CLIs may lack project context."
    This is a non-fatal check — the Dispatcher continues execution.
    """
```

#### 2. GitHub Interaction Module

```python
def poll_issues(repo_path: str, label: str) -> list[dict]:
    """
    Run `gh issue list --label <label> --json number,title,labels`
    from repo_path. Returns list of issue dicts.
    Filters out issues carrying any state label other than the pickup label.
    """

def transition_label(repo_path: str, issue_number: int, remove_label: str, add_label: str) -> None:
    """
    Atomically remove one label and add another on a GitHub issue.
    Uses `gh issue edit <number> --remove-label <old> --add-label <new>`.
    """

def fetch_issue_context(repo_path: str, issue_number: int) -> str:
    """
    Run `gh issue view <number> --json title,body,comments` from repo_path.
    Returns formatted markdown string for ISSUE.md.
    """

def post_assignment_comment(repo_path: str, issue_number: int, agent_name: str, role: str, attempt: int, is_retry: bool = False) -> None:
    """
    Post a comment on the GitHub issue indicating which agent was assigned.
    Runs `gh issue comment <number> --body "<message>"` from repo_path.

    Format:
      - Normal:  🤖 Assigned to **<agent_name>** (<role>) — attempt <N>
      - Retry:   🤖 Assigned to **<agent_name>** (<role>) — retry attempt <N>
    """

def fetch_pr_context(repo_path: str, issue_number: int) -> str | None:
    """
    Check if a PR exists for the issue's branch and fetch its details.
    1. Run `gh pr list --head agent/issue-<number> --json number,url,title --limit 1`
       from repo_path to find a PR for the branch.
    2. If no PR exists, return None.
    3. If a PR exists, run `gh pr view <pr_number> --json number,url,title,reviews,comments`
       from repo_path to fetch full details including review comments.
    4. Format the result as a markdown string suitable for appending to ISSUE.md:
       ---
       # Pull Request #<number>
       ## URL
       <url>
       ## Title
       <title>
       ## Review Comments
       <formatted review comments with author and body>
    Returns formatted markdown string or None if no PR exists.
    """
```

#### 3. Workspace Module

```python
def create_workspace(repo_path: str, workspace_base: str, issue_number: int) -> str:
    """
    Create git worktree at workspace_base/issue-<number> on branch
    agent/issue-<number> from main. Reuses existing worktree if present.
    Returns workspace path.
    """

def cleanup_workspace(repo_path: str, workspace_base: str, issue_number: int) -> None:
    """
    Remove git worktree, delete branch, remove current/ symlink.
    State logs are preserved.
    """

def write_issue_context(workspace_path: str, context: str, pr_context: str | None = None) -> None:
    """
    Write ISSUE.md to the worktree root with issue number, title, body, comments.
    If pr_context is provided (not None), append it after the issue details
    separated by a horizontal rule.
    Always overwrites the existing ISSUE.md to ensure latest content.
    """
```

#### 4. Lockfile Module

```python
LOCKFILE_DIR = "/tmp"
STALE_THRESHOLD_MINUTES = 30

def acquire_lock(agent_name: str, issue_number: int) -> str:
    """
    Create /tmp/agent-<name>-<issue_number>.lock with content
    '<unix_timestamp>:<issue_number>'. Returns lockfile path.
    """

def release_lock(agent_name: str, issue_number: int) -> None:
    """Remove the lockfile for the given agent and issue."""

def get_active_locks(agent_name: str) -> list[dict]:
    """
    List all non-stale lockfiles for the agent.
    Lockfiles older than STALE_THRESHOLD_MINUTES are auto-released.
    Returns list of {issue_number, timestamp} dicts.
    """

def count_active_locks(agent_name: str) -> int:
    """Count of active (non-stale) lockfiles for the agent."""
```

#### 5. Agent Selection Module

```python
def pick_agent(agents: list[dict], role: str, round_robin_state: dict) -> dict | None:
    """
    Select next available agent for the given role:
    1. Filter agents by role
    2. Skip agents with active locks >= max_concurrent
    3. Skip agents in cooldown
    4. Round-robin across remaining agents
    Returns agent dict or None if no agent available.
    """

def calculate_role_capacity(agents: list[dict], role: str) -> int:
    """
    Sum max_concurrent across all agents with the given role,
    subtract currently occupied slots (active lockfiles).
    Returns available capacity.
    """
```

#### 6. Agent Execution Module

```python
def run_agent(agent: dict, workspace_path: str) -> tuple[int, str, str]:
    """
    Spawn agent CLI as subprocess with cwd=workspace_path.
    Always runs agent['command'] — resume flags are included in the command by the user.
    Uses capture_output=True.
    Returns (exit_code, stdout, stderr).
    """
```

#### 7. State Logging Module

```python
def write_state_log(
    state_base: str, issue_number: int, agent_name: str,
    role: str, prev_state: str, curr_state: str,
    attempt: int, stdout: str, stderr: str
) -> str:
    """
    Write YAML state log to state_base/issue-<number>/<index>-<state>.log.
    Index is derived from existing files in the directory.
    Updates current/ symlink.
    Returns log file path.
    """

def get_attempt_count(state_base: str, issue_number: int) -> int:
    """
    Count files matching *-changes-requested.log in the issue's state directory.
    Returns the review attempt count.
    """

def get_next_log_index(state_base: str, issue_number: int) -> int:
    """
    Determine next sequential log index for the issue.
    Supports continuation after cleanup/reopen.
    """
```

#### 8. Notification Module

```python
def notify(message: str, config: dict) -> None:
    """
    Send notification to configured channels.
    - Telegram: POST to Bot API with token and chat_id
    - Discord: POST to webhook URL
    Sends to both if both are configured. Silently skips unconfigured channels.
    """

NOTIFY_ON = [
    "changes-requested",
    "ready-to-merge",
    "human-review-required",
]
```

#### 9. Main Orchestration Loop

```python
def main():
    """
    Entry point. Single poll cycle:
    1. Load and validate config
    2. Validate .gitignore
    3. For each role in config:
       a. Poll GitHub for issues with role's pickup_label
       b. Calculate available capacity
       c. For each issue up to capacity:
          - Transition label (pickup → label_on_start)
          - Create/reuse workspace
          - Fetch PR context via fetch_pr_context() (returns None if no PR)
          - Write ISSUE.md (overwrite) with issue context + PR context
          - Pick agent (round-robin)
          - Post assignment comment on GitHub issue
          - Acquire lockfile
          - Run agent subprocess
          - Process result (transition label, handle retries)
          - Write state log
          - Update current/ symlink
          - Send notifications
          - Release lockfile
    4. Handle changes-requested issues (retry loop, with retry assignment comment)
       - Before spawning retry coding agent: fetch PR context, re-write ISSUE.md
    5. Clean up stale lockfiles
    """
```

### merge.sh — Interface

```bash
#!/bin/bash
# Lists PRs with ready-to-merge label
# Merges each with --squash --auto
# Relies on PR closing keywords for issue closure
```

### Inter-Component Communication

| From | To | Mechanism |
|------|----|-----------|
| Crontab | dispatcher.py | Process invocation |
| Crontab | merge.sh | Process invocation |
| dispatcher.py | GitHub | `gh` CLI subprocess |
| dispatcher.py | Agent CLI | `subprocess.run()` with `cwd` |
| dispatcher.py | Filesystem | Direct file I/O (worktrees, logs, lockfiles) |
| dispatcher.py | Telegram | HTTP POST via `curl` or `requests` |
| dispatcher.py | Discord | HTTP POST via `curl` or `requests` |
| Agent CLI | GitHub | `gh` CLI (commit, push, PR create/review) |
| merge.sh | GitHub | `gh` CLI |


## Data Models

### agents.yml Schema

```yaml
pipeline:
  repo_path: str          # Absolute path to the main git repository
  workspace_base: str     # Path template for worktree directories
  state_base: str         # Path template for state log directories

agents:
  - name: str             # Unique agent identifier (e.g., "kiro-cli")
    role: str             # Role name (must match a key in `roles`)
    command: str          # Complete CLI invocation (executed as-is). Include resume flags (e.g., --resume) if your CLI supports them — the Dispatcher always runs this same command.
    max_concurrent: int   # Max simultaneous issues for this agent
    cooldown_minutes: int # Minutes to skip agent after rate limit (default: 0)

roles:
  <role_name>:            # e.g., "coding", "review"
    pickup_label: str     # Label to poll for (e.g., "todo", "pr-opened")
    label_on_start: str   # Label applied when agent begins work
    label_on_done: str    # Label(s) applied on completion (comma-separated for review)

notifications:            # Optional section — supports ${VAR_NAME} env var expansion
  telegram:
    token: "${TELEGRAM_BOT_TOKEN}"       # Expanded from env var at load time
    chat_id: "${TELEGRAM_CHAT_ID}"       # Expanded from env var at load time
  discord:
    webhook_url: "${DISCORD_WEBHOOK_URL}" # Expanded from env var at load time
```

### Label State Machine Transitions

```
                          +-------------------+
                          |    (no label)     |
                          +--------+----------+
                                   |
                          Human applies "todo"
                                   |
                                   v
                          +-------------------+
                          |       todo        |
                          +--------+----------+
                                   |
                    Dispatcher picks up (coding role)
                                   |
                                   v
                          +-------------------+
               +--------->|   in-progress     |<---------+
               |          +--------+----------+          |
               |                   |                     |
               |       Coding agent succeeds (exit 0)    |
               |                   |                     |
               |                   v                     |
               |          +-------------------+          |
               |          |    pr-opened      |          |
               |          +--------+----------+          |
               |                   |                     |
               |    Dispatcher picks up (review role)    |
               |                   |                     |
               |                   v                     |
               |          +-------------------+          |
               |          |    reviewing      |          |
               |          +--------+----------+          |
               |                   |                     |
               |          +--------+----------+          |
               |          |                   |          |
               |       approved          changes         |
               |          |              requested       |
               |          v                   |          |
               |  +---------------+           v          |
               |  | ready-to-merge|   +----------------+ |
               |  +-------+-------+   |  changes-      | |
               |          |           |  requested      | |
               |    merge.sh merges   +-------+--------+ |
               |    PR, issue closed          |          |
               |          |           +-------+--------+ |
               |          v           |                 | |
               |       [done]    attempt < 3      attempt >= 3
               |                      |                 |
               |                      |                 v
               |                      |     +---------------------+
               |                      |     | human-review-       |
               |                      |     | required            |
               |                      |     +----------+----------+
               |                      |                |
               |            Dispatcher retries         v
               |            (coding agent)          [done]
               |                      |          (human takes over)
               +----------------------+
```

### State Log File (YAML)

```yaml
# ~/.agent-pipeline/<repo>/state/issue-<N>/<index>-<state>.log
timestamp: "2024-01-15T10:23:45"   # ISO 8601
issue: 42                           # GitHub issue number
agent: "claude"                     # Agent name from agents.yml
role: "review"                      # Role that was executed
prev_state: "reviewing"             # Label before transition
curr_state: "changes-requested"     # Label after transition
attempt: 2                          # Review attempt count
stdout: |                           # Full agent CLI stdout
  ...
stderr: |                           # Full agent CLI stderr
  ...
```

### Lockfile Format

```
# /tmp/agent-<name>-<issue_number>.lock
<unix_timestamp>:<issue_number>
```

Example: `/tmp/agent-kiro-cli-42.lock` containing `1705312345:42`

### Filesystem Layout

```
~/.agent-pipeline/
  <repo-name>/
    workspaces/
      issue-42/                    # Git worktree (full repo checkout)
        .git                       # Pointer file → main repo .git
        src/
        ISSUE.md                   # Injected by dispatcher
        .kiro/                     # Agent session data
      issue-38/
        ...
    state/
      issue-42/
        01-in-progress.log         # Sequential state logs
        02-pr-opened.log
        03-reviewing.log
        04-changes-requested.log
        05-in-progress.log
        06-pr-opened.log
        07-reviewing.log
        08-ready-to-merge.log
    current/
      issue-42 → symlink to latest state log
      issue-38 → symlink to latest state log
```

### Round-Robin State

The Dispatcher maintains round-robin state within a single invocation. Since each invocation is stateless, round-robin resets each cycle. This is acceptable because:

- Poll cycles are 3 minutes apart
- Agent availability changes between cycles (lockfiles, cooldowns)
- True long-term balance is achieved by the natural variation in agent availability

```python
# In-memory during single invocation
round_robin_index: dict[str, int] = {}  # role → last_used_agent_index
```

### Key Algorithms

#### Poll Cycle Algorithm

```
for each role in config.roles:
    issues = poll_github(role.pickup_label)
    issues = filter_out_issues_with_other_state_labels(issues)
    capacity = calculate_role_capacity(config.agents, role)
    to_process = issues[:capacity]

    for issue in to_process:
        transition_label(issue, role.pickup_label, role.label_on_start)
        workspace = create_workspace(issue.number)
        context = fetch_issue_context(repo_path, issue.number)
        pr_context = fetch_pr_context(repo_path, issue.number)
        write_issue_context(workspace, context, pr_context)
        agent = pick_agent(config.agents, role)
        if agent is None:
            continue  # no agent available, will retry next cycle
        post_assignment_comment(repo_path, issue.number, agent.name, role, attempt=1)
        acquire_lock(agent.name, issue.number)
        exit_code, stdout, stderr = run_agent(agent, workspace)
        process_result(issue, agent, role, exit_code, stdout, stderr)
        release_lock(agent.name, issue.number)
```

#### Agent Selection Algorithm

```
def pick_agent(agents, role, round_robin_state):
    candidates = [a for a in agents if a.role == role]
    candidates = [a for a in candidates if count_active_locks(a.name) < a.max_concurrent]
    candidates = [a for a in candidates if not in_cooldown(a)]

    if not candidates:
        return None

    idx = round_robin_state.get(role, 0) % len(candidates)
    round_robin_state[role] = idx + 1
    return candidates[idx]
```

#### Result Processing Algorithm

```
def process_result(issue, agent, role, exit_code, stdout, stderr):
    if role == "coding":
        if exit_code == 0 or pr_exists(repo_path, issue.number):
            transition_label(issue, "in-progress", "pr-opened")
        else:
            log_failure(issue, agent, stdout, stderr)
            notify("Coding agent failed on issue #{issue.number}")

    elif role == "review":
        if exit_code == 0 and pr_is_approved(repo_path, issue.number):
            transition_label(issue, "reviewing", "ready-to-merge")
        elif pr_has_review_comments(repo_path, issue.number):
            transition_label(issue, "reviewing", "changes-requested")
            attempts = get_attempt_count(issue.number)
            if attempts >= 3:
                transition_label(issue, "changes-requested", "human-review-required")
                cleanup_workspace(issue.number)
                notify("Issue #{issue.number} escalated to human review")
        else:
            log_warning("Review agent failed but no review comments found, skipping transition")
```

#### Change-Request Retry Comment

When the Dispatcher picks up a `changes-requested` issue for retry, it re-writes ISSUE.md with the latest PR context (including review comments) and posts a retry assignment comment before spawning the agent:

```
agent = pick_agent(config.agents, "coding")
context = fetch_issue_context(repo_path, issue.number)
pr_context = fetch_pr_context(repo_path, issue.number)
write_issue_context(workspace, context, pr_context)
post_assignment_comment(repo_path, issue.number, agent.name, "coding", attempt, is_retry=True)
acquire_lock(agent.name, issue.number)
run_agent(agent, workspace)
```
```

#### Workspace Cleanup Triggers

| Trigger | Action |
|---------|--------|
| PR merged → issue closed | `cleanup_workspace(issue_number)` |
| attempt >= 3 → `human-review-required` | `cleanup_workspace(issue_number)` |

Cleanup removes the worktree and branch but preserves state logs.

