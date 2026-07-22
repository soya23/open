# zeliji

**zellij evolved** — zellij は画面を分割してくれるが、ペイン同士は互いを知らない。
zeliji はそこに「チーム」を足す: 役割ごとの Claude Code セッションを
git worktree で隔離しつつ、共有タスクボードとメッセージングで繋ぐ。

```
┌──────────────┬──────────────┬──────────────┐
│  frontend    │  backend     │  reviewer    │   ← 各ペイン = claude
│  (worktree)  │  (worktree)  │  (worktree)  │      役割チャーター入り
├──────────────┴──────────────┴──────────────┤
│  zeliji watch — タスクボード / メッセージ / コンフリクト監視  │
└─────────────────────────────────────────────┘
```

## zellij との違い

| | zellij 単体 | zeliji |
|---|---|---|
| 画面分割 | ✅ | ✅ (zellij をそのまま利用) |
| セッション間のタスク共有 | ❌ | ✅ 共有タスクボード (claim で取り合い防止) |
| セッション間メッセージ | ❌ | ✅ `zeliji say` / `zeliji inbox` |
| 編集コンフリクト | 💥 同一ツリーで衝突 | ✅ 役割ごとに git worktree で完全隔離 |
| コンフリクトの予兆検知 | ❌ | ✅ ブランチ間の同一ファイル編集を警告 |
| 役割の定義 | 手動で毎回 | ✅ `zeliji.toml` に宣言、システムプロンプトに自動注入 |

## インストール

```bash
pip install -e .   # このリポジトリで。依存は Python 3.11+ 標準ライブラリのみ
```

前提: `git`, `claude` (Claude Code CLI), `zellij`

## 使い方

```bash
cd your-repo
zeliji init          # zeliji.toml の雛形を生成
$EDITOR zeliji.toml  # 役割・担当パス・ブリーフを書く
zeliji up            # worktree 作成 → 役割充填済み zellij が起動
```

`zeliji.toml`:

```toml
[project]
base_branch = "main"

[[role]]
name = "frontend"
prompt = "You own the UI. Keep components small and accessible."
paths = ["web/", "src/ui/"]

[[role]]
name = "backend"
prompt = "You own the API and data layer. Keep endpoints tested."
paths = ["server/"]
```

`zeliji up` がやること:

1. 役割ごとに `.zeliji/worktrees/<role>` へ worktree を作成 (ブランチ `zeliji/<role>`)
2. 役割チャーター (役割・担当ファイル・チーム連携コマンドの説明) を生成し、
   `claude --append-system-prompt` で各セッションに注入
3. zellij レイアウトを生成して起動。下段には `zeliji watch` ダッシュボード

## チーム連携 (各 Claude セッションが Bash で叩く)

```bash
zeliji task add "ログインAPIを実装" --role backend
zeliji task list
zeliji task claim 3        # 二重着手はエラーになる
zeliji task done 3
zeliji say frontend "APIのスキーマ変えたよ、/docs見て"
zeliji say all "mainをrebaseして"
zeliji inbox               # 自分宛て・全体宛ての未読
zeliji status              # 各worktreeの状態 + 同一ファイル編集の警告
```

状態はすべて `.zeliji/bus/` のファイル (flock で排他) にあるので、
人間もペイン外から同じコマンドで参加できる。

## マージ

各役割は自分のブランチ `zeliji/<role>` にコミットする。統合は普通の git:

```bash
git merge zeliji/frontend zeliji/backend   # または PR
```

同一ファイルを複数役割が触った時点で `zeliji status` とダッシュボードが
警告するので、マージ前に気づける。

## ライセンス

MIT
