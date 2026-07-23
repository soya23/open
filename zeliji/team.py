"""Team setup: config loading, per-role git worktrees, role charters."""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

from . import bus

CONFIG_NAME = "zeliji.toml"

SAMPLE_CONFIG = """\
# zeliji team definition
[project]
# branch every role's worktree starts from
base_branch = "main"
# gitignored files copied into each new worktree (like .worktreeinclude)
# include = [".env", ".env.local"]
# command run once inside each new worktree
# setup = "npm install"

[[role]]
name = "frontend"
prompt = "You own the UI. Keep components small and accessible."
paths = ["web/", "src/ui/"]

[[role]]
name = "backend"
prompt = "You own the API and data layer. Keep endpoints tested."
paths = ["server/", "src/api/"]

[[role]]
name = "reviewer"
prompt = "You review the other roles' branches and file follow-up tasks."
paths = []
"""

CHARTER_TEMPLATE = """\
# zeliji role: {name}

You are the **{name}** member of a zeliji team — several Claude Code \
sessions working on this repository in parallel, each in its own git \
worktree, coordinating through a shared bus.

## Your role
{prompt}

{paths_section}\
## Ground rules
- Work only inside this worktree; commit to your branch `{branch}`.
- Do not edit files owned by other roles — file a task for them instead.

## Coordinating with the team (run these with Bash)
- `zeliji task list` — see the shared task board.
- `zeliji task add "<title>" [--role <name>] [--after <id>]` — file work \
for yourself or others; `--after` marks a dependency.
- `zeliji task claim <id>` — claim a task before starting it (fails if \
taken or blocked by an unfinished dependency).
- `zeliji task done <id>` — mark your task finished.
- `zeliji say <role>|all "<text>"` — message a teammate or everyone.
- `zeliji inbox` — read your unread messages. **Check this and the task \
board between tasks and before starting anything new.**
- `zeliji status` — worktree states and cross-role file-overlap warnings.
"""


class TeamError(RuntimeError):
    pass


# role names become git branch names and directory names — keep them tame
ROLE_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")


def valid_role_name(name: str) -> bool:
    return bool(ROLE_NAME_RE.fullmatch(name))


def load_config(root: Path) -> dict:
    f = root / CONFIG_NAME
    if not f.exists():
        raise TeamError(f"{root} に {CONFIG_NAME} がありません — `zeliji` で作成できます")
    # utf-8-sig: Windows Notepad may save with a BOM, which tomllib rejects
    cfg = tomllib.loads(f.read_text(encoding="utf-8-sig"))
    roles = cfg.get("role", [])
    if not roles:
        raise TeamError(f"{CONFIG_NAME} defines no [[role]] entries")
    names = [r["name"] for r in roles]
    if len(names) != len(set(names)):
        raise TeamError("duplicate role names in zeliji.toml")
    for n in names:
        if not valid_role_name(n):
            raise TeamError(
                f"role name '{n}' is invalid — use only letters, digits, - and _"
            )
    return cfg


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def role_toml(role: dict) -> str:
    lines = ["[[role]]", f"name = {_toml_str(role['name'])}",
             f"prompt = {_toml_str(role.get('prompt', ''))}"]
    if role.get("paths"):
        lines.append("paths = [" + ", ".join(_toml_str(p) for p in role["paths"]) + "]")
    return "\n".join(lines) + "\n"


def write_config(root: Path, base_branch: str, roles: list[dict],
                 mode: str = "pair") -> Path:
    parts = ["# zeliji team — `zeliji up` で起動\n[project]\n"
             f"base_branch = {_toml_str(base_branch)}\n"
             f"mode = {_toml_str(mode)}  # mirror=提案のみ / pair=協調(既定) / rogue=全自動\n"]
    for r in roles:
        parts.append("\n" + role_toml(r))
    f = root / CONFIG_NAME
    f.write_text("".join(parts), encoding="utf-8")
    return f


def append_role(root: Path, role: dict) -> None:
    if not valid_role_name(role["name"]):
        raise TeamError(f"invalid role name: {role['name']}")
    with open(root / CONFIG_NAME, "a", encoding="utf-8") as fh:
        fh.write("\n" + role_toml(role))


def _git(root: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, encoding="utf-8", errors="replace"
    )
    if res.returncode != 0:
        raise TeamError(f"git {' '.join(args)}: {res.stderr.strip()}")
    return res.stdout.strip()


def branch_for(role: str) -> str:
    return f"zeliji/{role}"


def worktree_path(home: Path, role: str) -> Path:
    return home / "worktrees" / role


def ensure_worktree(root: Path, home: Path, role: str, cfg: dict) -> Path:
    project = cfg.get("project", {})
    base = project.get("base_branch", "main")
    path = worktree_path(home, role)
    if path.is_dir():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    branch = branch_for(role)
    existing = _git(root, "branch", "--list", branch)
    if existing:
        _git(root, "worktree", "add", str(path), branch)
    else:
        _git(root, "worktree", "add", "-b", branch, str(path), base)
    # pointer back to the shared bus, so `zeliji` works from inside
    (path / bus.LINK_FILE).write_text(str(home.resolve()) + "\n", encoding="utf-8")
    _ensure_ignored(path, bus.LINK_FILE)
    for rel in project.get("include", []):
        src = root / rel
        if src.is_file():
            dst = path / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    setup = project.get("setup")
    if setup:
        res = subprocess.run(setup, shell=True, cwd=path, capture_output=True, encoding="utf-8", errors="replace")
        if res.returncode != 0:
            raise TeamError(f"setup failed in {path}: {res.stderr.strip()[-500:]}")
    return path


def _ensure_ignored(worktree: Path, pattern: str) -> None:
    # git only reads the *common* info/exclude, so per-worktree exclude
    # files would be silently ignored
    common = Path(_git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    info = common / "info"
    info.mkdir(parents=True, exist_ok=True)
    ex = info / "exclude"
    lines = ex.read_text(encoding="utf-8").splitlines() if ex.exists() else []
    if pattern not in lines:
        ex.write_text("\n".join([*lines, pattern]) + "\n", encoding="utf-8")


def write_charter(home: Path, role: dict) -> Path:
    roles_dir = home / "roles"
    roles_dir.mkdir(parents=True, exist_ok=True)
    paths = role.get("paths") or []
    if paths:
        owned = "\n".join(f"- `{p}`" for p in paths)
        paths_section = f"## Files you own\n{owned}\n\n"
    else:
        paths_section = ""
    charter = CHARTER_TEMPLATE.format(
        name=role["name"],
        prompt=role.get("prompt", "(no specific brief)"),
        branch=branch_for(role["name"]),
        paths_section=paths_section,
    )
    f = roles_dir / f"{role['name']}.md"
    f.write_text(charter, encoding="utf-8")
    return f


def progress(root: Path, cfg: dict, role: str) -> tuple[int, int]:
    """(commits ahead of base, files changed) for a role's branch."""
    base = cfg.get("project", {}).get("base_branch", "main")
    branch = branch_for(role)
    if not _git(root, "branch", "--list", branch):
        return (0, 0)
    commits = int(_git(root, "rev-list", "--count", f"{base}..{branch}") or 0)
    files = _git(root, "diff", "--name-only", f"{base}...{branch}")
    return (commits, len(files.splitlines()) if files else 0)


def merge_roles(
    root: Path, cfg: dict, roles: list[str], cleanup: bool = False
) -> list[str]:
    """Merge each role branch into the base branch; stop on conflict."""
    base = cfg.get("project", {}).get("base_branch", "main")
    current = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if current != base:
        raise TeamError(
            f"本体が '{current}' ブランチにいます — 先に `git checkout {base}`"
        )
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise TeamError("本体に未コミットの変更があります — 先にコミットかstashを")

    home = root / bus.DIR_NAME
    report = []
    for role in roles:
        branch = branch_for(role)
        commits, files = progress(root, cfg, role)
        wt = worktree_path(home, role)
        if commits == 0:
            if cleanup and _git(root, "branch", "--list", branch):
                merged = branch in _git(
                    root, "branch", "--format=%(refname:short)", "--merged", base
                ).splitlines()
                if merged:
                    _remove_role(root, wt, branch)
                    report.append(f"{role}: マージ済み — 作業場とブランチを片付けました")
                    continue
            report.append(f"{role}: マージするものなし")
            continue
        if wt.is_dir() and _git(wt, "status", "--porcelain", "--untracked-files=no"):
            raise TeamError(
                f"{role}: 作業場に未コミットの変更があります — 先にコミットを"
            )
        try:
            _git(root, "merge", "--no-ff", branch, "-m", f"merge {branch} ({commits} commits)")
        except TeamError as e:
            _git(root, "merge", "--abort")
            raise TeamError(
                f"{role}: コンフリクト — `git merge {branch}` で手動解決してください"
            ) from e
        report.append(f"{role}: {commits}コミット · {files}ファイルをマージ")
        if cleanup:
            _remove_role(root, wt, branch)
            report.append(f"{role}: 作業場とブランチを片付けました")
    return report


def _remove_role(root: Path, wt: Path, branch: str) -> None:
    # the branch is merged at this point, so stray untracked files in the
    # worktree (agent scratch, copied includes) are safe to discard
    if wt.is_dir():
        _git(root, "worktree", "remove", "--force", str(wt))
    _git(root, "branch", "-d", branch)


def overlap_report(root: Path, cfg: dict) -> list[str]:
    """Warn when two roles' branches touch the same file vs the base."""
    base = cfg.get("project", {}).get("base_branch", "main")
    touched: dict[str, list[str]] = {}
    for role in cfg["role"]:
        branch = branch_for(role["name"])
        if not _git(root, "branch", "--list", branch):
            continue
        try:
            files = _git(root, "diff", "--name-only", f"{base}...{branch}")
        except TeamError:
            continue
        for f in files.splitlines():
            touched.setdefault(f, []).append(role["name"])
    return [
        f"⚠ {f} edited by: {', '.join(roles)}"
        for f, roles in sorted(touched.items())
        if len(roles) > 1
    ]
