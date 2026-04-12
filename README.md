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

**2. Set up your target repo**

Run the setup script to create all required labels and configure merge settings:
```bash
./scripts/setup-repo.sh <owner/repo>
# Example: ./scripts/setup-repo.sh myorg/my-project
```

This creates the 7 pipeline labels and sets the repo to squash-merge only (matching `merge.sh`).

**3. Install agent CLIs**

Make sure the agent CLIs you configured are installed and authenticated:
```bash
kiro-cli --version
claude --version
gh auth status
```

**4. Set up Python environment**
```bash
cd /path/to/agentic-dev-loop
python3 -m venv .venv
.venv/bin/pip install pyyaml
```

**5. Create `.env` for credentials**

crontab does not load `~/.zshrc`, so environment variables must be in a `.env` file:
```bash
# .env (already in .gitignore)
export CODER_GH_TOKEN="ghp_..."
export REVIEWER_GH_TOKEN="ghp_..."
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
export DISCORD_WEBHOOK_URL="..."
```

For manual runs: `source .env && .venv/bin/python3 dispatcher.py`

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

Also install the [`gh-cli`](https://skills.sh/github/awesome-copilot/gh-cli) skill — it teaches agents how to use the GitHub CLI effectively for PR operations, issue management, and more:
```bash
# download and install gh-cli skill
gh skills install github/awesome-copilot/gh-cli   # or copy manually to your skills dir
```

> Skills load automatically when the prompt matches the skill's `description`. For this to work, skills must be declared in the agent's config file:
>
> **kiro-cli** — add to `~/.kiro/agents/<agent-name>.md`:
> ```markdown
> skills:
>   - agentic-coder      # or agentic-reviewer for review role
>   - conventional-commit
> ```
> **claude** — add to your agent profile's `skills:` list similarly.
>
> If skills don't auto-load, see the troubleshooting section in `AGENTS.md`.

**4. Add `AGENTS.md` to your target repo**

`AGENTS.md` is the project context file that agents read before doing any work. The easiest way to generate it is with kiro-cli's `/code summary` command — it analyses your codebase and produces a concise summary of the architecture, conventions, and key files:

```bash
cd /path/to/your/repo
kiro-cli
# inside kiro: /code summary
# save the output as AGENTS.md
```

Then review and edit it to add any project-specific conventions or rules you want agents to follow.

**5. Set up crontab**
```bash
crontab -e
# Paste contents of config/crontab.example (update paths first)
```

> `workspace_base` and `state_base` directories are created automatically by the dispatcher on first run — no need to `mkdir` them manually.

**6. Test notifications**

Before running the full pipeline, verify your Telegram/Discord setup:
```bash
source .env  # or export vars inline
.venv/bin/python3 scripts/test-notifications.py
```

**7. Apply the `todo` label to a GitHub Issue and watch it go.**

For manual testing (recommended before setting up crontab):
```bash
source .env && .venv/bin/python3 dispatcher.py
```

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

> ⚠️ The reviewer GitHub account must be added as a **collaborator with Write access** to the target repo, otherwise `gh pr review` will fail. Go to repo **Settings → Collaborators → Add people**.

## Observability

```bash
# Live dispatcher log
tail -f /tmp/agentic-loop.log

# Current state of all issues
./scripts/status.sh

# Full history of one issue
ls ~/.agent-pipeline/<repo>/state/issue-42/
```
