#!/usr/bin/env python3
import json
import os
import re
import time
from typing import Any, Dict

import pexpect
import pyte


def render_terminal(raw: bytes, columns: int = 200, rows: int = 80) -> str:
    screen = pyte.Screen(columns, rows)
    stream = pyte.Stream(screen)
    text = raw.decode("utf-8", errors="ignore")
    stream.feed(text)

    lines = []
    for line in screen.display:
        line = line.rstrip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def parse_status_screen(screen_text: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "account": None,
        "plan": None,
        "model": None,
        "five_hour_percent_left": None,
        "five_hour_resets": None,
        "weekly_percent_left": None,
        "weekly_resets": None,
    }

    m = re.search(
        r"Account:\s*(\S+)\s*\(([^)]+)\)",
        screen_text,
        re.IGNORECASE,
    )
    if m:
        result["account"] = m.group(1).strip()
        result["plan"] = m.group(2).strip()

    # Case-sensitive: the startup banner uses lowercase "model:",
    # while the /status panel uses capitalized "Model:".
    m = re.search(r"Model:\s*([^\n│]+?)(?:\s*\(|\s*│|\s*$)", screen_text)
    if m:
        result["model"] = m.group(1).strip()

    m = re.search(
        r"5h limit:\s*\[[^\]]*\]\s*([0-9]+)%\s*left\s*\(resets\s+([^)]+)\)",
        screen_text,
        re.IGNORECASE,
    )
    if m:
        result["five_hour_percent_left"] = int(m.group(1))
        result["five_hour_resets"] = m.group(2).strip()

    m = re.search(
        r"Weekly limit:\s*\[[^\]]*\]\s*([0-9]+)%\s*left\s*\(resets\s+([^)]+)\)",
        screen_text,
        re.IGNORECASE,
    )
    if m:
        result["weekly_percent_left"] = int(m.group(1))
        result["weekly_resets"] = m.group(2).strip()

    return result


def _drain_until(child, raw: bytes, needle: str, timeout: float,
                 columns: int = 200, rows: int = 80) -> bytes:
    """Read child output until `needle` appears in the rendered screen, or timeout."""
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            chunk = child.read_nonblocking(size=65535, timeout=0.3)
            raw += chunk
        except Exception:
            pass
        if needle in render_terminal(raw, columns=columns, rows=rows):
            # Drain a bit more to make sure the whole block is rendered.
            extra_end = time.time() + 1.0
            while time.time() < extra_end:
                try:
                    raw += child.read_nonblocking(size=65535, timeout=0.2)
                except Exception:
                    break
            return raw
    return raw


def _handle_update_prompt(child, raw: bytes, timeout: float = 5.0,
                          columns: int = 200, rows: int = 80) -> bytes:
    """If codex shows the 'Update available' prompt, send '3' to skip until next version."""
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            raw += child.read_nonblocking(size=65535, timeout=0.3)
        except Exception:
            pass
        screen = render_terminal(raw, columns=columns, rows=rows)
        if "Update available" in screen and "Skip until next version" in screen:
            child.send(b"3\r")
            time.sleep(1)
            return raw
        # Main prompt already up -> no update prompt shown
        if "% left ·" in screen or "default ·" in screen:
            return raw
    return raw


def run_codex_status() -> Dict[str, Any]:
    cmd = "codex"

    child = pexpect.spawn(
        cmd,
        encoding=None,     # Important: keep raw bytes
        timeout=30,
        dimensions=(80, 200),  # rows, cols
        env=os.environ.copy(),
    )

    raw = b""
    # Handle the optional "Update available" prompt on first run after an update.
    raw = _handle_update_prompt(child, raw, timeout=8)

    # Wait for the main TUI prompt to be ready.
    raw = _drain_until(child, raw, "% left ·", timeout=15)
    # Give the input field an extra moment to attach, otherwise early keystrokes are lost.
    time.sleep(1.0)

    # Send /status. The codex TUI submits with \r\n, not \n or \r alone,
    # and the text + submit must be sent separately or the Enter is swallowed.
    child.send(b"/status")
    time.sleep(0.5)
    child.send(b"\r\n")

    # Poll until the status panel is rendered.
    raw = _drain_until(child, raw, "5h limit:", timeout=15)

    rendered = render_terminal(raw, columns=200, rows=80)
    parsed = parse_status_screen(rendered)

    # Exit
    try:
        child.send(b"/quit\r\n")
        time.sleep(0.5)
        child.close(force=True)
    except Exception:
        pass

    return {
        "ok": parsed["five_hour_percent_left"] is not None
              and parsed["weekly_percent_left"] is not None,
        "command": cmd,
        "screen_text": rendered,
        "parsed": parsed,
    }


if __name__ == "__main__":
    result = run_codex_status()
    print(json.dumps(result, indent=2, ensure_ascii=False))
