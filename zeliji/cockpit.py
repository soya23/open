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

HINTS_NORMAL = " Tab focus · Enter instruct · g nudge · n task · s say · a add role · q quit"
HINTS_INPUT = " Enter send · Esc cancel"
AUTONOMOUS_FLAG = "--dangerously-skip-permissions"
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
        self.input_target: str | None = None  # None | "agent" | "task" | "say" | "role"
        self.buffer = ""
        flags = agents[0].flags if agents else []
        self.autonomous = AUTONOMOUS_FLAG in flags
        self.show_board = False
        self.confirm_quit = False
        self._belled: set[str] = set()

    # ------------------------------------------------------------ frame

    def compose(self, w: int, h: int) -> list[str]:
        if self.show_board:
            return self._compose_board(w, h)
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

    def _compose_board(self, w: int, h: int) -> list[str]:
        icon = {"todo": "· ", "doing": CYAN + "▶ ", "done": GREEN + "✔ "}
        lines = [BOLD + " タスクボード" + RESET + DIM + "  (b で戻る)" + RESET, ""]
        tasks = bus.task_list(self.home)
        if not tasks:
            lines.append(DIM + "  (タスクなし — n で追加)" + RESET)
        for t in tasks:
            who = f" @{t['role']}" if t.get("role") else ""
            blocked = bus.blocked_by(t, tasks)
            mark = YELLOW + f" ⧗{','.join(f'#{d}' for d in blocked)}" + RESET \
                if blocked and t["status"] == "todo" else ""
            lines.append(f"  {icon.get(t['status'], '')}#{t['id']} "
                         f"{clip_cells(t['title'], w - 20)}{CYAN}{who}{RESET}{mark}")
        lines.append("")
        lines.append(BOLD + " メッセージ" + RESET)
        for m in bus.messages(self.home, limit=h - len(lines) - 3):
            lines.append(DIM + f"  {m['ts'][11:]} " + RESET +
                         clip_cells(f"{m['from']}→{m['to']}: {m['text']}", w - 12))
        lines += [""] * (h - 1 - len(lines))
        lines.append(REV + pad_cells(" b close · n task · s say · q quit", w - 1))
        return lines[:h]

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
            label = {
                "agent": self.agents[self.focus].role,
                "task": "新タスク",
                "say": "宛先+本文 (例: all 進捗どう?)",
                "role": "役割名+説明 (例: tester テスト担当)",
            }[self.input_target]
            prompt = f" {label}> {self.buffer}"
            while cells(prompt) > w - 2:
                prompt = prompt[1:]
            last = BOLD + prompt + "▌"
        elif self.confirm_quit:
            last = REV + YELLOW + pad_cells(
                " エージェントが実行中です — 本当に終了するなら q をもう一度、戻るなら他のキー", w - 1)
        elif self.autonomous:
            last = REV + YELLOW + " ⚠AUTO" + RESET + REV + pad_cells(HINTS_NORMAL, w - 7)
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
        elif target == "role":
            self._add_role(text)

    def _add_role(self, text: str) -> None:
        name, _, prompt = text.partition(" ")
        if not team.valid_role_name(name) or any(a.role == name for a in self.agents):
            return
        role = {"name": name, "prompt": prompt or "チームの一員として、ボードのタスクを手伝う。"}
        try:
            team.append_role(self.root, role)
            self.cfg["role"].append(role)
            team.ensure_worktree(self.root, self.home, name, self.cfg)
            team.write_charter(self.home, role)
        except team.TeamError:
            return
        project = self.cfg.get("project", {})
        agent = agentrun.Agent(
            role=name,
            worktree=team.worktree_path(self.home, name),
            charter=self.home / "roles" / f"{name}.md",
            home=self.home,
            model=role.get("model") or project.get("model"),
            flags=list(self.agents[0].flags) if self.agents else list(agentrun.DEFAULT_FLAGS),
        )
        agent.start()
        agent.instruct(project.get("kickoff", agentrun.KICKOFF))
        self.agents.append(agent)
        self.focus = len(self.agents) - 1

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
        if self.confirm_quit:
            self.confirm_quit = False
            return ch != "q"  # q confirms quit; anything else just cancels
        if ch in ("q", "\x1b", "\x03"):
            # zellij lesson (issue #467, 41 upvotes): confirm before killing
            # a session with live agents; Ctrl-C stays immediate
            if ch != "\x03" and any(a.state == agentrun.RUNNING for a in self.agents):
                self.confirm_quit = True
                return True
            return False
        if ch == "b":
            self.show_board = not self.show_board
        elif ch in ("\t", term.RIGHT):
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
        elif ch == "a":
            self.input_target = "role"
        elif ch == "g":
            for agent in self.agents:
                if agent.state == agentrun.WAITING:
                    agent.instruct(NUDGE)
        return True

    # ------------------------------------------------------------ loop

    def loop(self, t: term.Term) -> None:
        import sys

        while True:
            # terminal bell when an agent newly needs attention
            for agent in self.agents:
                if agent.attention and agent.role not in self._belled:
                    self._belled.add(agent.role)
                    sys.stdout.write("\a")
                elif not agent.attention:
                    self._belled.discard(agent.role)
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
