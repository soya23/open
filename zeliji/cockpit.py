"""The cockpit: zeliji's own display layer. No zellij, no tmux.

One column per role showing that agent's structured event timeline,
a shared footer with the task board and messages, and a permanent
key-hint bar (discoverability, stolen from zellij). Panes hold agents,
not terminals — input means instructing the focused agent, and a
finished agent is flagged with `!` until you look at it.
"""

from __future__ import annotations

import curses
import time
import unicodedata
from pathlib import Path

from . import agentrun, bus, team

EVENT_MARK = {
    "you": ("» ", 5),   # instruction we sent
    "say": ("", 0),     # agent's own words
    "tool": ("⚒ ", 4),
    "done": ("✔ ", 2),
    "error": ("✖ ", 1),
}
STATE_BADGE = {
    agentrun.RUNNING: ("● RUN ", 4),
    agentrun.WAITING: ("○ WAIT", 3),
    agentrun.IDLE: ("· IDLE", 0),
    agentrun.ERROR: ("✖ ERR ", 1),
}

HINTS_NORMAL = " Tab focus · Enter instruct · g nudge waiting · n task · s say · q quit "
HINTS_INPUT = " Enter send · Esc cancel "


def _cell_width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


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


class Cockpit:
    def __init__(self, cfg: dict, home: Path, root: Path, agents: list[agentrun.Agent]):
        self.cfg = cfg
        self.home = home
        self.root = root
        self.agents = agents
        self.focus = 0
        self.input_target: str | None = None  # None | "agent" | "task" | "say"
        self.buffer = ""

    # ------------------------------------------------------------ drawing

    def _draw(self, scr) -> None:
        scr.erase()
        h, w = scr.getmaxyx()
        footer_h = 4
        body_h = max(3, h - footer_h)
        col_w = max(20, w // max(1, len(self.agents)))

        for i, agent in enumerate(self.agents):
            x0 = i * col_w
            if x0 + 2 > w:
                break
            badge, color = STATE_BADGE[agent.state]
            mark = " !" if agent.attention and i != self.focus else ""
            title = f" {agent.role} {badge}{mark} "
            attr = curses.A_REVERSE if i == self.focus else curses.A_BOLD
            self._put(scr, 0, x0, clip_cells(title, col_w - 1), curses.color_pair(color) | attr)

            lines: list[tuple[str, int]] = []
            for kind, text in list(agent.events):
                prefix, c = EVENT_MARK.get(kind, ("", 0))
                for wrapped in wrap_cells(prefix + text, col_w - 2):
                    lines.append((wrapped, c))
            for row, (text, c) in enumerate(lines[-(body_h - 1):], start=1):
                self._put(scr, row, x0, text, curses.color_pair(c))
            if i:
                for row in range(0, body_h):
                    self._put(scr, row, x0 - 1, "│", curses.A_DIM)

        self._draw_footer(scr, h, w, footer_h)
        scr.refresh()

    def _draw_footer(self, scr, h: int, w: int, footer_h: int) -> None:
        y = h - footer_h
        self._put(scr, y, 0, "─" * (w - 1), curses.A_DIM)
        tasks = bus.task_list(self.home)
        counts = {s: sum(1 for t in tasks if t["status"] == s) for s in ("todo", "doing", "done")}
        doing = ", ".join(
            f"#{t['id']} {t['title'][:20]}@{t.get('role') or '?'}"
            for t in tasks if t["status"] == "doing"
        )
        board = f" Tasks: {counts['todo']} todo · {counts['doing']} doing ({doing}) · {counts['done']} done" \
            if doing else f" Tasks: {counts['todo']} todo · {counts['doing']} doing · {counts['done']} done"
        self._put(scr, y + 1, 0, clip_cells(board, w - 1))
        msgs = bus.messages(self.home, limit=1)
        if msgs:
            m = msgs[-1]
            self._put(scr, y + 2, 0, clip_cells(f" msg: {m['from']}→{m['to']}: {m['text']}", w - 1), curses.A_DIM)
        if self.input_target:
            who = self.agents[self.focus].role if self.input_target == "agent" else self.input_target
            prompt = f" {who}> {self.buffer}"
            while sum(_cell_width(c) for c in prompt) > w - 2:
                prompt = prompt[1:]
            self._put(scr, h - 1, 0, prompt, curses.A_BOLD)
        else:
            self._put(scr, h - 1, 0, HINTS_NORMAL[: w - 1], curses.A_REVERSE)

    @staticmethod
    def _put(scr, y: int, x: int, text: str, attr=0) -> None:
        try:
            scr.addstr(y, x, text, attr)
        except curses.error:
            pass  # writing to the last cell raises; harmless

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

    def _key(self, ch) -> bool:
        """Handle one key from get_wch (str, or int for KEY_*); False = quit."""
        is_enter = ch in ("\n", "\r", curses.KEY_ENTER)
        is_esc = ch == "\x1b"
        if self.input_target is not None:
            if is_esc:
                self.buffer, self.input_target = "", None
            elif is_enter:
                self._submit()
            elif ch in ("\x7f", "\b", curses.KEY_BACKSPACE):
                self.buffer = self.buffer[:-1]
            elif isinstance(ch, str) and ch.isprintable():
                self.buffer += ch
            return True
        if ch == "q" or is_esc:
            return False
        if ch in ("\t", curses.KEY_RIGHT):
            self.focus = (self.focus + 1) % len(self.agents)
            self.agents[self.focus].attention = False
        elif ch == curses.KEY_LEFT:
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
                    agent.instruct("ボードとinboxを再確認し、着手可能なタスクがあれば続けて。なければ待機と報告して。")
        return True

    # ------------------------------------------------------------ loop

    def loop(self, scr) -> None:
        curses.curs_set(0)
        curses.use_default_colors()
        for i, fg in enumerate(
            [curses.COLOR_RED, curses.COLOR_GREEN, curses.COLOR_YELLOW,
             curses.COLOR_CYAN, curses.COLOR_MAGENTA], start=1
        ):
            curses.init_pair(i, fg, -1)
        scr.timeout(250)
        while True:
            self._draw(scr)
            try:
                ch = scr.get_wch()
            except curses.error:  # timeout tick — just redraw
                continue
            if not self._key(ch):
                return


def run(cfg: dict, home: Path, root: Path) -> None:
    worktrees = {r["name"]: team.worktree_path(home, r["name"]) for r in cfg["role"]}
    agents = agentrun.spawn_team(cfg, home, worktrees)
    try:
        curses.wrapper(Cockpit(cfg, home, root, agents).loop)
    finally:
        for agent in agents:
            agent.stop()
        time.sleep(0.2)
