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


