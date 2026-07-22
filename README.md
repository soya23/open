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
| セッション間のタスク共有 | ❌ | ✅ 共有タスクボード (claim で取り合い防止、`--after` で依存関係) |
| セッション間メッセージ | ❌ | ✅ `zeliji say` / `zeliji inbox` |
| 編集コンフリクト | 💥 同一ツリーで衝突 | ✅ 役割ごとに git worktree で完全隔離 |
| コンフリクトの予兆検知 | ❌ | ✅ ブランチ間の同一ファイル編集を警告 |
| 役割の定義 | 手動で毎回 | ✅ `zeliji.toml` に宣言、システムプロンプトに自動注入 |
| 成果の回収 | ❌ 手動 merge | ✅ `zeliji merge --cleanup` で収穫から掃除まで1コマンド |

類似ツール (claude-squad, vibe-kanban, 公式 Agent Teams 等) との比較は
[docs/competitors.md](docs/competitors.md)、設計原則は
[docs/design.md](docs/design.md) を参照。要点: **worktree 隔離と
セッション間協調を両方持つ軽量 CLI は空白地帯**で、ボードがプレーン
ファイルなので人間もどのエージェント (Claude/Codex/Gemini) も同じ規約で
参加できる。

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
include = [".env"]        # gitignore済みでも各worktreeへコピー
setup = "npm install"     # worktree作成時に1回実行

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
zeliji task add "ログイン画面" --after 1   # #1が終わるまでclaim不可
zeliji task list
zeliji task claim 3        # 二重着手・依存未完了はエラーになる
zeliji task done 3
zeliji say frontend "APIのスキーマ変えたよ、/docs見て"
zeliji say all "mainをrebaseして"
zeliji inbox               # 自分宛て・全体宛ての未読
zeliji status              # 進捗(コミット数/変更ファイル数) + 同一ファイル編集の警告
```

状態はすべて `.zeliji/bus/` のファイル (flock で排他) にあるので、
人間もペイン外から同じコマンドで参加できる。

## マージ (収穫)

各役割は自分のブランチ `zeliji/<role>` にコミットする。回収は1コマンド:

```bash
zeliji merge                    # 全役割のブランチをbaseへ (コミットのある役割のみ)
zeliji merge frontend           # 特定の役割だけ
zeliji merge --cleanup          # マージ後にworktreeとブランチも削除
```

同一ファイルを複数役割が触った時点で `zeliji status` とダッシュボードが
警告するので、マージ前に気づける。コンフリクトしたら安全に abort して
手動解決を案内する。普通の `git merge zeliji/<role>` も当然使える。

## ライセンス

MIT
