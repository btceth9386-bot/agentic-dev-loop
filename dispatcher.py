#!/usr/bin/env python3
"""Agentic Loop Dispatcher — main orchestrator."""

import logging
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
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
        agent.setdefault("env", {})

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
    "ready-to-merge", "changes-requested", "human-review-required", "agent-error",
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


def fetch_pr_context(issue_number, repo_path):
    """Return formatted PR markdown string, or None if no PR exists for agent/issue-<N>."""
    import json
    result = _gh(
        ["pr", "list", "--head", f"agent/issue-{issue_number}", "--json", "number,url,title", "--limit", "1"],
        repo_path,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    prs = json.loads(result.stdout)
    if not prs:
        return None
    pr = prs[0]
    detail = _gh(["pr", "view", str(pr["number"]), "--json", "number,url,title,reviews,comments"], repo_path)
    if detail.returncode != 0:
        return None
    d = json.loads(detail.stdout)
    lines = [
        "---",
        f"# Pull Request #{d['number']}",
        "## URL",
        d.get("url", ""),
        "## Title",
        d.get("title", ""),
        "## Review Comments",
    ]
    for review in d.get("reviews", []):
        author = review.get("author", {}).get("login", "unknown")
        body = review.get("body", "").strip()
        if body:
            lines += [f"### Review by {author}", "", body, ""]
    for comment in d.get("comments", []):
        author = comment.get("author", {}).get("login", "unknown")
        lines += [f"### Comment by {author}", "", comment.get("body", ""), ""]
    return "\n".join(lines)


def pr_exists(issue_number, repo_path):
    """Return True if a PR exists for agent/issue-<N> branch."""
    import json
    result = _gh(
        ["pr", "list", "--head", f"agent/issue-{issue_number}", "--json", "number", "--limit", "1"],
        repo_path,
    )
    if result.returncode != 0:
        return False
    prs = json.loads(result.stdout or "[]")
    return len(prs) > 0


def pr_is_approved(issue_number, repo_path):
    """Return True if the PR for agent/issue-<N> has at least one APPROVED review."""
    import json
    result = _gh(
        ["pr", "list", "--head", f"agent/issue-{issue_number}", "--json", "number", "--limit", "1"],
        repo_path,
    )
    if result.returncode != 0:
        return False
    prs = json.loads(result.stdout or "[]")
    if not prs:
        return False
    detail = _gh(["pr", "view", str(prs[0]["number"]), "--json", "reviews"], repo_path)
    if detail.returncode != 0:
        return False
    reviews = json.loads(detail.stdout).get("reviews", [])
    return any(r.get("state") == "APPROVED" for r in reviews)


def pr_has_review_comments(issue_number, repo_path):
    """Return True if the PR for agent/issue-<N> has review comments or change-request reviews."""
    import json
    result = _gh(
        ["pr", "list", "--head", f"agent/issue-{issue_number}", "--json", "number", "--limit", "1"],
        repo_path,
    )
    if result.returncode != 0:
        return False
    prs = json.loads(result.stdout or "[]")
    if not prs:
        return False
    detail = _gh(["pr", "view", str(prs[0]["number"]), "--json", "reviews,comments"], repo_path)
    if detail.returncode != 0:
        return False
    data = json.loads(detail.stdout)
    has_changes_requested = any(r.get("state") == "CHANGES_REQUESTED" for r in data.get("reviews", []))
    has_comments = len(data.get("comments", [])) > 0
    return has_changes_requested or has_comments


def post_assignment_comment(issue_number, agent_name, role, attempt, repo_path, is_retry=False):
    label = "retry attempt" if is_retry else "attempt"
    body = f"🤖 Assigned to **{agent_name}** ({role}) — {label} {attempt}"
    _gh(["issue", "comment", str(issue_number), "--body", body], repo_path)


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
        ["git", "worktree", "add", "-b", f"agent/issue-{issue_number}", str(workspace), "origin/main"],
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


def write_issue_context(config, issue_number, context_text, pr_context=None):
    workspace = _workspace_path(config, issue_number)
    content = context_text
    if pr_context:
        content = content + "\n\n" + pr_context
    (workspace / "ISSUE.md").write_text(content)


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
    env = {**os.environ, **agent.get("env", {})}
    result = subprocess.run(
        shlex.split(agent["command"]),
        cwd=str(workspace_path),
        capture_output=True,
        text=True,
        env=env,
    )
    return result


# ---------------------------------------------------------------------------
# State logging
# ---------------------------------------------------------------------------

def _state_dir(config, issue_number):
    base = Path(os.path.expanduser(config["pipeline"]["state_base"]))
    d = base / f"issue-{issue_number}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_next_log_index(config, issue_number):
    d = _state_dir(config, issue_number)
    existing = sorted(d.glob("*.log"))
    if not existing:
        return 1
    last = existing[-1].name
    return int(last.split("-")[0]) + 1


def get_attempt_count(config, issue_number):
    d = _state_dir(config, issue_number)
    return len(list(d.glob("*-changes-requested.log")))


def write_state_log(config, issue_number, agent_name, role, prev_state, curr_state, attempt, stdout="", stderr=""):
    d = _state_dir(config, issue_number)
    idx = get_next_log_index(config, issue_number)
    log_file = d / f"{idx:02d}-{curr_state}.log"
    content = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "issue": issue_number,
        "agent": agent_name,
        "role": role,
        "prev_state": prev_state,
        "curr_state": curr_state,
        "attempt": attempt,
        "stdout": stdout,
        "stderr": stderr,
    }
    log_file.write_text(yaml.dump(content, allow_unicode=True))

    # Update current/ symlink
    current_dir = Path(os.path.expanduser(config["pipeline"]["state_base"])).parent / "current"
    current_dir.mkdir(parents=True, exist_ok=True)
    symlink = current_dir / f"issue-{issue_number}"
    symlink.unlink(missing_ok=True)
    symlink.symlink_to(log_file)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

NOTIFY_ON = {"changes-requested", "ready-to-merge", "human-review-required", "in-progress", "pr-opened", "agent-error"}


def notify(config, message, state=None):
    if state and state not in NOTIFY_ON:
        return
    notif = config.get("notifications", {})
    _notify_telegram(notif.get("telegram", {}), message)
    _notify_discord(notif.get("discord", {}), message)


def _notify_telegram(cfg, message):
    token = cfg.get("token")
    chat_id = cfg.get("chat_id")
    if not token or not chat_id:
        return
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10)
    except Exception as e:
        log.warning("Telegram notify failed: %s", e)


def _notify_discord(cfg, message):
    webhook_url = cfg.get("webhook_url")
    if not webhook_url:
        return
    import json as _json
    data = _json.dumps({"content": message}).encode()
    req = urllib.request.Request(webhook_url, data=data, headers={
        "Content-Type": "application/json",
        "User-Agent": "agentic-loop/1.0",
    })
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.warning("Discord notify failed: %s", e)


# ---------------------------------------------------------------------------
# Main orchestration loop
# ---------------------------------------------------------------------------

def process_issue(config, issue, role_name, role_cfg):
    issue_number = issue["number"]
    pickup_label = role_cfg["pickup_label"]
    label_on_start = role_cfg["label_on_start"]
    label_on_done = role_cfg["label_on_done"]
    repo_path = config["pipeline"]["repo_path"]

    log.info("Picking up issue #%s for role '%s'", issue_number, role_name)

    # Transition label to in-progress/reviewing
    if not transition_label(issue_number, pickup_label, label_on_start, repo_path):
        return

    attempt = get_attempt_count(config, issue_number) + 1

    # Workspace
    try:
        workspace = create_workspace(config, issue_number)
    except Exception as e:
        log.error("Workspace creation failed for #%s: %s", issue_number, e)
        return

    # Write ISSUE.md
    try:
        context = fetch_issue_context(issue_number, repo_path)
        pr_context = fetch_pr_context(issue_number, repo_path)
        write_issue_context(config, issue_number, context, pr_context)
    except Exception as e:
        log.error("Failed to write ISSUE.md for #%s: %s", issue_number, e)

    # Pick agent
    agent = pick_agent(config, role_name)
    if not agent:
        log.warning("No available agent for role '%s', skipping #%s", role_name, issue_number)
        return

    if not acquire_lock(agent["name"], issue_number):
        log.warning("Could not acquire lock for agent %s issue #%s", agent["name"], issue_number)
        return

    try:
        post_assignment_comment(issue_number, agent["name"], role_name, attempt, repo_path, is_retry=(attempt > 1))
        log.info("Running agent '%s' on issue #%s", agent["name"], issue_number)
        result = run_agent(agent, workspace)
        success = result.returncode == 0

        if role_name == "coding":
            if success or pr_exists(issue_number, repo_path):
                curr_state = label_on_done
                transition_label(issue_number, label_on_start, label_on_done, repo_path)
                notify(config, f"✅ PR opened for issue #{issue_number} by {agent['name']}", label_on_done)
            else:
                curr_state = "agent-error"
                transition_label(issue_number, label_on_start, "agent-error", repo_path)
                error_summary = (result.stderr or result.stdout or "no output")[:500]
                log.error("Coding agent failed for #%s: %s", issue_number, error_summary)
                notify(config, f"🚨 Agent error on issue #{issue_number} ({agent['name']})\n```\n{error_summary}\n```", "agent-error")

        elif role_name == "review":
            if success and pr_is_approved(issue_number, repo_path):
                curr_state = "ready-to-merge"
                transition_label(issue_number, label_on_start, "ready-to-merge", repo_path)
                notify(config, f"✅ PR approved for issue #{issue_number}, ready to merge", "ready-to-merge")
            else:
                if not pr_has_review_comments(issue_number, repo_path):
                    curr_state = "agent-error"
                    transition_label(issue_number, label_on_start, "agent-error", repo_path)
                    error_summary = (result.stderr or result.stdout or "no output")[:500]
                    log.warning("Review agent failed with no review comments for #%s: %s", issue_number, error_summary)
                    notify(config, f"🚨 Agent error on issue #{issue_number} ({agent['name']})\n```\n{error_summary}\n```", "agent-error")
                else:
                    curr_state = "changes-requested"
                    transition_label(issue_number, label_on_start, "changes-requested", repo_path)
                    notify(config, f"🔄 Changes requested for issue #{issue_number} (attempt {attempt})", "changes-requested")

                    if attempt >= 3:
                        transition_label(issue_number, "changes-requested", "human-review-required", repo_path)
                        cleanup_workspace(config, issue_number)
                        notify(config, f"🚨 Issue #{issue_number} escalated to human review after {attempt} attempts", "human-review-required")
                        curr_state = "human-review-required"

        write_state_log(
            config, issue_number, agent["name"], role_name,
            label_on_start, curr_state, attempt,
            result.stdout, result.stderr,
        )

    finally:
        release_lock(agent["name"], issue_number)


def cleanup_merged_workspaces(config):
    """Cleanup workspaces for issues that have been closed/merged."""
    import json
    base = Path(os.path.expanduser(config["pipeline"]["workspace_base"]))
    if not base.exists():
        return
    repo_path = config["pipeline"]["repo_path"]
    for workspace in base.iterdir():
        if not workspace.name.startswith("issue-"):
            continue
        issue_number = int(workspace.name.split("-")[1])
        result = _gh(["issue", "view", str(issue_number), "--json", "state"], repo_path)
        if result.returncode != 0:
            continue
        state = json.loads(result.stdout).get("state", "")
        if state == "CLOSED":
            log.info("Issue #%s is closed, cleaning up workspace", issue_number)
            cleanup_workspace(config, issue_number)
            notify(config, f"🧹 Issue #{issue_number} workspace cleaned up after merge", "ready-to-merge")
    issue_number = issue["number"]
    pickup_label = role_cfg["pickup_label"]
    label_on_start = role_cfg["label_on_start"]
    label_on_done = role_cfg["label_on_done"]
    repo_path = config["pipeline"]["repo_path"]

    log.info("Picking up issue #%s for role '%s'", issue_number, role_name)

    # Transition label to in-progress/reviewing
    if not transition_label(issue_number, pickup_label, label_on_start, repo_path):
        return

    attempt = get_attempt_count(config, issue_number) + 1

    # Workspace
    try:
        workspace = create_workspace(config, issue_number)
    except Exception as e:
        log.error("Workspace creation failed for #%s: %s", issue_number, e)
        return

    # Write ISSUE.md
    try:
        context = fetch_issue_context(issue_number, repo_path)
        pr_context = fetch_pr_context(issue_number, repo_path)
        write_issue_context(config, issue_number, context, pr_context)
    except Exception as e:
        log.error("Failed to write ISSUE.md for #%s: %s", issue_number, e)

    # Pick agent
    agent = pick_agent(config, role_name)
    if not agent:
        log.warning("No available agent for role '%s', skipping #%s", role_name, issue_number)
        return

    if not acquire_lock(agent["name"], issue_number):
        log.warning("Could not acquire lock for agent %s issue #%s", agent["name"], issue_number)
        return

    try:
        post_assignment_comment(issue_number, agent["name"], role_name, attempt, repo_path, is_retry=(attempt > 1))
        log.info("Running agent '%s' on issue #%s", agent["name"], issue_number)
        result = run_agent(agent, workspace)
        success = result.returncode == 0

        if role_name == "coding":
            if success or pr_exists(issue_number, repo_path):
                curr_state = label_on_done
                transition_label(issue_number, label_on_start, label_on_done, repo_path)
                notify(config, f"✅ PR opened for issue #{issue_number} by {agent['name']}", label_on_done)
            else:
                curr_state = "agent-error"
                transition_label(issue_number, label_on_start, "agent-error", repo_path)
                error_summary = (result.stderr or result.stdout or "no output")[:500]
                log.error("Coding agent failed for #%s: %s", issue_number, error_summary)
                notify(config, f"🚨 Agent error on issue #{issue_number} ({agent['name']})\n```\n{error_summary}\n```", "agent-error")

        elif role_name == "review":
            if success and pr_is_approved(issue_number, repo_path):
                curr_state = "ready-to-merge"
                transition_label(issue_number, label_on_start, "ready-to-merge", repo_path)
                notify(config, f"✅ PR approved for issue #{issue_number}, ready to merge", "ready-to-merge")
            else:
                if not pr_has_review_comments(issue_number, repo_path):
                    curr_state = "agent-error"
                    transition_label(issue_number, label_on_start, "agent-error", repo_path)
                    error_summary = (result.stderr or result.stdout or "no output")[:500]
                    log.warning("Review agent failed with no review comments for #%s: %s", issue_number, error_summary)
                    notify(config, f"🚨 Agent error on issue #{issue_number} ({agent['name']})\n```\n{error_summary}\n```", "agent-error")
                else:
                    curr_state = "changes-requested"
                    transition_label(issue_number, label_on_start, "changes-requested", repo_path)
                    notify(config, f"🔄 Changes requested for issue #{issue_number} (attempt {attempt})", "changes-requested")

                    if attempt >= 3:
                        transition_label(issue_number, "changes-requested", "human-review-required", repo_path)
                        cleanup_workspace(config, issue_number)
                        notify(config, f"🚨 Issue #{issue_number} escalated to human review after {attempt} attempts", "human-review-required")
                        curr_state = "human-review-required"

        write_state_log(
            config, issue_number, agent["name"], role_name,
            label_on_start, curr_state, attempt,
            result.stdout, result.stderr,
        )

    finally:
        release_lock(agent["name"], issue_number)


def main():
    config = load_config()
    repo_path = config["pipeline"]["repo_path"]

    try:
        validate_gitignore(repo_path)
    except Exception as e:
        log.error("gitignore validation failed: %s", e)
        sys.exit(1)

    roles = config.get("roles", {})

    cleanup_merged_workspaces(config)

    for role_name, role_cfg in roles.items():
        capacity = calculate_role_capacity(config, role_name)
        if capacity <= 0:
            log.info("No capacity for role '%s', skipping", role_name)
            continue

        issues = poll_issues(role_cfg["pickup_label"], repo_path)
        log.info("Role '%s': %d issue(s) available, capacity %d", role_name, len(issues), capacity)

        for issue in issues[:capacity]:
            try:
                process_issue(config, issue, role_name, role_cfg)
            except Exception as e:
                log.error("Error processing issue #%s: %s", issue.get("number"), e)


if __name__ == "__main__":
    main()
