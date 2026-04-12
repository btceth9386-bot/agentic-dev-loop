#!/usr/bin/env python3
"""Test Telegram and Discord notifications using the current agents.yml config."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dispatcher import load_config, notify

config = load_config()
message = "🔔 Agentic Loop notification test — if you see this, it works!"

print("Sending test notification...")
notify(config, message, state="ready-to-merge")
print("Done. Check your Telegram / Discord.")
