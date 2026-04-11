"""Unit tests for dispatcher.py"""

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

import dispatcher as d


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_repo(tmp_path):
    """Minimal repo dir with .gitignore and AGENTS.md."""
    gi = tmp_path / ".gitignore"
    gi.write_text("\n".join(d.REQUIRED_GITIGNORE_ENTRIES) + "\n")
    (tmp_path / "AGENTS.md").write_text("# Agents")
    return tmp_path


@pytest.fixture
def base_config(tmp_path):
    return {
        "pipeline": {
            "repo_path": str(tmp_path / "repo"),
            "workspace_base": str(tmp_path / "workspaces"),
            "state_base": str(tmp_path / "state"),
        },
        "agents": [
            {"name": "kiro", "role": "coding", "command": "echo done", "max_concurrent": 2, "cooldown_minutes": 0},
            {"name": "claude", "role": "review", "command": "echo reviewed", "max_concurrent": 1, "cooldown_minutes": 0},
        ],
        "roles": {
            "coding": {"pickup_label": "todo", "label_on_start": "in-progress", "label_on_done": "pr-opened"},
            "review": {"pickup_label": "pr-opened", "label_on_start": "reviewing", "label_on_done": "ready-to-merge"},
        },
        "notifications": {},
    }


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

def test_load_config_valid(tmp_path):
    cfg = {
        "pipeline": {"repo_path": "/repo", "workspace_base": "/ws", "state_base": "/state"},
        "agents": [{"name": "a", "role": "coding", "command": "cmd", "max_concurrent": 1}],
        "roles": {"coding": {"pickup_label": "todo", "label_on_start": "in-progress", "label_on_done": "pr-opened"}},
    }
    p = tmp_path / "agents.yml"
    p.write_text(yaml.dump(cfg))
    result = d.load_config(p)
    assert result["agents"][0]["cooldown_minutes"] == 0  # default applied


def test_load_config_missing_agent_field(tmp_path):
    cfg = {
        "pipeline": {"repo_path": "/r", "workspace_base": "/w", "state_base": "/s"},
        "agents": [{"name": "a", "role": "coding", "max_concurrent": 1}],  # missing command
        "roles": {},
    }
    p = tmp_path / "agents.yml"
    p.write_text(yaml.dump(cfg))
    with pytest.raises(ValueError, match="command"):
        d.load_config(p)


def test_load_config_missing_role_field(tmp_path):
    cfg = {
        "pipeline": {"repo_path": "/r", "workspace_base": "/w", "state_base": "/s"},
        "agents": [],
        "roles": {"coding": {"pickup_label": "todo", "label_on_start": "in-progress"}},  # missing label_on_done
    }
    p = tmp_path / "agents.yml"
    p.write_text(yaml.dump(cfg))
    with pytest.raises(ValueError, match="label_on_done"):
        d.load_config(p)


def test_load_config_env_var_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "secret123")
    cfg = {
        "pipeline": {"repo_path": "/r", "workspace_base": "/w", "state_base": "/s"},
        "agents": [],
        "roles": {},
        "notifications": {"telegram": {"token": "${MY_TOKEN}"}},
    }
    p = tmp_path / "agents.yml"
    p.write_text(yaml.dump(cfg))
    result = d.load_config(p)
    assert result["notifications"]["telegram"]["token"] == "secret123"


def test_load_config_env_var_not_set(tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    cfg = {
        "pipeline": {"repo_path": "/r", "workspace_base": "/w", "state_base": "/s"},
        "agents": [],
        "roles": {},
        "notifications": {"discord": {"webhook_url": "${MISSING_VAR}"}},
    }
    p = tmp_path / "agents.yml"
    p.write_text(yaml.dump(cfg))
    with pytest.raises(ValueError, match="MISSING_VAR"):
        d.load_config(p)


def test_load_config_no_expansion_without_braces(tmp_path):
    cfg = {
        "pipeline": {"repo_path": "/r", "workspace_base": "/w", "state_base": "/s"},
        "agents": [],
        "roles": {},
        "notifications": {"discord": {"webhook_url": "https://example.com/$plain"}},
    }
    p = tmp_path / "agents.yml"
    p.write_text(yaml.dump(cfg))
    result = d.load_config(p)
    assert result["notifications"]["discord"]["webhook_url"] == "https://example.com/$plain"


# ---------------------------------------------------------------------------
# validate_gitignore
# ---------------------------------------------------------------------------

def test_validate_gitignore_ok(tmp_repo):
    d.validate_gitignore(str(tmp_repo))  # should not raise


def test_validate_gitignore_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        d.validate_gitignore(str(tmp_path))


def test_validate_gitignore_missing_entries(tmp_path):
    (tmp_path / ".gitignore").write_text("ISSUE.md\n")
    with pytest.raises(ValueError, match="missing required entries"):
        d.validate_gitignore(str(tmp_path))


def test_validate_gitignore_no_agents_md_warns(tmp_path, caplog):
    gi = tmp_path / ".gitignore"
    gi.write_text("\n".join(d.REQUIRED_GITIGNORE_ENTRIES) + "\n")
    # No AGENTS.md
    import logging
    with caplog.at_level(logging.WARNING):
        d.validate_gitignore(str(tmp_path))
    assert "AGENTS.md" in caplog.text


# ---------------------------------------------------------------------------
# poll_issues
# ---------------------------------------------------------------------------

def _mock_run(stdout, returncode=0):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = ""
    return r


def test_poll_issues_filters_other_state_labels():
    issues = [
        {"number": 1, "title": "A", "labels": [{"name": "todo"}]},
        {"number": 2, "title": "B", "labels": [{"name": "todo"}, {"name": "in-progress"}]},
    ]
    with patch("dispatcher.subprocess.run", return_value=_mock_run(json.dumps(issues))):
        result = d.poll_issues("todo", "/repo")
    assert len(result) == 1
    assert result[0]["number"] == 1


def test_poll_issues_gh_failure():
    with patch("dispatcher.subprocess.run", return_value=_mock_run("", returncode=1)):
        result = d.poll_issues("todo", "/repo")
    assert result == []


# ---------------------------------------------------------------------------
# transition_label
# ---------------------------------------------------------------------------

def test_transition_label_success():
    with patch("dispatcher.subprocess.run", return_value=_mock_run("")) as mock:
        result = d.transition_label(42, "todo", "in-progress", "/repo")
    assert result is True
    cmd = mock.call_args[0][0]
    assert "--remove-label" in cmd and "todo" in cmd
    assert "--add-label" in cmd and "in-progress" in cmd


def test_transition_label_failure():
    with patch("dispatcher.subprocess.run", return_value=_mock_run("", returncode=1)):
        result = d.transition_label(42, "todo", "in-progress", "/repo")
    assert result is False


# ---------------------------------------------------------------------------
# fetch_issue_context
# ---------------------------------------------------------------------------

def test_fetch_issue_context_format():
    data = {"title": "Fix bug", "body": "Details here", "comments": [
        {"author": {"login": "alice"}, "body": "LGTM"}
    ]}
    with patch("dispatcher.subprocess.run", return_value=_mock_run(json.dumps(data))):
        result = d.fetch_issue_context(42, "/repo")
    assert "# Issue #42: Fix bug" in result
    assert "Details here" in result
    assert "## Comment by alice" in result
    assert "LGTM" in result


# ---------------------------------------------------------------------------
# Lockfile
# ---------------------------------------------------------------------------

def test_acquire_and_release_lock(tmp_path):
    with patch.object(d, "LOCK_DIR", tmp_path):
        assert d.acquire_lock("kiro", 1) is True
        assert d.acquire_lock("kiro", 1) is False  # already locked
        d.release_lock("kiro", 1)
        assert d.acquire_lock("kiro", 1) is True  # released, can re-acquire
        d.release_lock("kiro", 1)


def test_stale_lock_auto_released(tmp_path):
    with patch.object(d, "LOCK_DIR", tmp_path):
        lock = tmp_path / "agent-kiro-99.lock"
        old_ts = int(time.time()) - d.STALE_SECONDS - 1
        lock.write_text(f"{old_ts}:99")
        assert d.count_active_locks("kiro") == 0
        assert not lock.exists()


def test_count_active_locks_multiple(tmp_path):
    with patch.object(d, "LOCK_DIR", tmp_path):
        d.acquire_lock("kiro", 1)
        d.acquire_lock("kiro", 2)
        assert d.count_active_locks("kiro") == 2
        d.release_lock("kiro", 1)
        d.release_lock("kiro", 2)


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------

def test_create_workspace_reuses_existing(tmp_path, base_config):
    ws = Path(base_config["pipeline"]["workspace_base"]) / "issue-5"
    ws.mkdir(parents=True)
    with patch("dispatcher.subprocess.run") as mock:
        result = d.create_workspace(base_config, 5)
    mock.assert_not_called()
    assert result == ws


def test_create_workspace_calls_git_worktree(tmp_path, base_config):
    with patch("dispatcher.subprocess.run", return_value=_mock_run("")) as mock:
        d.create_workspace(base_config, 10)
    cmd = mock.call_args[0][0]
    assert "worktree" in cmd and "add" in cmd
    assert "agent/issue-10" in cmd
    assert "origin/main" in cmd


def test_write_issue_context(tmp_path, base_config):
    ws = Path(base_config["pipeline"]["workspace_base"]) / "issue-7"
    ws.mkdir(parents=True)
    d.write_issue_context(base_config, 7, "# Issue #7")
    assert (ws / "ISSUE.md").read_text() == "# Issue #7"


# ---------------------------------------------------------------------------
# Agent selection
# ---------------------------------------------------------------------------

def test_pick_agent_round_robin(tmp_path, base_config):
    # Two coding agents
    base_config["agents"].append(
        {"name": "codex", "role": "coding", "command": "codex", "max_concurrent": 1, "cooldown_minutes": 0}
    )
    with patch.object(d, "LOCK_DIR", tmp_path):
        d._rr_index.clear()
        a1 = d.pick_agent(base_config, "coding")
        a2 = d.pick_agent(base_config, "coding")
        assert a1["name"] != a2["name"]


def test_pick_agent_skips_full(tmp_path, base_config):
    with patch.object(d, "LOCK_DIR", tmp_path):
        # Fill kiro's slots
        d.acquire_lock("kiro", 1)
        d.acquire_lock("kiro", 2)
        agent = d.pick_agent(base_config, "coding")
    assert agent is None  # only one coding agent and it's full


def test_calculate_role_capacity(tmp_path, base_config):
    with patch.object(d, "LOCK_DIR", tmp_path):
        assert d.calculate_role_capacity(base_config, "coding") == 2
        d.acquire_lock("kiro", 1)
        assert d.calculate_role_capacity(base_config, "coding") == 1
        d.release_lock("kiro", 1)


# ---------------------------------------------------------------------------
# State logging
# ---------------------------------------------------------------------------

def test_write_state_log_creates_file(tmp_path, base_config):
    d.write_state_log(base_config, 42, "kiro", "coding", "in-progress", "pr-opened", 1, "out", "err")
    state_dir = Path(base_config["pipeline"]["state_base"]) / "issue-42"
    logs = list(state_dir.glob("*.log"))
    assert len(logs) == 1
    content = yaml.safe_load(logs[0].read_text())
    assert content["curr_state"] == "pr-opened"
    assert content["stdout"] == "out"


def test_get_next_log_index_sequential(tmp_path, base_config):
    d.write_state_log(base_config, 1, "kiro", "coding", "todo", "in-progress", 1)
    d.write_state_log(base_config, 1, "kiro", "coding", "in-progress", "pr-opened", 1)
    assert d.get_next_log_index(base_config, 1) == 3


def test_get_attempt_count(tmp_path, base_config):
    d.write_state_log(base_config, 5, "claude", "review", "reviewing", "changes-requested", 1)
    d.write_state_log(base_config, 5, "claude", "review", "reviewing", "changes-requested", 2)
    assert d.get_attempt_count(base_config, 5) == 2


def test_write_state_log_updates_symlink(tmp_path, base_config):
    d.write_state_log(base_config, 3, "kiro", "coding", "todo", "in-progress", 1)
    current = Path(base_config["pipeline"]["state_base"]).parent / "current" / "issue-3"
    assert current.is_symlink()


# ---------------------------------------------------------------------------
# pr_exists / pr_is_approved
# ---------------------------------------------------------------------------

def test_pr_exists_true():
    with patch("dispatcher.subprocess.run", return_value=_mock_run(json.dumps([{"number": 5}]))):
        assert d.pr_exists(42, "/repo") is True


def test_pr_exists_false():
    with patch("dispatcher.subprocess.run", return_value=_mock_run("[]")):
        assert d.pr_exists(42, "/repo") is False


def test_pr_is_approved_true():
    pr_list = [{"number": 5}]
    pr_detail = {"reviews": [{"state": "APPROVED", "author": {"login": "bob"}}]}
    with patch("dispatcher.subprocess.run", side_effect=[_mock_run(json.dumps(pr_list)), _mock_run(json.dumps(pr_detail))]):
        assert d.pr_is_approved(42, "/repo") is True


def test_pr_is_approved_false_no_approval():
    pr_list = [{"number": 5}]
    pr_detail = {"reviews": [{"state": "CHANGES_REQUESTED", "author": {"login": "bob"}}]}
    with patch("dispatcher.subprocess.run", side_effect=[_mock_run(json.dumps(pr_list)), _mock_run(json.dumps(pr_detail))]):
        assert d.pr_is_approved(42, "/repo") is False


def test_pr_is_approved_false_no_pr():
    with patch("dispatcher.subprocess.run", return_value=_mock_run("[]")):
        assert d.pr_is_approved(42, "/repo") is False


def test_pr_has_review_comments_true_changes_requested():
    pr_list = [{"number": 5}]
    pr_detail = {"reviews": [{"state": "CHANGES_REQUESTED"}], "comments": []}
    with patch("dispatcher.subprocess.run", side_effect=[_mock_run(json.dumps(pr_list)), _mock_run(json.dumps(pr_detail))]):
        assert d.pr_has_review_comments(42, "/repo") is True


def test_pr_has_review_comments_true_has_comments():
    pr_list = [{"number": 5}]
    pr_detail = {"reviews": [], "comments": [{"body": "please fix this"}]}
    with patch("dispatcher.subprocess.run", side_effect=[_mock_run(json.dumps(pr_list)), _mock_run(json.dumps(pr_detail))]):
        assert d.pr_has_review_comments(42, "/repo") is True


def test_pr_has_review_comments_false():
    pr_list = [{"number": 5}]
    pr_detail = {"reviews": [], "comments": []}
    with patch("dispatcher.subprocess.run", side_effect=[_mock_run(json.dumps(pr_list)), _mock_run(json.dumps(pr_detail))]):
        assert d.pr_has_review_comments(42, "/repo") is False


# ---------------------------------------------------------------------------
# fetch_pr_context
# ---------------------------------------------------------------------------

def test_fetch_pr_context_no_pr():
    with patch("dispatcher.subprocess.run", return_value=_mock_run("[]")):
        assert d.fetch_pr_context(42, "/repo") is None


def test_fetch_pr_context_with_pr():
    pr_list = [{"number": 7, "url": "https://github.com/x/y/pull/7", "title": "Fix #42"}]
    pr_detail = {
        "number": 7, "url": "https://github.com/x/y/pull/7", "title": "Fix #42",
        "reviews": [{"author": {"login": "bob"}, "body": "Looks good"}],
        "comments": [],
    }
    responses = [_mock_run(json.dumps(pr_list)), _mock_run(json.dumps(pr_detail))]
    with patch("dispatcher.subprocess.run", side_effect=responses):
        result = d.fetch_pr_context(42, "/repo")
    assert "# Pull Request #7" in result
    assert "Fix #42" in result
    assert "Looks good" in result


def test_fetch_pr_context_gh_failure():
    with patch("dispatcher.subprocess.run", return_value=_mock_run("", returncode=1)):
        assert d.fetch_pr_context(42, "/repo") is None


# ---------------------------------------------------------------------------
# post_assignment_comment
# ---------------------------------------------------------------------------

def test_post_assignment_comment_normal():
    with patch("dispatcher.subprocess.run", return_value=_mock_run("")) as mock:
        d.post_assignment_comment(42, "kiro", "coding", 1, "/repo")
    body = mock.call_args[0][0]
    assert "attempt 1" in " ".join(body)
    assert "kiro" in " ".join(body)


def test_post_assignment_comment_retry():
    with patch("dispatcher.subprocess.run", return_value=_mock_run("")) as mock:
        d.post_assignment_comment(42, "kiro", "coding", 2, "/repo", is_retry=True)
    body = mock.call_args[0][0]
    assert "retry attempt 2" in " ".join(body)


# ---------------------------------------------------------------------------
# write_issue_context with pr_context
# ---------------------------------------------------------------------------

def test_write_issue_context_with_pr(tmp_path, base_config):
    ws = Path(base_config["pipeline"]["workspace_base"]) / "issue-8"
    ws.mkdir(parents=True)
    d.write_issue_context(base_config, 8, "# Issue #8", "---\n# Pull Request #3")
    content = (ws / "ISSUE.md").read_text()
    assert "# Issue #8" in content
    assert "# Pull Request #3" in content


def test_write_issue_context_without_pr(tmp_path, base_config):
    ws = Path(base_config["pipeline"]["workspace_base"]) / "issue-9"
    ws.mkdir(parents=True)
    d.write_issue_context(base_config, 9, "# Issue #9", None)
    content = (ws / "ISSUE.md").read_text()
    assert content == "# Issue #9"




def test_notify_discord(base_config):
    base_config["notifications"] = {"discord": {"webhook_url": "https://discord.example/hook"}}
    with patch("dispatcher.urllib.request.urlopen") as mock:
        d.notify(base_config, "hello", "ready-to-merge")
    mock.assert_called_once()


def test_notify_telegram(base_config):
    base_config["notifications"] = {"telegram": {"token": "tok", "chat_id": "123"}}
    with patch("dispatcher.urllib.request.urlopen") as mock:
        d.notify(base_config, "hello", "changes-requested")
    mock.assert_called_once()


def test_notify_skips_non_notify_state(base_config):
    base_config["notifications"] = {"discord": {"webhook_url": "https://discord.example/hook"}}
    with patch("dispatcher.urllib.request.urlopen") as mock:
        d.notify(base_config, "hello", "some-other-state")
    mock.assert_not_called()


def test_notify_skips_missing_config(base_config):
    base_config["notifications"] = {}
    with patch("dispatcher.urllib.request.urlopen") as mock:
        d.notify(base_config, "hello", "ready-to-merge")
    mock.assert_not_called()
