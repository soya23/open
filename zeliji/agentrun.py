"""Agent driver: runs one role's claude session headlessly.

Instead of emulating a terminal, we consume claude's structured event
stream (`--output-format stream-json`) and keep a timeline of what the
agent is doing. Follow-up instructions resume the same session, so the
agent keeps its context across turns.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from collections import deque
from pathlib import Path

# state machine: idle -> running -> waiting (turn done, resumable) -> running ...
IDLE, RUNNING, WAITING, ERROR = "idle", "running", "waiting", "error"

KICKOFF = (
    "シフト開始。zeliji inbox で未読を確認し、zeliji task list でボードを見て、"
    "着手可能な自分のタスクをclaimして実行すること。コミットまで終えたら task done。"
    "着手可能なタスクがなくなるまで繰り返し、状況を報告して終えること。"
)

# out-of-the-box the agents can edit their own worktree and run the
# coordination commands, nothing broader; set agent_flags in zeliji.toml
# (e.g. --dangerously-skip-permissions) for full autonomy
DEFAULT_FLAGS = [
    "--permission-mode", "acceptEdits",
    "--allowedTools", "Bash(zeliji:*)", "Bash(python -m zeliji:*)", "Bash(git:*)",
]


def _summarize_tool(name: str, inp: dict) -> str:
    detail = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
    detail = str(detail).replace("\n", " ")
    return f"{name} {detail}".strip()


class Agent(threading.Thread):
    def __init__(self, role: str, worktree: Path, charter: Path, home: Path,
                 model: str | None = None, flags: list[str] | None = None):
        super().__init__(daemon=True, name=f"agent-{role}")
        self.role = role
        self.worktree = worktree
        self.charter = charter
        self.home = home
        self.model = model
        self.flags = flags or []
        self.state = IDLE
        self.session_id: str | None = None
        self.events: deque[tuple[str, str]] = deque(maxlen=200)
        self.prompts: queue.Queue[str | None] = queue.Queue()
        self.attention = False  # turn finished and nobody has looked yet
        self._interrupted = False
        self.proc: subprocess.Popen | None = None
        self.log = home / "agents" / f"{role}.jsonl"
        self.log.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ control

    def instruct(self, prompt: str) -> None:
        self.attention = False
        self.prompts.put(prompt)

    def stop(self) -> None:
        self.prompts.put(None)
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def interrupt(self) -> None:
        """Stop the current turn only; the session stays resumable."""
        if self.state == RUNNING and self.proc and self.proc.poll() is None:
            self._interrupted = True
            self.proc.terminate()

    # ------------------------------------------------------------ loop

    def run(self) -> None:
        while True:
            prompt = self.prompts.get()
            if prompt is None:
                return
            try:
                self._turn(prompt)
            except Exception as e:  # keep the cockpit alive whatever happens
                self.state = ERROR
                self.events.append(("error", str(e)))

    def _turn(self, prompt: str) -> None:
        self.state = RUNNING
        self.events.append(("you", prompt))
        import shutil

        claude = shutil.which("claude") or "claude"  # windows needs claude.cmd resolved
        cmd = [
            claude, "-p", prompt,
            "--output-format", "stream-json", "--verbose",
            "--append-system-prompt", self.charter.read_text(encoding="utf-8"),
        ]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        if self.model:
            cmd += ["--model", self.model]
        # the shared bus lives in the main checkout, outside this worktree —
        # without this the agent cannot even read the task board
        cmd += ["--add-dir", str(self.home.parent.resolve())]
        cmd += self.flags
        env = os.environ | {
            "ZELIJI_HOME": str(self.home.resolve()),
            "ZELIJI_ROLE": self.role,
        }
        self.proc = subprocess.Popen(
            cmd, cwd=self.worktree, env=env, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        with open(self.log, "a", encoding="utf-8") as logf:
            for line in self.proc.stdout:
                logf.write(line)
                try:
                    self._handle(json.loads(line))
                except json.JSONDecodeError:
                    continue
        code = self.proc.wait()
        if self._interrupted:
            self._interrupted = False
            self.state = WAITING
            self.events.append(("error", "⏹ 中断しました — Enter で次の指示を"))
        elif code != 0 and self.state != WAITING:
            self.state = ERROR
            self.events.append(("error", f"claude exited with {code}"))
        else:
            self.state = WAITING
        self.attention = True

    def _handle(self, obj: dict) -> None:
        kind = obj.get("type")
        if kind == "system" and obj.get("subtype") == "init":
            self.session_id = obj.get("session_id", self.session_id)
        elif kind == "assistant":
            for block in obj.get("message", {}).get("content", []):
                if block.get("type") == "text" and block.get("text", "").strip():
                    self.events.append(("say", block["text"].strip()))
                elif block.get("type") == "tool_use":
                    self.events.append(
                        ("tool", _summarize_tool(block.get("name", "?"), block.get("input", {})))
                    )
        elif kind == "result":
            self.events.append(("done", (obj.get("result") or "").strip()))


def spawn_team(cfg: dict, home: Path, worktrees: dict[str, Path]) -> list[Agent]:
    project = cfg.get("project", {})
    agents = []
    for role in cfg["role"]:
        name = role["name"]
        agent = Agent(
            role=name,
            worktree=worktrees[name],
            charter=home / "roles" / f"{name}.md",
            home=home,
            model=role.get("model") or project.get("model"),
            flags=list(project.get("agent_flags", DEFAULT_FLAGS)),
        )
        agent.start()
        agent.instruct(project.get("kickoff", KICKOFF))
        agents.append(agent)
    return agents
