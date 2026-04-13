#!/bin/bash
# status.sh — pipeline monitoring dashboard
# Designed to be run by AI agents or humans to get a quick overview.
# Output is plain text, easy to parse programmatically.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS_YML="${SCRIPT_DIR}/agents.yml"
PYTHON="${SCRIPT_DIR}/.venv/bin/python3"
[[ -x "$PYTHON" ]] || PYTHON="python3"

[[ -f "${SCRIPT_DIR}/.env" ]] && source "${SCRIPT_DIR}/.env"
export GH_TOKEN="${DISPATCHER_GH_TOKEN:-${CODER_GH_TOKEN:-}}"

if [[ ! -f "$AGENTS_YML" ]]; then
  echo "ERROR: agents.yml not found" >&2
  exit 1
fi

$PYTHON - "$AGENTS_YML" <<'PYEOF'
import json, os, sys, time, subprocess, yaml
from pathlib import Path
from datetime import datetime, timezone

with open(sys.argv[1]) as f:
    config = yaml.safe_load(f)

repo_path = config["pipeline"]["repo_path"]
state_base = Path(os.path.expanduser(config["pipeline"]["state_base"]))
workspace_base = Path(os.path.expanduser(config["pipeline"]["workspace_base"]))
agents = config.get("agents", [])
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

def gh(args):
    r = subprocess.run(["gh"] + args, cwd=repo_path, capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []

# ─── 1. GitHub Issues by state ───
print("=" * 60)
print("📊 PIPELINE STATUS")
print("=" * 60)

all_issues = gh(["issue", "list", "--state", "all", "--json", "number,title,state,labels", "--limit", "100"])
label_groups = {}
for issue in all_issues:
    labels = [l["name"] for l in issue.get("labels", [])]
    state_label = next((l for l in labels if l in {
        "todo", "in-progress", "pr-opened", "reviewing",
        "ready-to-merge", "changes-requested", "human-review-required", "agent-error"
    }), "no-label")
    label_groups.setdefault(state_label, []).append(issue)

open_issues = [i for i in all_issues if i["state"] == "OPEN"]
closed_issues = [i for i in all_issues if i["state"] == "CLOSED"]

print(f"\nOpen issues: {len(open_issues)}  |  Closed: {len(closed_issues)}  |  Total: {len(all_issues)}")

# Active work
active_labels = ["in-progress", "reviewing", "pr-opened", "todo", "changes-requested"]
print("\n── Active Issues ──")
found_active = False
for label in active_labels:
    for issue in label_groups.get(label, []):
        if issue["state"] == "OPEN":
            print(f"  #{issue['number']:>3}  [{label:<20}]  {issue['title']}")
            found_active = True
if not found_active:
    print("  (none)")

# Needs attention
print("\n── ⚠️  Needs Human Attention ──")
attention_labels = ["human-review-required", "agent-error"]
found_attention = False
for label in attention_labels:
    for issue in label_groups.get(label, []):
        if issue["state"] == "OPEN":
            print(f"  #{issue['number']:>3}  [{label:<20}]  {issue['title']}")
            found_attention = True
if not found_attention:
    print("  (none)")

# Ready to merge
rtm = [i for i in label_groups.get("ready-to-merge", []) if i["state"] == "OPEN"]
if rtm:
    print(f"\n── 🟢 Ready to Merge ({len(rtm)}) ──")
    for issue in rtm:
        print(f"  #{issue['number']:>3}  {issue['title']}")

# ─── 2. Agent Activity ───
print("\n" + "=" * 60)
print("🤖 AGENT ACTIVITY")
print("=" * 60)

lock_dir = Path("/tmp")
now = time.time()
coding_active = []
review_active = []

for agent in agents:
    name = agent["name"]
    role = agent["role"]
    locks = list(lock_dir.glob(f"agent-{name}-*.lock"))
    for lock in locks:
        try:
            ts, issue_num = lock.read_text().split(":")
            age = int(now - int(ts))
            mins, secs = divmod(age, 60)
            entry = f"  {name:<15} → issue #{issue_num:<5} ({mins}m {secs}s)"
            if role == "coding":
                coding_active.append(entry)
            else:
                review_active.append(entry)
        except Exception:
            pass

print(f"\nCoding agents active: {len(coding_active)}")
for e in coding_active:
    print(e)
if not coding_active:
    print("  (idle)")

print(f"\nReview agents active: {len(review_active)}")
for e in review_active:
    print(e)
if not review_active:
    print("  (idle)")

# Agent config summary
print("\nConfigured agents:")
for agent in agents:
    locks = len(list(lock_dir.glob(f"agent-{agent['name']}-*.lock")))
    print(f"  {agent['name']:<15}  role={agent['role']:<8}  slots={locks}/{agent['max_concurrent']}")

# ─── 3. Recent History ───
print("\n" + "=" * 60)
print("📜 RECENT HISTORY (newest first)")
print("=" * 60)

history = []
if state_base.exists():
    for issue_dir in state_base.iterdir():
        if not issue_dir.name.startswith("issue-"):
            continue
        issue_num = issue_dir.name.split("-")[1]
        for log_file in issue_dir.glob("*.log"):
            try:
                with open(log_file) as f:
                    data = yaml.safe_load(f)
                ts = data.get("timestamp", "")
                history.append({
                    "issue": issue_num,
                    "state": data.get("curr_state", "?"),
                    "agent": data.get("agent", "?"),
                    "role": data.get("role", "?"),
                    "timestamp": ts,
                    "date": ts[:10] if ts else "",
                    "file": log_file.name,
                })
            except Exception:
                pass

history.sort(key=lambda x: x["timestamp"], reverse=True)

today_entries = [h for h in history if h["date"] == today]
older_entries = [h for h in history if h["date"] != today]

if today_entries:
    print(f"\n── Today ({today}) ──")
    for h in today_entries:
        t = h["timestamp"][11:19] if len(h["timestamp"]) > 19 else ""
        print(f"  {t}  issue #{h['issue']:>3}  {h['state']:<22}  agent={h['agent']}  role={h['role']}")

if older_entries:
    dates = sorted(set(h["date"] for h in older_entries), reverse=True)
    for date in dates[:3]:  # show last 3 days max
        day_entries = [h for h in older_entries if h["date"] == date]
        print(f"\n── {date} ──")
        for h in day_entries:
            t = h["timestamp"][11:19] if len(h["timestamp"]) > 19 else ""
            print(f"  {t}  issue #{h['issue']:>3}  {h['state']:<22}  agent={h['agent']}  role={h['role']}")

if not history:
    print("  (no history)")

# ─── 4. Disk / Workspace ───
print("\n" + "=" * 60)
print("💾 WORKSPACES")
print("=" * 60)

if workspace_base.exists():
    workspaces = [d for d in workspace_base.iterdir() if d.name.startswith("issue-")]
    print(f"\nActive workspaces: {len(workspaces)}")
    for ws in sorted(workspaces):
        size = sum(f.stat().st_size for f in ws.rglob("*") if f.is_file()) // (1024 * 1024)
        print(f"  {ws.name:<15}  ~{size}MB")
else:
    print("\n  (no workspaces)")

# ─── 5. Error log check ───
print("\n" + "=" * 60)
print("🔍 ERROR LOG (last 5 lines)")
print("=" * 60)

error_log = Path("/tmp/agentic-loop.error.log")
if error_log.exists() and error_log.stat().st_size > 0:
    lines = error_log.read_text().strip().split("\n")
    for line in lines[-5:]:
        print(f"  {line}")
else:
    print("  (clean — no errors)")

print()
PYEOF
