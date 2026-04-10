#!/usr/bin/env python3
"""Agentic Loop Dispatcher — main orchestrator."""

import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REQUIRED_GITIGNORE_ENTRIES = ["ISSUE.md", ".kiro/", ".claude/", ".codex/", ".copilot/", ".gemini/"]
AGENTS_YML = Path(__file__).parent / "agents.yml"


def _expand_env_vars(value):
    """Recursively expand ${VAR} in strings; raise if var not set."""
    if isinstance(value, str):
        def replacer(m):
            var = m.group(1)
            if var not in os.environ:
                raise ValueError(f"Environment variable '{var}' is not set (referenced in agents.yml)")
            return os.environ[var]
        return re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', replacer, value)
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(i) for i in value]
    return value


def load_config(path=AGENTS_YML):
    with open(path) as f:
        raw = yaml.safe_load(f)

    config = _expand_env_vars(raw)

    # Validate agents
    for agent in config.get("agents", []):
        for field in ("name", "role", "command", "max_concurrent"):
            if field not in agent:
                raise ValueError(f"Agent missing required field '{field}': {agent}")
        agent.setdefault("cooldown_minutes", 0)

    # Validate roles
    for role_name, role in config.get("roles", {}).items():
        for field in ("pickup_label", "label_on_start", "label_on_done"):
            if field not in role:
                raise ValueError(f"Role '{role_name}' missing required field '{field}'")

    return config


def validate_gitignore(repo_path):
    gitignore = Path(repo_path) / ".gitignore"
    if not gitignore.exists():
        raise FileNotFoundError(f".gitignore not found at {repo_path}")

    content = gitignore.read_text()
    missing = [e for e in REQUIRED_GITIGNORE_ENTRIES if e not in content]
    if missing:
        raise ValueError(f".gitignore is missing required entries: {missing}")

    if not (Path(repo_path) / "AGENTS.md").exists():
        log.warning("⚠️ AGENTS.md not found in repo_path — agent CLIs may lack project context.")


# ---------------------------------------------------------------------------
# GitHub interaction
# ---------------------------------------------------------------------------

STATE_LABELS = {
    "todo", "in-progress", "pr-opened", "reviewing",
    "ready-to-merge", "changes-requested", "human-review-required",
}


def _gh(args, repo_path, **kwargs):
    return subprocess.run(
        ["gh"] + args,
        cwd=repo_path,
        capture_output=True,
        text=True,
        **kwargs,
    )


def poll_issues(label, repo_path):
    """Return list of issues with `label` that carry no other state label."""
    import json
    result = _gh(["issue", "list", "--label", label, "--json", "number,title,labels", "--limit", "50"], repo_path)
    if result.returncode != 0:
        log.error("gh issue list failed: %s", result.stderr)
        return []
    issues = json.loads(result.stdout or "[]")
    filtered = []
    for issue in issues:
        issue_labels = {lbl["name"] for lbl in issue.get("labels", [])}
        other_state = issue_labels & STATE_LABELS - {label}
        if not other_state:
            filtered.append(issue)
    return filtered


def transition_label(issue_number, old_label, new_label, repo_path):
    result = _gh(
        ["issue", "edit", str(issue_number), "--remove-label", old_label, "--add-label", new_label],
        repo_path,
    )
    if result.returncode != 0:
        log.error("Label transition failed for #%s: %s", issue_number, result.stderr)
        return False
    return True


def fetch_issue_context(issue_number, repo_path):
    import json
    result = _gh(["issue", "view", str(issue_number), "--json", "title,body,comments"], repo_path)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to fetch issue #{issue_number}: {result.stderr}")
    data = json.loads(result.stdout)
    lines = [
        f"# Issue #{issue_number}: {data['title']}",
        "",
        data.get("body", ""),
        "",
    ]
    for comment in data.get("comments", []):
        lines += [f"## Comment by {comment.get('author', {}).get('login', 'unknown')}", "", comment.get("body", ""), ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Lockfile
# ---------------------------------------------------------------------------

LOCK_DIR = Path("/tmp")
STALE_SECONDS = 30 * 60


def _lock_path(agent_name, issue_number):
    return LOCK_DIR / f"agent-{agent_name}-{issue_number}.lock"


def acquire_lock(agent_name, issue_number):
    path = _lock_path(agent_name, issue_number)
    if path.exists():
        return False
    path.write_text(f"{int(time.time())}:{issue_number}")
    return True


def release_lock(agent_name, issue_number):
    _lock_path(agent_name, issue_number).unlink(missing_ok=True)


def get_active_locks(agent_name):
    locks = []
    for p in LOCK_DIR.glob(f"agent-{agent_name}-*.lock"):
        try:
            ts, issue = p.read_text().split(":")
            if time.time() - int(ts) < STALE_SECONDS:
                locks.append(p)
            else:
                p.unlink(missing_ok=True)  # stale
        except Exception:
            p.unlink(missing_ok=True)
    return locks


def count_active_locks(agent_name):
    return len(get_active_locks(agent_name))


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------

def _workspace_path(config, issue_number):
    base = os.path.expanduser(config["pipeline"]["workspace_base"])
    return Path(base) / f"issue-{issue_number}"


def create_workspace(config, issue_number):
    workspace = _workspace_path(config, issue_number)
    if workspace.exists():
        return workspace
    workspace.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "worktree", "add", "-b", f"agent/issue-{issue_number}", str(workspace), "main"],
        cwd=config["pipeline"]["repo_path"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {result.stderr}")
    return workspace


def cleanup_workspace(config, issue_number):
    workspace = _workspace_path(config, issue_number)
    repo_path = config["pipeline"]["repo_path"]
    subprocess.run(
        ["git", "worktree", "remove", str(workspace), "--force"],
        cwd=repo_path, capture_output=True,
    )
    subprocess.run(
        ["git", "branch", "-d", f"agent/issue-{issue_number}"],
        cwd=repo_path, capture_output=True,
    )
    current = Path(os.path.expanduser(config["pipeline"]["state_base"])).parent / "current" / f"issue-{issue_number}"
    current.unlink(missing_ok=True)


def write_issue_context(config, issue_number, context_text):
    workspace = _workspace_path(config, issue_number)
    (workspace / "ISSUE.md").write_text(context_text)


# ---------------------------------------------------------------------------
# Agent selection & execution
# ---------------------------------------------------------------------------

# Simple round-robin counter per role
_rr_index: dict[str, int] = {}


def calculate_role_capacity(config, role):
    agents = [a for a in config["agents"] if a["role"] == role]
    total = sum(a["max_concurrent"] for a in agents)
    used = sum(count_active_locks(a["name"]) for a in agents)
    return max(0, total - used)


def _in_cooldown(agent):
    cooldown = agent.get("cooldown_minutes", 0)
    if not cooldown:
        return False
    marker = LOCK_DIR / f"agent-{agent['name']}.cooldown"
    if not marker.exists():
        return False
    elapsed = (time.time() - marker.stat().st_mtime) / 60
    if elapsed >= cooldown:
        marker.unlink(missing_ok=True)
        return False
    return True


def pick_agent(config, role):
    candidates = [
        a for a in config["agents"]
        if a["role"] == role
        and count_active_locks(a["name"]) < a["max_concurrent"]
        and not _in_cooldown(a)
    ]
    if not candidates:
        return None
    idx = _rr_index.get(role, 0) % len(candidates)
    _rr_index[role] = idx + 1
    return candidates[idx]


def run_agent(agent, workspace_path):
    import shlex
    result = subprocess.run(
        shlex.split(agent["command"]),
        cwd=str(workspace_path),
        capture_output=True,
        text=True,
    )
    return result


