# Agentic Loop

A local multi-agent CI/CD pipeline that autonomously handles GitHub Issues — from implementation to review to merge — with no database, no server, and no webhooks required.

## Requirements

- macOS or Linux
- Python 3.10+
- [GitHub CLI (`gh`)](https://cli.github.com/) — **two authenticated accounts** (coder + reviewer, see [Per-agent credentials](#per-agent-credentials))
- Agent CLIs as configured in `agents.yml` (e.g. kiro-cli, claude, codex)

## Highlights

- **Zero infrastructure** — runs entirely on your local machine via crontab
- **No database** — GitHub labels are the single source of truth for issue state; survives restarts and crashes
- **Agent-agnostic** — works with any CLI-based agent (kiro-cli, claude, codex, or your own)
- **Parallel execution** — per-issue git worktrees keep agents fully isolated with no file conflicts
- **Self-healing** — stateless dispatcher resumes correctly after any crash or restart
- **Human-in-the-loop** — escalates to `human-review-required` after 3 failed review cycles
- **Notifications** — Telegram and/or Discord alerts at key state transitions

## Security Notice

> ⚠️ **Use in non-production environments only.**
>
> Agent CLIs are invoked with full permissions (e.g. `--dangerously-skip-permissions`). They can read, write, and execute arbitrary code in your repository. Do not point this pipeline at production repos or systems with sensitive data.

## How It Works

```
GitHub Issue (label: todo)
        |
        v
  dispatcher.py  (crontab, every 3 min)
        |
        ├── poll issues by label
        ├── git worktree add  (isolated workspace per issue)
        ├── write ISSUE.md    (issue + PR context)
        ├── post assignment comment on issue
        |
        ├── [coding agent]  implement → commit → push → open PR
        |        |
        |        v  label: pr-opened
        |
        ├── [review agent]  review PR
        |        |
        |     approved?
        |     /       \
        |   yes        no (up to 3x)
        |    |          |
        |    |    label: changes-requested
        |    |    [coding agent retries with PR review context]
        |    |          |
        |    |    3 failures → label: human-review-required
        |    |
        |    v  label: ready-to-merge
        |
        └── merge.sh  (crontab, offset 90s)
                 |
                 v
           gh pr merge --squash
                 |
                 v
           Issue closed
```

## How Agents Understand Context

Each agent CLI receives two layers of context before doing any work:

**1. Project context — `AGENTS.md`**

Place `AGENTS.md` at the root of your target repo. The dispatcher ensures it is present in every worktree. Agent CLIs read it automatically to understand:
- Project architecture and conventions
- Coding guidelines and patterns
- How to run tests
- What files to avoid touching

**2. Issue context — `ISSUE.md`**

Before spawning an agent, the dispatcher writes `ISSUE.md` into the worktree root containing:
- Issue number, title, and body
- All issue comments
- PR details (number, URL, review comments) — when a PR already exists for the branch

This means the agent always has full context: *what the project is* (AGENTS.md) + *what needs to be done right now* (ISSUE.md), without needing any extra flags or prompts.

## Pro Tip — Use Kiro SDD for Better Issues

For best results, write issues backed by structured specs. [Kiro](https://kiro.dev/) supports Spec-Driven Development (SDD) — it generates `requirements.md`, `design.md`, and `tasks.md` from a feature description.

When agents work from well-structured specs, they implement more accurately, write better tests, and consume fewer tokens. Place the generated spec files in `.kiro/specs/<feature>/` in your repo and reference them in the issue body.

## Quick Start

**1. Clone and configure**
```bash
git clone https://github.com/your-org/agentic-dev-loop.git
cd agentic-dev-loop
cp agents.example.yml agents.yml
# Edit agents.yml: set repo_path, workspace_base, state_base, and agent commands
```

**2. Install agent CLIs**
```bash
kiro-cli --version
claude --version
gh auth status
```

**3. Install agent skills**

Copy or symlink the skills into your agent CLI's skills directory so agents know how to behave as coder or reviewer:
```bash
# kiro-cli
cp -r skills/agentic-coder ~/.kiro/skills/
cp -r skills/agentic-reviewer ~/.kiro/skills/

# claude (adjust path to your claude skills dir)
cp -r skills/agentic-coder ~/.claude/skills/
cp -r skills/agentic-reviewer ~/.claude/skills/
```

Or symlink to keep them in sync with this repo:
```bash
ln -s $(pwd)/skills/agentic-coder ~/.kiro/skills/agentic-coder
ln -s $(pwd)/skills/agentic-reviewer ~/.kiro/skills/agentic-reviewer
```

**4. Add `AGENTS.md` to your target repo**
```bash
cp AGENTS.md /path/to/your/repo/AGENTS.md
# Edit it to describe your project structure and conventions
```

**5. Set up crontab**
```bash
crontab -e
# Paste contents of config/crontab.example (update paths first)
```

**6. Apply the `todo` label to a GitHub Issue and watch it go.**

## Label State Machine

```
todo → in-progress → pr-opened → reviewing → ready-to-merge → [merged]
                                           → changes-requested → in-progress (retry, max 3)
                                           → human-review-required
```

## Configuration (`agents.yml`)

See [`agents.example.yml`](agents.example.yml) for a full annotated example.

Key sections:
- `pipeline` — repo path, workspace and state directories
- `agents` — list of agent CLIs with role, command, concurrency settings, and optional `env` map
- `roles` — label transitions per role
- `notifications` — optional Telegram / Discord webhooks

### Per-agent credentials

Use the `env` field to give each agent its own GitHub token, so the coder and reviewer operate as separate GitHub accounts. This is required for the reviewer to be able to approve PRs opened by the coder:

```yaml
agents:
  - name: kiro-cli
    role: coding
    command: "kiro-cli --resume --agent senior --no-interactive"
    env:
      GH_TOKEN: "${CODER_GH_TOKEN}"    # GitHub account that opens PRs

  - name: claude
    role: review
    command: "claude --dangerously-skip-permissions -p '...'"
    env:
      GH_TOKEN: "${REVIEWER_GH_TOKEN}" # Separate GitHub account that approves PRs
```

The `env` map is merged into the subprocess environment at spawn time, overriding any existing variables with the same name. All values support `${VAR_NAME}` expansion.

## Observability

```bash
# Live dispatcher log
tail -f /tmp/agentic-loop.log

# Current state of all issues
./scripts/status.sh

# Full history of one issue
ls ~/.agent-pipeline/<repo>/state/issue-42/
```
