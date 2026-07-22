"""zeliji command line interface."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import bus, layout, team


def _home() -> Path:
    return bus.find_home()


def cmd_init(args: argparse.Namespace) -> int:
    root = Path.cwd()
    if not (root / ".git").exists():
        print("zeliji init must run at the root of a git repository", file=sys.stderr)
        return 1
    cfg = root / team.CONFIG_NAME
    if cfg.exists():
        print(f"{team.CONFIG_NAME} already exists — edit it to define your roles")
    else:
        cfg.write_text(team.SAMPLE_CONFIG, encoding="utf-8")
        print(f"wrote {team.CONFIG_NAME} — edit the roles, then run `zeliji up`")
    (root / bus.DIR_NAME).mkdir(exist_ok=True)
    gitignore = root / ".gitignore"
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    if bus.DIR_NAME + "/" not in lines:
        gitignore.write_text("\n".join([*lines, bus.DIR_NAME + "/"]) + "\n", encoding="utf-8")
        print("added .zeliji/ to .gitignore")
    print("next: edit zeliji.toml, commit it with .gitignore, then `zeliji up`")
    return 0


TEMPLATES: dict[str, tuple[str, list[dict]]] = {
    "1": ("おまかせ開発チーム (作る人+見る人)", [
        {"name": "builder", "prompt": "実装担当。ボードのタスクを実装し、動作確認してコミットする。"},
        {"name": "reviewer", "prompt": "レビュー担当。builderのブランチを読んで問題や改善点をタスクとして起票する。自分では修正しない。"},
    ]),
    "2": ("Web開発チーム", [
        {"name": "frontend", "prompt": "UI担当。画面まわりを実装する。"},
        {"name": "backend", "prompt": "API・データ担当。サーバ側を実装する。"},
        {"name": "reviewer", "prompt": "レビュー担当。他の2人のブランチを確認し、問題をタスクとして起票する。"},
    ]),
    "3": ("文書チーム (書く人+直す人)", [
        {"name": "writer", "prompt": "執筆担当。ボードのタスクに沿って文書を書く。"},
        {"name": "editor", "prompt": "編集担当。writerの文章を読みやすく直し、矛盾があればタスクを切る。"},
    ]),
}


def _ask(prompt: str, default: str = "") -> str:
    try:
        ans = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(1)
    return ans or default


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


def wizard(root: Path) -> Path | None:
    """No-config path for non-engineers: questions in, working team out."""
    print("zeliji セットアップ — 質問に答えるだけで始められます (Ctrl+Cで中止)\n")
    if not (root / ".git").exists():
        print(f"このフォルダ ({root}) はまだ作業場 (gitリポジトリ) ではありません。")
        choice = _ask("  1) このフォルダをそのまま使う  2) 新しいフォルダを作る  [1/2]: ", "1")
        if choice == "2":
            name = _ask("  フォルダ名 [zeliji-playground]: ", "zeliji-playground")
            root = root / name
            root.mkdir(exist_ok=True)
            os.chdir(root)
        _run(["git", "init", "-b", "main"], root)
        ident = subprocess.run(["git", "config", "user.name"], cwd=root,
                               capture_output=True, encoding="utf-8")
        if not ident.stdout.strip():
            who = _ask("  コミットに記録する名前 [me]: ", "me")
            _run(["git", "config", "user.name", who], root)
            _run(["git", "config", "user.email", f"{who}@local"], root)
        _run(["git", "commit", "--allow-empty", "-m", "init"], root)
        print("  ✔ 作業場を用意しました\n")

    print("チーム構成を選んでください:")
    for key, (label, roles) in TEMPLATES.items():
        print(f"  {key}) {label}  ({' / '.join(r['name'] for r in roles)})")
    choice = _ask("  [1]: ", "1")
    roles = TEMPLATES.get(choice, TEMPLATES["1"])[1]

    auto = _ask("\nエージェントに確認なしで自由に作業させますか?\n"
                "  (y = 全自動で速いが、信頼できる作業場でのみ推奨 / N = 安全な既定)  [y/N]: ").lower() == "y"

    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root,
                            capture_output=True, encoding="utf-8").stdout.strip() or "main"
    team.write_config(root, branch, roles, autonomous=auto)
    gitignore = root / ".gitignore"
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    if bus.DIR_NAME + "/" not in lines:
        gitignore.write_text("\n".join([*lines, bus.DIR_NAME + "/"]) + "\n", encoding="utf-8")
    _run(["git", "add", team.CONFIG_NAME, ".gitignore"], root)
    _run(["git", "commit", "-m", "zeliji setup"], root)
    print(f"\n✔ 設定完了 ({team.CONFIG_NAME} に保存・コミット済み)。cockpitを起動します…\n")
    return root


def cmd_up(args: argparse.Namespace) -> int:
    root = Path.cwd()
    if not (root / team.CONFIG_NAME).exists():
        new_root = wizard(root)
        if new_root is None:
            return 1
        root = new_root
    cfg = team.load_config(root)
    home = root / bus.DIR_NAME
    home.mkdir(exist_ok=True)

    for role in cfg["role"]:
        wt = team.ensure_worktree(root, home, role["name"], cfg)
        team.write_charter(home, role)
        print(f"role {role['name']}: worktree {wt} (branch {team.branch_for(role['name'])})")

    if args.zellij:
        kdl = layout.write(cfg, home)
        print(f"layout: {kdl}")
        if args.dry_run:
            print(f"\nnext: `zellij --layout {kdl}`")
            return 0
        if shutil.which("zellij") is None:
            print(f"\nzellij not found on PATH — run: zellij --layout {kdl}", file=sys.stderr)
            return 1
        os.execvp("zellij", ["zellij", "--layout", str(kdl)])

    if args.dry_run:
        print("\nnext: rerun without --dry-run to open the cockpit")
        return 0
    if shutil.which("claude") is None:
        print("claude CLI not found on PATH — install Claude Code first", file=sys.stderr)
        return 1
    from . import cockpit

    cockpit.run(cfg, home, root)
    return 0


def cmd_task(args: argparse.Namespace) -> int:
    home = _home()
    if args.task_cmd == "add":
        t = bus.task_add(home, args.title, args.role, args.after)
        deps = f" (after {', '.join(f'#{d}' for d in t['after'])})" if t["after"] else ""
        print(f"added #{t['id']}: {t['title']}" + (f" @{t['role']}" if t["role"] else "") + deps)
        print(f"next: `zeliji task claim {t['id']}` to start it")
    elif args.task_cmd == "list":
        tasks = bus.task_list(home)
        shown = [t for t in tasks if not args.status or t["status"] == args.status]
        if not shown:
            print('(empty — `zeliji task add "..."` to file work)')
        for t in shown:
            who = f" @{t['role']}" if t.get("role") else ""
            blocked = bus.blocked_by(t, tasks)
            mark = f" ⧗blocked by {', '.join(f'#{d}' for d in blocked)}" if blocked and t["status"] == "todo" else ""
            print(f"[{t['status']:5}] #{t['id']} {t['title']}{who}{mark}")
    elif args.task_cmd == "claim":
        t = bus.task_claim(home, args.id, args.role)
        print(f"claimed #{t['id']} as {t['role']} — `zeliji task done {t['id']}` when finished")
    elif args.task_cmd == "done":
        t = bus.task_done(home, args.id)
        print(f"done #{t['id']}: {t['title']}")
    return 0


def cmd_say(args: argparse.Namespace) -> int:
    bus.say(_home(), args.to, args.text)
    print(f"→ {args.to}: {args.text}")
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    role = args.role or os.environ.get("ZELIJI_ROLE")
    if not role:
        print("no role — pass --role or set ZELIJI_ROLE", file=sys.stderr)
        return 1
    fresh = bus.inbox(_home(), role)
    if not fresh:
        print("(no new messages)")
    for m in fresh:
        print(f"{m['ts']} {m['from']} → {m['to']}: {m['text']}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    home = _home()
    root = home.parent
    cfg = team.load_config(root)
    for role in cfg["role"]:
        name = role["name"]
        wt = team.worktree_path(home, name)
        if not wt.is_dir():
            print(f"{name:12} (no worktree — run `zeliji up`)")
            continue
        res = subprocess.run(
            ["git", "-C", str(wt), "status", "--porcelain"],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        dirty = len([ln for ln in res.stdout.splitlines() if ln.strip()])
        state = f"{dirty} uncommitted" if dirty else "clean"
        commits, files = team.progress(root, cfg, name)
        ahead = f"{commits} commit(s), {files} file(s) vs base" if commits else "no commits yet"
        print(f"{name:12} {team.branch_for(name):24} {state:14} {ahead}")
    warnings = team.overlap_report(root, cfg)
    if warnings:
        print()
        for w in warnings:
            print(w)
    else:
        print("\nno cross-role file overlaps ✔")
    if any(team.progress(root, cfg, r["name"])[0] for r in cfg["role"]):
        print("harvest with `zeliji merge` (add --cleanup to also remove worktrees)")
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    home = _home()
    root = home.parent
    cfg = team.load_config(root)
    warnings = team.overlap_report(root, cfg)
    for w in warnings:
        print(w)
    roles = args.roles or [r["name"] for r in cfg["role"]]
    known = {r["name"] for r in cfg["role"]}
    unknown = [r for r in roles if r not in known]
    if unknown:
        print(f"zeliji: unknown role(s): {', '.join(unknown)}", file=sys.stderr)
        return 1
    for line in team.merge_roles(root, cfg, roles, cleanup=args.cleanup):
        print(line)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    from . import watch

    home = _home()
    watch.run(home, interval=args.interval)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zeliji",
        description="zellij evolved — role-based Claude sessions with shared state",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create zeliji.toml and the shared bus")

    up = sub.add_parser("up", help="create worktrees + charters, open the cockpit")
    up.add_argument("--dry-run", action="store_true", help="prepare everything but do not launch")
    up.add_argument("--zellij", action="store_true",
                    help="legacy bridge: raw claude terminals in a zellij layout instead of the cockpit")

    task = sub.add_parser("task", help="shared task board")
    tsub = task.add_subparsers(dest="task_cmd", required=True)
    t_add = tsub.add_parser("add")
    t_add.add_argument("title")
    t_add.add_argument("--role", help="assign to a role")
    t_add.add_argument("--after", type=int, action="append",
                       help="task id this depends on (repeatable)")
    t_list = tsub.add_parser("list")
    t_list.add_argument("--status", choices=["todo", "doing", "done"])
    t_claim = tsub.add_parser("claim")
    t_claim.add_argument("id", type=int)
    t_claim.add_argument("--role")
    t_done = tsub.add_parser("done")
    t_done.add_argument("id", type=int)

    say = sub.add_parser("say", help="message a role, or `all`")
    say.add_argument("to")
    say.add_argument("text")

    inbox = sub.add_parser("inbox", help="read unread messages for your role")
    inbox.add_argument("--role")

    sub.add_parser("status", help="worktree states, progress, conflict watch")

    merge = sub.add_parser("merge", help="merge role branches into the base branch")
    merge.add_argument("roles", nargs="*", help="roles to merge (default: all)")
    merge.add_argument("--cleanup", action="store_true",
                       help="remove worktree and branch after a successful merge")

    watch = sub.add_parser("watch", help="live dashboard (used by the layout)")
    watch.add_argument("--interval", type=float, default=2.0)

    return p


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:  # bare `zeliji` = the friendly path: wizard or cockpit
        argv = ["up"]
    args = build_parser().parse_args(argv)
    handlers = {
        "init": cmd_init,
        "up": cmd_up,
        "task": cmd_task,
        "say": cmd_say,
        "inbox": cmd_inbox,
        "status": cmd_status,
        "merge": cmd_merge,
        "watch": cmd_watch,
    }
    try:
        return handlers[args.cmd](args)
    except (bus.BusError, team.TeamError) as e:
        print(f"zeliji: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
