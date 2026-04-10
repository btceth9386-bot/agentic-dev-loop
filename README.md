# Agentic Loop

A local multi-agent CI/CD pipeline that autonomously handles GitHub Issues — from implementation to review to merge — with no database, no server, and no webhooks required.

## Highlights

- **Zero infrastructure** — runs entirely on your local machine via crontab
- **No database** — GitHub labels are the single source of truth for issue state; survives restarts and crashes
- **Agent-agnostic** — works with any CLI-based agent (kiro-cli, claude, codex, or your own)
- **Parallel execution** — per-issue git worktrees keep agents fully isolated with no file conflicts
- **Self-healing** — stateless dispatcher resumes correctly after any crash or restart
- **Human-in-the-loop** — escalates to `human-review-required` after 3 failed review cycles
- **Notifications** — Telegram and/or Discord alerts at key state transitions

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

## Quick Start

**1. Clone and configure**
```bash
git clone https://github.com/your-org/agentic-dev-loop.git
cd agentic-dev-loop
cp agents.example.yml agents.yml
# Edit agents.yml: set repo_path, workspace_base, state_base, and agent commands
```

**2. Install agent CLIs**

Make sure the agent CLIs you configured are installed and authenticated:
```bash
# Examples
kiro-cli --version
claude --version
gh auth status
```

**3. Add `AGENTS.md` to your target repo**

The dispatcher injects `AGENTS.md` as project context for agent CLIs. Place it at the root of the repo being automated:
```bash
cp AGENTS.md /path/to/your/repo/AGENTS.md
# Edit it to describe your project structure and conventions
```

**4. Set up crontab**
```bash
crontab -e
# Paste contents of config/crontab.example (update paths first)
```

**5. Apply the `todo` label to a GitHub Issue and watch it go.**

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
- `agents` — list of agent CLIs with role, command, and concurrency settings
- `roles` — label transitions per role
- `notifications` — optional Telegram / Discord webhooks

## Observability

```bash
# Live dispatcher log
tail -f /tmp/agentic-loop.log

# Current state of all issues
./scripts/status.sh

# Full history of one issue
ls ~/.agent-pipeline/<repo>/state/issue-42/
```

## Requirements

- macOS or Linux
- Python 3.10+
- [GitHub CLI (`gh`)](https://cli.github.com/) — authenticated
- Agent CLIs as configured in `agents.yml`
