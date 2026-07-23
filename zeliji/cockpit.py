"""The cockpit: zeliji's own display layer. No zellij, no tmux, no curses.

One column per role showing that agent's timeline, a shared footer with
the task board, and a permanent key-hint bar. Panes hold agents, not
terminals — and the timeline is written for humans: the agent's own
words in full, tool activity as single dim lines (press v for the raw
firehose). Every frame is composed as complete lines with cell-accurate
padding, so rendering is just printing.
"""

from __future__ import annotations

import json
import sys
import time
import unicodedata
from pathlib import Path

from . import agentrun, bus, team, term

RESET, BOLD, DIM, REV = "\x1b[0m", "\x1b[1m", "\x1b[2m", "\x1b[7m"
RED, GREEN, YELLOW, CYAN, MAGENTA = (f"\x1b[3{n}m" for n in (1, 2, 3, 6, 5))

STATE_BADGE = {
    agentrun.RUNNING: ("● 作業中", CYAN),
    agentrun.WAITING: ("○ 待機", YELLOW),
    agentrun.IDLE: ("· 停止", DIM),
    agentrun.ERROR: ("✖ エラー", RED),
}

HINTS = " n やること · Enter 指示 · Tab 移動 · b ボード · ? 全キー · q 終了"
HINTS_INPUT = " Enter 送信 · Esc キャンセル"
AUTONOMOUS_FLAG = "--dangerously-skip-permissions"
NUDGE = "ボードとinboxを再確認し、着手可能なタスクがあれば続けて。なければ待機と報告して。"
CTA = " ✎ まず n を押して、やってほしいことを書いてください (例: READMEを読みやすくして)"

HELP = """\
 キー一覧  (? か Esc で戻る)

   n        やってほしいことを書く (タスクとしてボードに載り、手が空いた役割が拾う)
   Enter    選択中の役割に直接指示する
   Tab / ←→ 役割を選ぶ
   ↑ / ↓    選択中の列の履歴をさかのぼる / 戻る
   f        選択中の列を全画面に (もう一度で戻る)
   < / >    選択中の列の幅を調整 (, と . でも可)
   b        タスクボードを全画面で見る
   s        連絡を送る (例: all 今日はここまで / core 先にテスト書いて)
   a        役割を追加 (例: tester テスト担当)
   g        待機中の全員に「続けて」
   v        詳細表示の切替 (ツール実行ログを全文で見る)
   K        全員の作業をいますぐ中断 (緊急停止。セッションは残る)
   q        終了 (作業中の役割がいれば確認します)
"""


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


_ANSI = __import__("re").compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def clip_ansi(line: str, width: int) -> str:
    """Hard guarantee: a styled line never exceeds `width` cells."""
    out, cw, i = [], 0, 0
    while i < len(line):
        m = _ANSI.match(line, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        ch = line[i]
        w = _cell_width(ch)
        if cw + w > width:
            break
        out.append(ch)
        cw += w
        i += 1
    return "".join(out)


class Cockpit:
    def __init__(self, cfg: dict, home: Path, root: Path, agents: list[agentrun.Agent]):
        self.cfg = cfg
        self.home = home
        self.root = root
        self.agents = agents
        self.focus = 0
        self.input_target: str | None = None  # None | "agent" | "task" | "say" | "role"
        self.buffer = ""
        self.autonomous = AUTONOMOUS_FLAG in (agents[0].flags if agents else [])
        self.show_board = False
        self.show_help = False
        self.verbose = False
        self.zoom: int | None = None
        self.confirm_quit = False
        self.weights = [1.0] * len(agents)
        self.scroll: dict[int, int] = {}
        self._belled: set[str] = set()
        self._title = ""
        self._load_view()

    # ------------------------------------------------- view persistence
    # the KDL-layout lesson: your screen arrangement is part of the
    # environment and should survive a restart

    def _load_view(self) -> None:
        try:
            v = json.loads((self.home / "view.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        wmap = v.get("weights", {})
        self.weights = [float(wmap.get(a.role, 1.0)) for a in self.agents]
        self.verbose = bool(v.get("verbose", False))

    def save_view(self) -> None:
        v = {
            "weights": {a.role: w for a, w in zip(self.agents, self.weights)},
            "verbose": self.verbose,
        }
        try:
            (self.home / "view.json").write_text(
                json.dumps(v, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------ frame

    def compose(self, w: int, h: int) -> list[str]:
        return [clip_ansi(l, w) for l in self._compose(w, h)]

    def _compose(self, w: int, h: int) -> list[str]:
        if self.show_help:
            lines = HELP.splitlines()
            lines += [""] * max(0, h - 1 - len(lines))
            return lines[: h - 1] + [REV + pad_cells(HINTS, w - 1)]
        if self.show_board:
            return self._compose_board(w, h)

        idxs = [self.zoom] if self.zoom is not None else list(range(len(self.agents)))
        if len(idxs) * 13 - 3 > w:  # too narrow for all columns: show focus only
            idxs = [self.focus]
        inners = self._widths(w, idxs)
        body_h = max(3, h - 5)

        columns = []
        for i, inner in zip(idxs, inners):
            rows = self._rows(self.agents[i], inner)
            s = min(self.scroll.get(i, 0), max(0, len(rows) - body_h))
            self.scroll[i] = s
            end = len(rows) - s
            columns.append(rows[max(0, end - body_h):end])

        lines = [self._header(idxs, inners)]
        for r in range(body_h):
            parts = []
            for rows, inner in zip(columns, inners):
                text, color = rows[r] if r < len(rows) else ("", "")
                parts.append(color + pad_cells(text, inner) + RESET)
            lines.append(f"{RESET} {DIM}│{RESET} ".join(parts))
        lines.extend(self._footer(w))
        return lines

    def _widths(self, w: int, idxs: list[int]) -> list[int]:
        avail = max(len(idxs) * 10, w - 3 * (len(idxs) - 1) - 1)
        total = sum(self.weights[i] for i in idxs)
        inners = [max(10, int(avail * self.weights[i] / total)) for i in idxs]
        # min-width floors can push the sum past avail — shave the widest
        while sum(inners) > avail and max(inners) > 10:
            inners[inners.index(max(inners))] -= 1
        inners[inners.index(max(inners))] += avail - sum(inners)
        return inners

    def _rows(self, agent: agentrun.Agent, inner: int) -> list[tuple[str, str]]:
        """Human-first timeline: agent's words in full, activity as one
        dim line each; verbose mode shows everything wrapped."""
        out: list[tuple[str, str]] = []
        for kind, text in list(agent.events):
            flat = " ".join(text.split())
            if kind == "you":
                if self.verbose:
                    out += [(l, MAGENTA) for l in wrap_cells("» " + flat, inner)]
                else:
                    out.append((clip_cells("» " + flat, inner), MAGENTA + DIM))
            elif kind == "tool":
                if self.verbose:
                    out += [(l, CYAN) for l in wrap_cells("⚒ " + flat, inner)]
                else:
                    out.append((clip_cells("⚒ " + flat, inner), DIM))
            elif kind == "done":
                out += [(l, GREEN) for l in wrap_cells("✔ " + text, inner)]
            elif kind == "error":
                out += [(l, RED) for l in wrap_cells("✖ " + text, inner)]
            else:  # the agent's own words — always in full
                out += [(l, "") for l in wrap_cells(text, inner)]
        return out

    def _header(self, idxs: list[int], inners: list[int]) -> str:
        parts = []
        for i, inner in zip(idxs, inners):
            agent = self.agents[i]
            badge, color = STATE_BADGE[agent.state]
            elapsed = int(time.time() - agent.state_since)
            if agent.state == agentrun.RUNNING:
                badge += f" {elapsed}秒" if elapsed < 60 else f" {elapsed // 60}分"
            mark = " !" if agent.attention and i != self.focus else ""
            mark += " ▲" if self.scroll.get(i, 0) else ""
            if self.zoom is not None:
                mark += "  (f で戻る)"
            style = REV if i == self.focus else BOLD
            parts.append(color + style + pad_cells(f" {agent.role} {badge}{mark}", inner) + RESET)
        return f"{RESET} {DIM}│{RESET} ".join(parts)

    def _footer(self, w: int) -> list[str]:
        tasks = bus.task_list(self.home)
        counts = {s: sum(1 for t in tasks if t["status"] == s) for s in ("todo", "doing", "done")}
        active = [t for t in tasks if t["status"] in ("todo", "doing")]
        if not active:
            board = BOLD + YELLOW + pad_cells(CTA, w - 1)
        else:
            doing = ", ".join(
                f"#{t['id']} {clip_cells(t['title'], 22)}@{t.get('role') or '?'}"
                for t in tasks if t["status"] == "doing"
            )
            board = f" やること: 残り{counts['todo']} · 作業中{counts['doing']}"
            if doing:
                board += f" ({doing})"
            board += f" · 完了{counts['done']}   (b で一覧)"
            board = pad_cells(board, w - 1)
        msgs = bus.messages(self.home, limit=1)
        msg = f" 連絡: {msgs[-1]['from']}→{msgs[-1]['to']}: {msgs[-1]['text']}" if msgs else ""
        if self.input_target:
            label = {
                "agent": f"{self.agents[self.focus].role} への指示",
                "task": "やってほしいこと",
                "say": "宛先+本文 (例: all 進捗どう?)",
                "role": "新しい役割への指示 (説明だけでOK。例: テスト担当として壊れ方を探して)",
            }[self.input_target]
            prompt = f" {label}> {self.buffer}"
            while cells(prompt) > w - 2:
                prompt = prompt[1:]
            last = BOLD + prompt + "▌"
        elif self.confirm_quit:
            last = REV + YELLOW + pad_cells(
                " 作業中の役割がいます — 本当に終了するなら q をもう一度、戻るなら他のキー", w - 1)
        elif self.autonomous:
            last = REV + YELLOW + " ⚠AUTO" + RESET + REV + pad_cells(HINTS, w - 7)
        else:
            last = REV + pad_cells(HINTS, w - 1)
        return [DIM + "─" * (w - 1), board, DIM + pad_cells(msg, w - 1), last]

    def _compose_board(self, w: int, h: int) -> list[str]:
        icon = {"todo": "· ", "doing": CYAN + "▶ ", "done": GREEN + "✔ "}
        lines = [BOLD + " タスクボード" + RESET + DIM + "  (b で戻る)" + RESET, ""]
        tasks = bus.task_list(self.home)
        if not tasks:
            lines.append(YELLOW + CTA + RESET)
        for t in tasks:
            who = f" @{t['role']}" if t.get("role") else ""
            blocked = bus.blocked_by(t, tasks)
            mark = YELLOW + f" ⧗{','.join(f'#{d}' for d in blocked)}" + RESET \
                if blocked and t["status"] == "todo" else ""
            lines.append(f"  {icon.get(t['status'], '')}#{t['id']} "
                         f"{clip_cells(t['title'], w - 20)}{CYAN}{who}{RESET}{mark}")
        lines.append("")
        lines.append(BOLD + " 連絡" + RESET)
        for m in bus.messages(self.home, limit=max(1, h - len(lines) - 3)):
            lines.append(DIM + f"  {m['ts'][11:]} " + RESET +
                         clip_cells(f"{m['from']}→{m['to']}: {m['text']}", w - 12))
        lines += [""] * max(0, h - 1 - len(lines))
        return lines[: h - 1] + [REV + pad_cells(" b 戻る · n やること · s 連絡 · q 終了", w - 1)]

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
            for agent in self.agents:  # a fresh task wakes the idle team
                if agent.state == agentrun.WAITING:
                    agent.instruct(NUDGE)
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
        taken = {a.role for a in self.agents}
        if not team.valid_role_name(name) or name in taken:
            # description-only input: name the panel automatically
            prompt = text
            n = 2
            while f"mate{n}" in taken:
                n += 1
            name = f"mate{n}"
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
        self.weights.append(1.0)
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
        if self.show_help and ch in ("?", "\x1b"):
            self.show_help = False
            return True
        if ch in ("q", "\x1b", "\x03"):
            # zellij lesson (issue #467, 41 upvotes): confirm before killing
            # a session with live agents; Ctrl-C stays immediate
            if ch != "\x03" and any(a.state == agentrun.RUNNING for a in self.agents):
                self.confirm_quit = True
                return True
            return False
        if ch == "?":
            self.show_help = not self.show_help
        elif ch == "b":
            self.show_board = not self.show_board
        elif ch == "v":
            self.verbose = not self.verbose
        elif ch == "f":
            self.zoom = None if self.zoom == self.focus else self.focus
        elif ch in ("<", ","):
            self.weights[self.focus] = max(0.5, self.weights[self.focus] - 0.15)
        elif ch in (">", "."):
            self.weights[self.focus] = min(3.0, self.weights[self.focus] + 0.15)
        elif ch == term.UP:
            self.scroll[self.focus] = self.scroll.get(self.focus, 0) + 3
        elif ch == term.DOWN:
            self.scroll[self.focus] = max(0, self.scroll.get(self.focus, 0) - 3)
        elif ch in ("\t", term.RIGHT):
            self.focus = (self.focus + 1) % len(self.agents)
            self.zoom = self.focus if self.zoom is not None else None
            self.agents[self.focus].attention = False
        elif ch == term.LEFT:
            self.focus = (self.focus - 1) % len(self.agents)
            self.zoom = self.focus if self.zoom is not None else None
            self.agents[self.focus].attention = False
        elif is_enter:
            self.input_target = "agent"
        elif ch == "n":
            self.input_target = "task"
        elif ch == "s":
            self.input_target = "say"
        elif ch == "a":
            self.input_target = "role"
        elif ch == "K":
            for agent in self.agents:
                agent.interrupt()
        elif ch == "g":
            for agent in self.agents:
                if agent.state in (agentrun.WAITING, agentrun.ERROR):
                    agent.instruct(NUDGE)
        return True

    # ------------------------------------------------------------ loop

    def _dispatch_control(self) -> None:
        """Deliver `zeliji tell` instructions queued from outside."""
        for m in bus.drain_control(self.home):
            prompt = m.get("prompt", "")
            if not prompt:
                continue
            if m.get("by") and m["by"] != "user":
                prompt = f"({m['by']}からの指示) {prompt}"
            for agent in self.agents:
                if m.get("to") in ("all", agent.role):
                    agent.instruct(prompt)

    def _update_title(self) -> None:
        """Team state in the terminal tab (the zellij-emotitle lesson):
        visible even while you work in another window."""
        run = sum(1 for a in self.agents if a.state == agentrun.RUNNING)
        wait = sum(1 for a in self.agents if a.state == agentrun.WAITING)
        att = any(a.attention for a in self.agents)
        title = f"zeliji ●{run} ○{wait}" + (" ❗" if att else "")
        if title != self._title:
            self._title = title
            sys.stdout.write(f"\x1b]0;{title}\x07")

    def loop(self, t: term.Term) -> None:
        while True:
            self._dispatch_control()
            self._update_title()
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
    cp = Cockpit(cfg, home, root, agents)
    try:
        with term.Term() as t:
            cp.loop(t)
    finally:
        cp.save_view()
        sys.stdout.write("\x1b]0;\x07")  # clear the tab title
        for agent in agents:
            agent.stop()
        time.sleep(0.2)
