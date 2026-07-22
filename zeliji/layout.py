"""Generate the zellij layout that boots the whole team.

One pane per role running `claude` inside that role's worktree with its
charter appended to the system prompt, plus a bottom pane running the
live `zeliji watch` dashboard.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from . import team


def _pane(role: dict, home: Path) -> str:
    name = role["name"]
    wt = team.worktree_path(home, name)
    charter = home / "roles" / f"{name}.md"
    inner = (
        f"export ZELIJI_HOME={shlex.quote(str(home.resolve()))} "
        f"ZELIJI_ROLE={shlex.quote(name)}; "
        f"claude --append-system-prompt \"$(cat {shlex.quote(str(charter))})\""
    )
    escaped = inner.replace('"', '\\"')
    return f"""\
        pane name="{name}" cwd="{wt.resolve()}" {{
            command "bash"
            args "-lc" "{escaped}"
        }}"""


def render(cfg: dict, home: Path) -> str:
    panes = "\n".join(_pane(r, home) for r in cfg["role"])
    watch = (
        f"export ZELIJI_HOME={shlex.quote(str(home.resolve()))}; zeliji watch"
    )
    return f"""\
layout {{
    pane split_direction="vertical" {{
{panes}
    }}
    pane name="zeliji" size="30%" {{
        command "bash"
        args "-lc" "{watch}"
    }}
    pane size=1 borderless=true {{
        plugin location="zellij:status-bar"
    }}
}}
"""


def write(cfg: dict, home: Path) -> Path:
    f = home / "layout.kdl"
    f.write_text(render(cfg, home), encoding="utf-8")
    return f
