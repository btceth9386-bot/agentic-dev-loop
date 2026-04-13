#!/usr/bin/env python3
import json
import os
import re
import time
from typing import Any, Dict, Optional

import pexpect
import pyte


def render_terminal(raw: bytes, columns: int = 160, rows: int = 60) -> str:
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


def parse_usage_screen(screen_text: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "reset_date": None,
        "plan": None,
        "covered_used": None,
        "covered_limit": None,
        "covered_percent": None,
        "overages_enabled": None,
        "overage_rate_usd_per_request": None,
        "overage_credits_used": None,
        "est_cost_usd": None,
    }

    m = re.search(
        r"Estimated Usage\s*\|\s*resets on\s*([0-9]{4}-[0-9]{2}-[0-9]{2})\s*\|\s*([A-Z0-9 _-]+)",
        screen_text,
        re.IGNORECASE,
    )
    if m:
        result["reset_date"] = m.group(1)
        result["plan"] = m.group(2).strip()

    m = re.search(
        r"Credits\s*\(\s*([0-9]+(?:\.[0-9]+)?)\s+of\s+([0-9]+(?:\.[0-9]+)?)\s+covered in plan\s*\)",
        screen_text,
        re.IGNORECASE,
    )
    if m:
        result["covered_used"] = float(m.group(1))
        result["covered_limit"] = float(m.group(2))

    # 只抓進度條旁 / Credits 區塊之後的百分比，避免抓到 prompt 上的 context 百分比 (例如 "1% >")
    m = re.search(r"covered in plan\s*\)[\s\S]*?\b([0-9]{1,3})%", screen_text, re.IGNORECASE)
    if not m:
        m = re.search(r"█\s*([0-9]{1,3})%", screen_text)
    if m:
        result["covered_percent"] = int(m.group(1))

    m = re.search(
        r"Overages:\s*(Enabled|Disabled)\s+billed at \$([0-9]+(?:\.[0-9]+)?) per request",
        screen_text,
        re.IGNORECASE,
    )
    if m:
        result["overages_enabled"] = m.group(1).lower() == "enabled"
        result["overage_rate_usd_per_request"] = float(m.group(2))
    else:
        m = re.search(r"Overages:\s*(Enabled|Disabled)", screen_text, re.IGNORECASE)
        if m:
            result["overages_enabled"] = m.group(1).lower() == "enabled"

    m = re.search(r"Credits used:\s*([0-9]+(?:\.[0-9]+)?)", screen_text, re.IGNORECASE)
    if m:
        result["overage_credits_used"] = float(m.group(1))

    m = re.search(r"Est\.\s*cost:\s*\$([0-9]+(?:\.[0-9]+)?)\s*USD", screen_text, re.IGNORECASE)
    if m:
        result["est_cost_usd"] = float(m.group(1))

    return result


def _drain_until(child, raw: bytes, needle: str, timeout: float,
                 columns: int = 160, rows: int = 60) -> bytes:
    """讀取 child 輸出直到 render 後出現 needle，或 timeout。"""
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            chunk = child.read_nonblocking(size=65535, timeout=0.3)
            raw += chunk
        except Exception:
            pass
        if needle in render_terminal(raw, columns=columns, rows=rows):
            # 再多抓一點，確保整個區塊渲染完成
            extra_end = time.time() + 1.0
            while time.time() < extra_end:
                try:
                    raw += child.read_nonblocking(size=65535, timeout=0.2)
                except Exception:
                    break
            return raw
    return raw


def run_kiro_usage() -> Dict[str, Any]:
    cmd = "kiro-cli --classic"

    child = pexpect.spawn(
        cmd,
        encoding=None,     # 重要：保留 raw bytes
        timeout=30,
        dimensions=(60, 160),  # rows, cols
        env=os.environ.copy(),
    )

    # 等 CLI 初始化 —— poll 等 prompt ("% >") 出現
    raw = _drain_until(child, b"", "% >", timeout=15)

    # 送 /usage (kiro-cli 的 TUI 需要 \r，不是 \n；sendline 會送 \n 導致指令不會被送出)
    child.send(b"/usage\r")

    # Poll 等 usage 畫面渲染出來
    raw = _drain_until(child, raw, "Estimated Usage", timeout=15)

    rendered = render_terminal(raw, columns=160, rows=60)
    parsed = parse_usage_screen(rendered)

    # 離開
    try:
        child.send(b"/exit\r")
        time.sleep(0.5)
        child.close(force=True)
    except Exception:
        pass

    return {
        "ok": parsed["plan"] is not None,
        "command": cmd,
        "screen_text": rendered,
        "parsed": parsed,
    }


if __name__ == "__main__":
    result = run_kiro_usage()
    print(json.dumps(result, indent=2, ensure_ascii=False))
