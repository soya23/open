"""Live dashboard pane: task board, recent messages, conflict warnings."""

from __future__ import annotations

import time
from pathlib import Path

from . import bus, team

BOLD = "\033[1m"
DIM = "\033[2m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
CYAN = "\033[36m"
RESET = "\033[0m"

STATUS_ICON = {"todo": "·", "doing": f"{CYAN}▶{RESET}", "done": f"{GREEN}✔{RESET}"}


def render_once(home: Path, root: Path) -> str:
    lines = [f"{BOLD}zeliji{RESET} {DIM}— shared team board{RESET}", ""]

    tasks = bus.task_list(home)
    lines.append(f"{BOLD}Tasks{RESET}")
    if not tasks:
        lines.append(f"  {DIM}(none — add with `zeliji task add \"...\"`){RESET}")
    for t in tasks:
        icon = STATUS_ICON.get(t["status"], "?")
        who = f" @{t['role']}" if t.get("role") else ""
        lines.append(f"  {icon} #{t['id']} {t['title']}{CYAN}{who}{RESET}")

    lines.append("")
    lines.append(f"{BOLD}Messages{RESET}")
    msgs = bus.messages(home, limit=8)
    if not msgs:
        lines.append(f"  {DIM}(none — `zeliji say all \"hello\"`){RESET}")
    for m in msgs:
        lines.append(
            f"  {DIM}{m['ts'][11:]}{RESET} {CYAN}{m['from']}{RESET}"
            f" → {m['to']}: {m['text']}"
        )

    try:
        cfg = team.load_config(root)
        warnings = team.overlap_report(root, cfg)
    except Exception:
        warnings = []
    if warnings:
        lines.append("")
        lines.append(f"{BOLD}{YELLOW}Conflict watch{RESET}")
        lines.extend(f"  {YELLOW}{w}{RESET}" for w in warnings)

    return "\n".join(lines)


def run(home: Path, interval: float = 2.0) -> None:
    root = home.parent
    try:
        while True:
            frame = render_once(home, root)
            print(f"\033[2J\033[H{frame}", flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
