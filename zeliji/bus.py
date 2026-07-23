"""Shared bus: the piece zellij is missing.

Every pane (Claude session) talks to the same `.zeliji/bus/` directory in
the main checkout. Tasks live in one JSON file, messages in one JSONL
file, and every mutation goes through an exclusive flock so concurrent
sessions never corrupt state.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

try:  # unix
    import fcntl

    def _lock(fh):
        fcntl.flock(fh, fcntl.LOCK_EX)

    def _unlock(fh):
        fcntl.flock(fh, fcntl.LOCK_UN)
except ImportError:  # windows
    import msvcrt

    def _lock(fh):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock(fh):
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

HOME_ENV = "ZELIJI_HOME"
LINK_FILE = ".zeliji-link"
DIR_NAME = ".zeliji"


class BusError(RuntimeError):
    pass


def find_home(start: Path | None = None) -> Path:
    """Locate the shared .zeliji directory.

    Resolution order: $ZELIJI_HOME, a .zeliji-link pointer file (written
    into each worktree), then an ancestor containing .zeliji/. Worktrees
    live under <main>/.zeliji/worktrees/<role>, so walking up from inside
    one also lands on the right directory.
    """
    env = os.environ.get(HOME_ENV)
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p
        raise BusError(f"{HOME_ENV}={env} is not a directory")

    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        link = p / LINK_FILE
        if link.is_file():
            target = Path(link.read_text(encoding="utf-8").strip()).expanduser()
            if target.is_dir():
                return target
        cand = p / DIR_NAME
        if cand.is_dir():
            return cand
    raise BusError(
        ".zeliji が見つかりません — リポジトリで `zeliji init` (または `zeliji`) を実行してください"
    )


def bus_dir(home: Path) -> Path:
    d = home / "bus"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def locked(home: Path):
    lock = bus_dir(home) / "lock"
    with open(lock, "a+") as fh:
        if fh.seek(0, 2) == 0:  # windows locks need at least one byte
            fh.write(".")
            fh.flush()
        _lock(fh)
        try:
            yield
        finally:
            _unlock(fh)


def _tasks_file(home: Path) -> Path:
    return bus_dir(home) / "tasks.json"


def _messages_file(home: Path) -> Path:
    return bus_dir(home) / "messages.jsonl"


def _load_tasks(home: Path) -> list[dict]:
    f = _tasks_file(home)
    if not f.exists():
        return []
    return json.loads(f.read_text(encoding="utf-8") or "[]")


def _save_tasks(home: Path, tasks: list[dict]) -> None:
    tmp = _tasks_file(home).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_tasks_file(home))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def whoami() -> str:
    return os.environ.get("ZELIJI_ROLE", "user")


# ---------------------------------------------------------------- tasks

def task_add(
    home: Path, title: str, role: str | None = None, after: list[int] | None = None
) -> dict:
    with locked(home):
        tasks = _load_tasks(home)
        ids = {t["id"] for t in tasks}
        for dep in after or []:
            if dep not in ids:
                raise BusError(f"--after {dep}: そのタスクはありません")
        task = {
            "id": 1 + max(ids, default=0),
            "title": title,
            "role": role,
            "status": "todo",
            "after": after or [],
            "by": whoami(),
            "created": _now(),
            "updated": _now(),
        }
        tasks.append(task)
        _save_tasks(home, tasks)
    return task


def blocked_by(task: dict, tasks: list[dict]) -> list[int]:
    """Ids of unfinished dependencies, empty if the task is ready."""
    done = {t["id"] for t in tasks if t["status"] == "done"}
    return [dep for dep in task.get("after", []) if dep not in done]


def task_list(home: Path, status: str | None = None) -> list[dict]:
    tasks = _load_tasks(home)
    if status:
        tasks = [t for t in tasks if t["status"] == status]
    return tasks


def _set_status(home: Path, task_id: int, status: str, role: str | None) -> dict:
    with locked(home):
        tasks = _load_tasks(home)
        for t in tasks:
            if t["id"] == task_id:
                if status == "doing" and t["status"] == "doing" and t["role"] not in (None, role):
                    raise BusError(
                        f"タスク #{task_id} は '{t['role']}' が着手済みです"
                    )
                if status == "doing":
                    blocked = blocked_by(t, tasks)
                    if blocked:
                        deps = ", ".join(f"#{d}" for d in blocked)
                        raise BusError(
                            f"タスク #{task_id} は未完了の {deps} にブロックされています"
                        )
                t["status"] = status
                if role:
                    t["role"] = role
                t["updated"] = _now()
                _save_tasks(home, tasks)
                return t
    raise BusError(f"タスク #{task_id} は存在しません")


def task_claim(home: Path, task_id: int, role: str | None = None) -> dict:
    return _set_status(home, task_id, "doing", role or whoami())


def task_done(home: Path, task_id: int) -> dict:
    return _set_status(home, task_id, "done", None)


# ------------------------------------------------------------- messages

def say(home: Path, to: str, text: str) -> dict:
    msg = {"ts": _now(), "from": whoami(), "to": to, "text": text}
    with locked(home):
        with open(_messages_file(home), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return msg


def messages(home: Path, role: str | None = None, limit: int = 20) -> list[dict]:
    f = _messages_file(home)
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        m = json.loads(line)
        if role is None or m["to"] in ("all", role) or m["from"] == role:
            out.append(m)
    return out[-limit:]


def tell(home: Path, to: str, prompt: str) -> dict:
    """External control (the tmux send-keys lesson): queue an instruction
    for a role's agent; the cockpit dispatches it on its next tick."""
    msg = {"ts": _now(), "by": whoami(), "to": to, "prompt": prompt}
    with locked(home):
        with open(bus_dir(home) / "control.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return msg


def drain_control(home: Path) -> list[dict]:
    """New control messages since the last drain (cursor-tracked)."""
    f = bus_dir(home) / "control.jsonl"
    cursor = bus_dir(home) / "control.cursor"
    with locked(home):
        if not f.exists():
            return []
        lines = [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
        seen = int(cursor.read_text(encoding="utf-8")) if cursor.exists() else 0
        cursor.write_text(str(len(lines)), encoding="utf-8")
    out = []
    for ln in lines[seen:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def inbox(home: Path, role: str) -> list[dict]:
    """Unread messages addressed to `role` (or broadcast), cursor-tracked."""
    f = _messages_file(home)
    cursors = bus_dir(home) / "cursors"
    cursors.mkdir(exist_ok=True)
    cursor = cursors / role
    seen = int(cursor.read_text(encoding="utf-8")) if cursor.exists() else 0
    if not f.exists():
        return []
    lines = [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    fresh = []
    for ln in lines[seen:]:
        m = json.loads(ln)
        if m["to"] in ("all", role) and m["from"] != role:
            fresh.append(m)
    cursor.write_text(str(len(lines)), encoding="utf-8")
    return fresh
