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

    try:
        cfg = team.load_config(root)
    except Exception:
        cfg = None
    if cfg:
        lines.append(f"{BOLD}Roles{RESET}")
        for role in cfg["role"]:
            name = role["name"]
            try:
                commits, files = team.progress(root, cfg, name)
            except Exception:
                commits = files = 0
            prog = (
                f"{commits} commit(s) · {files} file(s)" if commits
                else f"{DIM}no commits yet{RESET}"
            )
            lines.append(f"  {CYAN}{name:12}{RESET} {prog}")
        lines.append("")

    tasks = bus.task_list(home)
    lines.append(f"{BOLD}Tasks{RESET}")
    if not tasks:
        lines.append(f"  {DIM}(none — add with `zeliji task add \"...\"`){RESET}")
    for t in tasks:
        icon = STATUS_ICON.get(t["status"], "?")
        who = f" @{t['role']}" if t.get("role") else ""
        blocked = bus.blocked_by(t, tasks)
        mark = ""
        if blocked and t["status"] == "todo":
            deps = ", ".join(f"#{d}" for d in blocked)
            mark = f" {YELLOW}⧗{deps}{RESET}"
        lines.append(f"  {icon} #{t['id']} {t['title']}{CYAN}{who}{RESET}{mark}")

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
        warnings = team.overlap_report(root, cfg) if cfg else []
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
