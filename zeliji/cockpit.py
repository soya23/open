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

HINT_CHIPS = [("m", "メニュー"), ("n", "やること"), ("Enter", "指示"),
              ("Tab", "移動"), ("b", "ボード"), ("?", "全キー"), ("q", "終了")]

# well-worn role presets: pick one, or write your own at the bottom
ROLE_PRESETS = [
    ("tester", "テスト担当", "実際に動かして壊れ方を探し、再現手順つきでタスクを起票する。修正はしない。"),
    ("reviewer", "レビュー担当", "他の役割のブランチを読み、問題や改善点をタスクとして起票する。自分では修正しない。"),
    ("docs", "ドキュメント係", "READMEやdocsをコードの現状に合わせて最新に保つ。食い違いはタスクを切る。"),
    ("cleaner", "整理係", "重複コードや読みにくい箇所を、挙動を変えずに読みやすく整理する。"),
    ("ideas", "企画係", "ボードと成果を眺め、次にやるべき改善をタスクとして提案する。実装はしない。"),
]
HINTS_INPUT = " Enter 送信 · Esc 戻る"
AUTONOMOUS_FLAG = "--dangerously-skip-permissions"
NUDGE = "ボードとinboxを再確認し、着手可能なタスクがあれば続けて。なければ待機と報告して。"
CTA = " ✎ まず n を押して、やってほしいことを書いてください (例: READMEを読みやすくして)"

HELP = """\
 キー一覧  (? か Esc で戻る)

   m        メニューを開く — 全機能を↑↓とEnterだけで使える。迷ったらまず m
   n        やってほしいことを書く (タスクとしてボードに載り、手が空いた役割が拾う)
   Enter    選択中の役割に直接指示する
   Tab / ←→ 役割を選ぶ
   ↑ / ↓    選択中の列の履歴をさかのぼる / 戻る
   f        選択中の列を全画面に (もう一度で戻る)
   < / >    選択中の列の幅を調整 (, と . でも可)
   b        タスクボードを全画面で見る
   s        連絡を送る (例: all 今日はここまで / core 先にテスト書いて)
   a        役割を追加 — よく使う役割 (テスト/レビュー/ドキュメント等) から
            選ぶか、最下段の「自由に書く」で文章から作る
   g        待機中の全員に「続けて」
   v        詳細表示の切替 (ツール実行ログを全文で見る)
   K        全員の作業をいますぐ中断 (緊急停止。セッションは残る)
   Esc      ひとつ戻る (入力・メニュー・スクロールの解除)。終了は q だけ
   ↑ (入力中) 前に送った内容を呼び出す / ↓ で戻す
   q        終了 (作業中の役割がいれば確認します)

 成果の回収 (merge) はメニュー (m) から。タスクの削除は `zeliji task drop <id>`
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
        self.menu: int | None = None  # selected row of the action menu
        self.role_menu: int | None = None  # selected row of the role picker
        self.overlay: list[str] | None = None  # transient result screen
        self._flash: tuple[str, float] | None = None
        self.history: dict[str, list[str]] = {}
        self._hist_pos = 0
        self.lockfile: Path | None = None
        self._load_view()

    def flash(self, text: str) -> None:
        """Feedback for every action: shown in the footer for a few seconds."""
        self._flash = (text, time.time() + 4)

    # ------------------------------------------------------------ menu
    # the zellij lesson taken to its end: every action is on screen,
    # picked with arrows+Enter — nothing to memorize

    def _menu_items(self) -> list[tuple[str, str, callable]]:
        def start_input(target):
            def go():
                self.input_target = target
            return go

        def toggle(attr):
            def go():
                setattr(self, attr, not getattr(self, attr))
            return go

        def nudge():
            hit = [a.role for a in self.agents if a.state in (agentrun.WAITING, agentrun.ERROR)]
            for a in self.agents:
                if a.state in (agentrun.WAITING, agentrun.ERROR):
                    a.instruct(NUDGE)
            self.flash(f"→ {', '.join(hit)} に「続けて」を送りました" if hit else "全員作業中です")

        def stop_all():
            for a in self.agents:
                a.interrupt()
            self.flash("⏹ 全員の作業を中断しました (Enter で再指示できます)")

        focused = self.agents[self.focus].role if self.agents else "?"
        return [
            ("やってほしいことを書く", "ボードに載り、手が空いた役割が拾う (n)", start_input("task")),
            (f"{focused} に指示する", "選択中の役割へ直接。Tabで役割を変えられる (Enter)", start_input("agent")),
            ("役割を追加する", "よく使う役割から選ぶか、自由に書く (a)", self._open_role_picker),
            ("連絡を送る", "例: all 今日はここまで (s)", start_input("say")),
            ("タスクボードを見る", "全タスクと連絡の一覧 (b)", toggle("show_board")),
            ("全員に「続けて」", "待機中・エラーの役割を再稼働 (g)", nudge),
            ("詳細表示の切替", "ツール実行ログを全文で見る (v)", toggle("verbose")),
            ("全員の作業を中断", "緊急停止。セッションは残る (K)", stop_all),
            ("成果を回収する (merge)", "全役割のブランチを取り込む。安全確認つき", self._do_merge),
            ("キー一覧", "全ショートカットの説明 (?)", toggle("show_help")),
        ]

    def _open_role_picker(self) -> None:
        self.role_menu = 0

    def _compose_role_menu(self, w: int, h: int) -> list[str]:
        rows = [(label, desc) for _, label, desc in ROLE_PRESETS]
        rows.append(("自由に書く", "やってほしいことを文章で (名前は自動で付く)"))
        self.role_menu = min(self.role_menu or 0, len(rows) - 1)
        lines = [BOLD + " 役割を追加" + RESET + DIM + "  — よく使うものは選ぶだけ" + RESET, ""]
        for i, (label, desc) in enumerate(rows):
            cursor = "▶ " if i == self.role_menu else "  "
            style = REV if i == self.role_menu else ""
            lines.append(style + f" {cursor}{pad_cells(label, 16)}" + RESET + DIM + f" {desc}" + RESET)
        lines += [""] * max(0, h - 1 - len(lines))
        return lines[: h - 1] + [REV + pad_cells(" ↑↓ 選ぶ · Enter 追加 · Esc 閉じる", w - 1)]

    def _do_merge(self) -> None:
        try:
            report = team.merge_roles(self.root, self.cfg,
                                      [r["name"] for r in self.cfg["role"]])
            self.overlay = [BOLD + " 回収結果" + RESET, ""] + \
                [f"  {ln}" for ln in report] + \
                ["", DIM + "  worktreeとブランチの掃除は `zeliji merge --cleanup`" + RESET]
        except team.TeamError as e:
            self.overlay = [BOLD + " 回収できませんでした" + RESET, "", f"  {YELLOW}{e}{RESET}"]
        self.overlay += ["", DIM + "  (何かキーを押すと戻ります)" + RESET]

    def _compose_menu(self, w: int, h: int) -> list[str]:
        items = self._menu_items()
        self.menu = min(self.menu or 0, len(items) - 1)
        lines = [BOLD + " メニュー" + RESET + DIM + "  — 覚えるキーはこれだけ: m" + RESET, ""]
        for i, (label, desc, _) in enumerate(items):
            cursor = "▶ " if i == self.menu else "  "
            style = REV if i == self.menu else ""
            lines.append(style + f" {cursor}{pad_cells(label, 26)}" + RESET + DIM + f" {desc}" + RESET)
        lines += [""] * max(0, h - 1 - len(lines))
        return lines[: h - 1] + [REV + pad_cells(" ↑↓ 選ぶ · Enter 実行 · Esc 閉じる", w - 1)]

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
        if self.overlay is not None:
            lines = list(self.overlay)
            lines += [""] * max(0, h - len(lines))
            return lines[:h]
        if self.role_menu is not None:
            return self._compose_role_menu(w, h)
        if self.menu is not None:
            return self._compose_menu(w, h)
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
        if self.input_target:
            msg = HINTS_INPUT  # while typing, show how to finish or back out
        elif self._flash and time.time() < self._flash[1]:
            msg = CYAN + " " + self._flash[0]
        else:
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
        else:
            last = self._chip_bar(w)
        return [DIM + "─" * (w - 1), board, DIM + pad_cells(msg, w - 1), last]

    def _chip_bar(self, w: int) -> str:
        """zellij-style key chips: the key stands out, the label explains."""
        parts = []
        used = 0
        if self.autonomous:
            parts.append(REV + YELLOW + " ⚠AUTO " + RESET)
            used += 7
        for key, label in HINT_CHIPS:
            width = cells(key) + cells(label) + 4
            if used + width > w - 1:
                break
            parts.append(REV + BOLD + f" {key} " + RESET + DIM + f"{label}  " + RESET)
            used += width
        return "".join(parts)

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
        self.history.setdefault(target, []).append(text)
        if target == "agent":
            agent = self.agents[self.focus]
            busy = agent.state == agentrun.RUNNING
            agent.instruct(text)
            self.flash(f"◷ {agent.role} は作業中 — 今の作業が終わり次第伝えます"
                       if busy else f"→ {agent.role} に指示しました")
        elif target == "task":
            t = bus.task_add(self.home, text)
            woken = [a.role for a in self.agents if a.state == agentrun.WAITING]
            for agent in self.agents:  # a fresh task wakes the idle team
                if agent.state == agentrun.WAITING:
                    agent.instruct(NUDGE)
            self.flash(f"✎ タスク #{t['id']} を追加" +
                       (f" — {', '.join(woken)} が確認に向かいます" if woken
                        else " — 手が空いた役割が拾います"))
        elif target == "say":
            to, _, body = text.partition(" ")
            roles = {a.role for a in self.agents}
            if to in roles | {"all"} and body:
                bus.say(self.home, to, body)
                self.flash(f"連絡を {to} に送りました")
            else:
                bus.say(self.home, "all", text)
                self.flash("連絡を全員に送りました")
        elif target == "role":
            self._add_role(text)

    def _unique_name(self, base: str) -> str:
        taken = {a.role for a in self.agents}
        if base not in taken:
            return base
        n = 2
        while f"{base}{n}" in taken:
            n += 1
        return f"{base}{n}"

    def _add_role(self, text: str) -> None:
        name, _, prompt = text.partition(" ")
        if not team.valid_role_name(name):
            # description-only input: name the panel automatically
            name, prompt = "mate", text
        self._create_role(self._unique_name(name), prompt)

    def _create_role(self, name: str, prompt: str) -> None:
        role = {"name": name, "prompt": prompt or "チームの一員として、ボードのタスクを手伝う。"}
        try:
            team.append_role(self.root, role)
            self.cfg["role"].append(role)
            team.ensure_worktree(self.root, self.home, name, self.cfg)
            team.write_charter(self.home, role)
        except team.TeamError as e:
            self.flash(f"✖ 役割を追加できませんでした: {e}")
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
        self.flash(f"＋ 役割 {name} が参加しました")

    def _key(self, ch: str) -> bool:
        """Handle one key from Term.read_keys; return False to quit."""
        is_enter = ch in ("\n", "\r")
        if self.overlay is not None:  # any key dismisses a result screen
            self.overlay = None
            return True
        if self.input_target is not None:
            hist = self.history.get(self.input_target, [])
            if ch == "\x1b":
                self.buffer, self.input_target = "", None
                self._hist_pos = 0
            elif is_enter:
                self._submit()
                self._hist_pos = 0
            elif ch == term.UP and hist:  # recall previous inputs
                self._hist_pos = min(self._hist_pos + 1, len(hist))
                self.buffer = hist[-self._hist_pos]
            elif ch == term.DOWN and hist:
                self._hist_pos = max(self._hist_pos - 1, 0)
                self.buffer = hist[-self._hist_pos] if self._hist_pos else ""
            elif ch in ("\x7f", "\x08"):
                self.buffer = self.buffer[:-1]
            elif len(ch) == 1 and ch.isprintable():
                self.buffer += ch
            return True
        if self.role_menu is not None:
            total = len(ROLE_PRESETS) + 1
            if ch == term.UP:
                self.role_menu = (self.role_menu - 1) % total
            elif ch == term.DOWN or ch == "\t":
                self.role_menu = (self.role_menu + 1) % total
            elif is_enter:
                idx = self.role_menu
                self.role_menu = None
                if idx < len(ROLE_PRESETS):
                    base, _, prompt = ROLE_PRESETS[idx]
                    self._create_role(self._unique_name(base), prompt)
                else:
                    self.input_target = "role"
            elif ch in ("\x1b", "a", "q"):
                self.role_menu = None
            return True
        if self.menu is not None:
            items = self._menu_items()
            if ch == term.UP:
                self.menu = (self.menu - 1) % len(items)
            elif ch == term.DOWN or ch == "\t":
                self.menu = (self.menu + 1) % len(items)
            elif is_enter:
                action = items[self.menu][2]
                self.menu = None
                action()
            elif ch in ("\x1b", "m", "q"):
                self.menu = None
            return True
        if self.confirm_quit:
            self.confirm_quit = False
            return ch != "q"  # q confirms quit; anything else just cancels
        if self.show_help and ch in ("?", "\x1b"):
            self.show_help = False
            return True
        if ch in ("q", "\x03"):
            # zellij lesson (issue #467, 41 upvotes): confirm before killing
            # a session with live agents; Ctrl-C stays immediate
            if ch == "q" and any(a.state == agentrun.RUNNING for a in self.agents):
                self.confirm_quit = True
                return True
            return False
        if ch == "\x1b":  # Esc means "back", never "quit"
            if self.scroll.get(self.focus):
                self.scroll[self.focus] = 0
            self.agents[self.focus].attention = False
            return True
        if ch == "m":
            self.menu = 0
        elif ch == "?":
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
            self.role_menu = 0
        elif ch == "K":
            for agent in self.agents:
                agent.interrupt()
            self.flash("⏹ 全員の作業を中断しました (Enter で再指示できます)")
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

    def _auto_assign(self) -> None:
        """A waiting agent is pointed at the board whenever it contains
        ready unclaimed work it hasn't been shown yet — tasks added at
        any time get picked up, not just tasks added while idle."""
        tasks = bus.task_list(self.home)
        ready = tuple(sorted(
            t["id"] for t in tasks
            if t["status"] == "todo" and not bus.blocked_by(t, tasks)))
        if not ready:
            return
        for agent in self.agents:
            if agent.state == agentrun.WAITING and getattr(agent, "_auto_key", None) != ready:
                agent._auto_key = ready
                agent.instruct(NUDGE)

    def loop(self, t: term.Term) -> None:
        while True:
            if self.lockfile is not None:  # heartbeat against double cockpits
                try:
                    self.lockfile.touch()
                except OSError:
                    pass
            self._dispatch_control()
            self._auto_assign()
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
    lock = home / "cockpit.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 10:
        print("この作業場では既に別の cockpit が動いています (二重起動するとエージェントが重複します)",
              file=sys.stderr)
        print("異常終了の直後なら10秒待つか、.zeliji/cockpit.lock を削除してください",
              file=sys.stderr)
        return
    lock.parent.mkdir(exist_ok=True)
    lock.touch()
    worktrees = {r["name"]: team.worktree_path(home, r["name"]) for r in cfg["role"]}
    agents = agentrun.spawn_team(cfg, home, worktrees)
    cp = Cockpit(cfg, home, root, agents)
    cp.lockfile = lock
    try:
        with term.Term() as t:
            cp.loop(t)
    finally:
        cp.save_view()
        lock.unlink(missing_ok=True)
        sys.stdout.write("\x1b]0;\x07")  # clear the tab title
        for agent in agents:
            agent.stop()
        time.sleep(0.2)
