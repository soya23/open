"""The cockpit: zeliji's own display layer. No zellij, no tmux, no curses.

One column per role showing that agent's structured event timeline,
a shared footer with the task board and messages, and a permanent
key-hint bar (discoverability, stolen from zellij). Panes hold agents,
not terminals — input means instructing the focused agent, and a
finished agent is flagged with `!` until you look at it.

Every frame is composed as complete lines with cell-accurate padding
(East-Asian wide chars count as 2), so rendering is just printing —
the terminal never has to guess our layout.
"""

from __future__ import annotations

import time
import unicodedata
from pathlib import Path

from . import agentrun, bus, team, term

RESET, BOLD, DIM, REV = "\x1b[0m", "\x1b[1m", "\x1b[2m", "\x1b[7m"
RED, GREEN, YELLOW, CYAN, MAGENTA = (f"\x1b[3{n}m" for n in (1, 2, 3, 6, 5))

EVENT_MARK = {
    "you": ("» ", MAGENTA),  # instruction we sent
    "say": ("", ""),         # agent's own words
    "tool": ("⚒ ", CYAN),
    "done": ("✔ ", GREEN),
    "error": ("✖ ", RED),
}
STATE_BADGE = {
    agentrun.RUNNING: ("● RUN ", CYAN),
    agentrun.WAITING: ("○ WAIT", YELLOW),
    agentrun.IDLE: ("· IDLE", DIM),
    agentrun.ERROR: ("✖ ERR ", RED),
}

HINTS_NORMAL = " Tab focus · Enter instruct · g nudge waiting · n task · s say · q quit"
HINTS_INPUT = " Enter send · Esc cancel"
NUDGE = "ボードとinboxを再確認し、着手可能なタスクがあれば続けて。なければ待機と報告して。"


def _cell_width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def cells(text: str) -> int:
    return sum(_cell_width(c) for c in text)


def wrap_cells(text: str, width: int) -> list[str]:
    """Wrap by terminal cells, not characters — CJK chars take 2 cells."""
    lines, cur, cw = [], "", 0
    for ch in text:
        w = _cell_width(ch)
        if cw + w > width:
            lines.append(cur)
            cur, cw = ch, w
        else:
            cur += ch
            cw += w
    lines.append(cur)
    return lines


def clip_cells(text: str, width: int) -> str:
    out, cw = "", 0
    for ch in text:
        w = _cell_width(ch)
        if cw + w > width:
            break
        out += ch
        cw += w
    return out


def pad_cells(text: str, width: int) -> str:
    text = clip_cells(text, width)
    return text + " " * (width - cells(text))


class Cockpit:
    def __init__(self, cfg: dict, home: Path, root: Path, agents: list[agentrun.Agent]):
        self.cfg = cfg
        self.home = home
        self.root = root
        self.agents = agents
        self.focus = 0
        self.input_target: str | None = None  # None | "agent" | "task" | "say"
        self.buffer = ""

    # ------------------------------------------------------------ frame

    def compose(self, w: int, h: int) -> list[str]:
        n = len(self.agents)
        col_w = max(16, w // n)
        inner = col_w - 2  # content cells per column, before " │" divider
        body_h = max(3, h - 5)  # 1 header + 4 footer

        columns: list[list[tuple[str, str]]] = []
        for agent in self.agents:
            rows: list[tuple[str, str]] = []
            for kind, text in list(agent.events):
                prefix, color = EVENT_MARK.get(kind, ("", ""))
                for wrapped in wrap_cells(prefix + text, inner):
                    rows.append((wrapped, color))
            columns.append(rows[-body_h:])

        lines = [self._header(col_w, inner)]
        for r in range(body_h):
            parts = []
            for i, rows in enumerate(columns):
                text, color = rows[r] if r < len(rows) else ("", "")
                parts.append(color + pad_cells(text, inner) + RESET)
            lines.append(f"{RESET} {DIM}│{RESET} ".join(parts))
        lines.extend(self._footer(w))
        return lines

    def _header(self, col_w: int, inner: int) -> str:
        parts = []
        for i, agent in enumerate(self.agents):
            badge, color = STATE_BADGE[agent.state]
            mark = " !" if agent.attention and i != self.focus else ""
            style = REV if i == self.focus else BOLD
            parts.append(color + style + pad_cells(f" {agent.role} {badge}{mark}", inner) + RESET)
        return f"{RESET} {DIM}│{RESET} ".join(parts)

    def _footer(self, w: int) -> list[str]:
        tasks = bus.task_list(self.home)
        counts = {s: sum(1 for t in tasks if t["status"] == s) for s in ("todo", "doing", "done")}
        doing = ", ".join(
            f"#{t['id']} {clip_cells(t['title'], 24)}@{t.get('role') or '?'}"
            for t in tasks if t["status"] == "doing"
        )
        board = f" Tasks: {counts['todo']} todo · {counts['doing']} doing"
        if doing:
            board += f" ({doing})"
        board += f" · {counts['done']} done"
        msgs = bus.messages(self.home, limit=1)
        msg = f" msg: {msgs[-1]['from']}→{msgs[-1]['to']}: {msgs[-1]['text']}" if msgs else ""
        if self.input_target:
            who = self.agents[self.focus].role if self.input_target == "agent" else self.input_target
            prompt = f" {who}> {self.buffer}"
            while cells(prompt) > w - 2:
                prompt = prompt[1:]
            last = BOLD + prompt + "▌"
        else:
            last = REV + pad_cells(HINTS_NORMAL, w - 1)
        return [
            DIM + "─" * (w - 1),
            pad_cells(board, w - 1),
            DIM + pad_cells(msg, w - 1),
            last,
        ]

    # ------------------------------------------------------------ input

    def _submit(self) -> None:
        text, target = self.buffer.strip(), self.input_target
        self.buffer, self.input_target = "", None
        if not text:
            return
        if target == "agent":
            self.agents[self.focus].instruct(text)
        elif target == "task":
            bus.task_add(self.home, text)
        elif target == "say":
            to, _, body = text.partition(" ")
            roles = {a.role for a in self.agents}
            if to in roles | {"all"} and body:
                bus.say(self.home, to, body)
            else:
                bus.say(self.home, "all", text)

    def _key(self, ch: str) -> bool:
        """Handle one key from Term.read_keys; return False to quit."""
        is_enter = ch in ("\n", "\r")
        if self.input_target is not None:
            if ch == "\x1b":
                self.buffer, self.input_target = "", None
            elif is_enter:
                self._submit()
            elif ch in ("\x7f", "\x08"):
                self.buffer = self.buffer[:-1]
            elif len(ch) == 1 and ch.isprintable():
                self.buffer += ch
            return True
        if ch in ("q", "\x1b", "\x03"):
            return False
        if ch in ("\t", term.RIGHT):
            self.focus = (self.focus + 1) % len(self.agents)
            self.agents[self.focus].attention = False
        elif ch == term.LEFT:
            self.focus = (self.focus - 1) % len(self.agents)
            self.agents[self.focus].attention = False
        elif is_enter:
            self.input_target = "agent"
        elif ch == "n":
            self.input_target = "task"
        elif ch == "s":
            self.input_target = "say"
        elif ch == "g":
            for agent in self.agents:
                if agent.state == agentrun.WAITING:
                    agent.instruct(NUDGE)
        return True

    # ------------------------------------------------------------ loop

    def loop(self, t: term.Term) -> None:
        while True:
            w, h = t.size()
            t.draw(self.compose(w, h))
            for ch in t.read_keys(0.25):
                if not self._key(ch):
                    return


def run(cfg: dict, home: Path, root: Path) -> None:
    worktrees = {r["name"]: team.worktree_path(home, r["name"]) for r in cfg["role"]}
    agents = agentrun.spawn_team(cfg, home, worktrees)
    try:
        with term.Term() as t:
            Cockpit(cfg, home, root, agents).loop(t)
    finally:
        for agent in agents:
            agent.stop()
        time.sleep(0.2)
