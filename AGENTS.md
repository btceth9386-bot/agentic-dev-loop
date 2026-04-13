# Agentic Loop — Setup Guide for AI Agents

This file is intended to be read by an AI agent (e.g. kiro-cli, claude) to help a user
set up and operate the agentic-dev-loop pipeline. Read this file fully before taking any action.

## What This Project Does

A local multi-agent CI/CD pipeline that autonomously handles GitHub Issues — from
implementation to review to merge — with no database, no server, and no webhooks.

## Project Structure

```
agentic-dev-loop/
  dispatcher.py          # Main orchestrator (stateless, run via crontab or manually)
  merge.sh               # Auto-merge script for ready-to-merge PRs
  agents.example.yml     # Config template — copy to agents.yml and fill in
  config/
    crontab.example      # Crontab setup reference
  scripts/
    setup-repo.sh        # Create GitHub labels and configure target repo
    rotate-role.sh       # Swap an agent's role in agents.yml
    monitor.sh            # Print current pipeline state
    test-notifications.py # Test Telegram/Discord notifications
  skills/
    agentic-coder/SKILL.md    # Skill for coding agents
    agentic-reviewer/SKILL.md # Skill for review agents
```

## Label State Machine

```
todo → in-progress → pr-opened → reviewing → ready-to-merge → [merged]
                                           → changes-requested → in-progress (retry, max 3)
                                           → human-review-required (after 3 failed attempts)
                   → agent-error  (agent failed — needs human intervention)
```

- `todo` — human applies to trigger pipeline
- `agent-error` — agent failed (API error, crash, etc.) — human must intervene

## Setup Steps (for AI agent to execute)

### 1. Copy and configure agents.yml

```bash
cp agents.example.yml agents.yml
```

Edit `agents.yml`:
- `pipeline.repo_path` — absolute path to the local clone of the target repo
- `pipeline.workspace_base` — e.g. `~/.agent-pipeline/<repo-name>/workspaces`
- `pipeline.state_base` — e.g. `~/.agent-pipeline/<repo-name>/state`
- `agents[].env.GH_TOKEN` — set `CODER_GH_TOKEN` and `REVIEWER_GH_TOKEN` in `.env`

### 2. Set up Python environment

```bash
python3 -m venv .venv
.venv/bin/pip install pyyaml
```

### 3. Create .env file

Copy the example and fill in your values:
```bash
cp .env.example .env
```

Three GitHub tokens are required (see `.env.example` for details):

| Token | Used by | Minimum GitHub scopes | Notes |
|-------|---------|----------------------|-------|
| `DISPATCHER_GH_TOKEN` | dispatcher.py | `repo` (issues r/w, PRs r/w) | Can be same as CODER or REVIEWER |
| `CODER_GH_TOKEN` | coding agents | `repo` (code r/w, PRs r/w, issues r) | Account that opens PRs |
| `REVIEWER_GH_TOKEN` | review agents | `repo` (PRs r/w) | Must be a **different** account from coder |

```bash
# .env (gitignored)
export DISPATCHER_GH_TOKEN="ghp_..."  # Dispatcher polling/labels (can be same as CODER or REVIEWER)
export CODER_GH_TOKEN="ghp_..."       # GitHub account that opens PRs
export REVIEWER_GH_TOKEN="ghp_..."    # Separate GitHub account that approves PRs
export TELEGRAM_BOT_TOKEN="..."       # Optional
export TELEGRAM_CHAT_ID="..."         # Optional
export DISCORD_WEBHOOK_URL="..."      # Optional
```

### 4. Set up target repo

```bash
source .env
./scripts/setup-repo.sh <owner/repo>
```

This creates all 8 pipeline labels and enables squash merge.

> ⚠️ **Important**: If coder and reviewer use different GitHub accounts, the reviewer account must be added as a collaborator with **Write** access to the target repo. Otherwise `gh pr review` will fail with a permission error.
>
> Go to: repo **Settings → Collaborators → Add people** → add the reviewer account.

### 5. Install skills into agent CLI directories

```bash
# kiro-cli
ln -s $(pwd)/skills/agentic-coder ~/.kiro/skills/agentic-coder
ln -s $(pwd)/skills/agentic-reviewer ~/.kiro/skills/agentic-reviewer

# claude
ln -s $(pwd)/skills/agentic-coder ~/.claude/skills/agentic-coder
ln -s $(pwd)/skills/agentic-reviewer ~/.claude/skills/agentic-reviewer
```

### 6. Add AGENTS.md to target repo

Generate with kiro-cli:
```bash
cd /path/to/target/repo
# run: /code summary inside kiro-cli, save output as AGENTS.md
```

Or copy a template and edit manually.

### 7. Add .gitignore to target repo

Must contain:
```
ISSUE.md
.kiro/
.claude/
.codex/
.copilot/
.gemini/
```

### 8. Test notifications

```bash
source .env && .venv/bin/python3 scripts/test-notifications.py
```

### 9. Run manually (recommended before crontab)

```bash
source .env && .venv/bin/python3 dispatcher.py
```

### 10. Set up crontab (after manual testing passes)

```bash
crontab -e
# paste contents of config/crontab.example (update paths first)
```

## Correct agents.yml Command Format

```yaml
agents:
  - name: kiro-cli
    role: coding
    command: "kiro-cli chat --resume --agent senior --no-interactive --trust-all-tools 'You are a coder. Read ISSUE.md first then begin'"
    max_concurrent: 2
    cooldown_minutes: 0
    env:
      GH_TOKEN: "${CODER_GH_TOKEN}"

  - name: claude
    role: review
    command: "claude --dangerously-skip-permissions --continue --model claude-sonnet-4-6 -p 'You are a code reviewer. Read ISSUE.md then begin.'"
    max_concurrent: 1
    cooldown_minutes: 0
    env:
      GH_TOKEN: "${REVIEWER_GH_TOKEN}"

roles:
  coding:
    pickup_label: "todo"
    label_on_start: "in-progress"
    label_on_done: "pr-opened"
  review:
    pickup_label: "pr-opened"
    label_on_start: "reviewing"
    label_on_done: "ready-to-merge"

notifications:
  telegram:
    token: "${TELEGRAM_BOT_TOKEN}"
    chat_id: "${TELEGRAM_CHAT_ID}"
  discord:
    webhook_url: "${DISCORD_WEBHOOK_URL}"
```

## Troubleshooting (for AI agent)

### Skills not auto-loading

Skills load automatically when the prompt matches the skill's `description`. For this to work, skills must be declared in the agent's config:

**kiro-cli** — add to `~/.kiro/agents/<agent-name>.md`:
```json
"resources": [
  "skill://~/.kiro/skills/**/SKILL.md"
]
```

**claude** — add skills to the agent profile's `skills:` list.

If skills still don't auto-load, explicitly reference the skill in the command:
```
'You are a coder. Read ISSUE.md first, then follow the agentic-coder skill to implement, commit, push, and open a PR.'
```

### Issue stuck in a label state

Check state log:
```bash
cat ~/.agent-pipeline/<repo>/state/issue-<N>/*.log
```

To re-enter pipeline, change label manually:
```bash
source .env
GH_TOKEN=$CODER_GH_TOKEN gh issue edit <N> --remove-label "<current>" --add-label "todo" --repo <owner/repo>
```

### agent-error label

Agent failed (API error, crash, quota exceeded). Read the state log for the error.
Fix the underlying issue (e.g. switch model, check token), then reset label to `todo` or `pr-opened`.

### Observability

```bash
tail -f /tmp/agentic-loop.log          # live dispatcher log
./scripts/monitor.sh                    # current state of all issues
ls ~/.agent-pipeline/<repo>/state/issue-<N>/  # full history of one issue
```
