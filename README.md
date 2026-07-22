# zeliji

**エージェント時代の多重化ツール。** zellij がターミナルを多重化した
ように、zeliji は **エージェントを多重化**する — zellij 本体はもう不要。

ペインの中身は生のターミナルではなく、役割を持った Claude セッション。
各役割は git worktree で隔離され、共有タスクボードとメッセージングで
協調し、自前の cockpit (curses、依存ゼロ) に構造化イベントが流れる。

```
┌ backend ● RUN ────────────┬ frontend ○ WAIT ! ───────┐
│ » シフト開始…             │ ⚒ Bash zeliji inbox      │
│ ⚒ Bash zeliji task claim 1│ ✔ done: cli.py 実装済み  │
│ ⚒ Write server/greeting.py│   (指示待ち)             │
├───────────────────────────┴──────────────────────────┤
│ Tasks: 1 todo · 1 doing (#2 画面実装@frontend) · 1 done│
│ Tab focus · Enter instruct · g nudge · n task · q quit │
└──────────────────────────────────────────────────────┘
```

なぜ端末エミュレーションを捨てたかは [docs/evolution.md](docs/evolution.md)、
競合との位置づけは [docs/competitors.md](docs/competitors.md)、
設計原則は [docs/design.md](docs/design.md)。

## zellij と比べて

| | zellij + 手動並列 | zeliji |
|---|---|---|
| ペインの単位 | 生のターミナル | **エージェント** (構造化イベントの流れ) |
| セッション間のタスク共有 | ❌ | ✅ 共有ボード (claim で取り合い防止、`--after` で依存) |
| セッション間メッセージ | ❌ | ✅ `say` / `inbox` |
| 編集コンフリクト | 💥 | ✅ 役割ごとに worktree 隔離 + 同一ファイル編集の警告 |
| 入力待ちの検知 | プラグインが必要 | ✅ `!` バッジ内蔵 |
| 成果の回収 | 手動 merge | ✅ `zeliji merge --cleanup` |
| キーヒント常時表示 | ✅ (ここから奪った) | ✅ |
| 端末エミュレーション | あり (重さの根源) | **なし** |

## インストールと前提

```bash
pip install -e .   # Python 3.11+、依存は標準ライブラリのみ
```

前提: `git`, `claude` (Claude Code CLI)。zellij は不要 (レガシーブリッジ
`--zellij` を使う時だけ)。

## 使い方

```bash
cd your-repo
zeliji init          # zeliji.toml の雛形を生成
$EDITOR zeliji.toml  # 役割・担当パス・ブリーフを書く
zeliji up            # worktree準備 → cockpit起動、全役割が自動でシフト開始
```

`zeliji.toml`:

```toml
[project]
base_branch = "main"
include = [".env"]         # gitignore済みでも各worktreeへコピー
setup = "npm install"      # worktree作成時に1回実行
# model = "sonnet"         # 全役割のモデル (役割ごとに上書き可)
# agent_flags = ["--permission-mode", "acceptEdits"]  # 既定値
# kickoff = "..."          # シフト開始プロンプトの上書き

[[role]]
name = "frontend"
prompt = "You own the UI. Keep components small and accessible."
paths = ["web/", "src/ui/"]

[[role]]
name = "backend"
prompt = "You own the API and data layer. Keep endpoints tested."
paths = ["server/"]
```

### cockpit の操作

- `Tab` / `←→` — 役割にフォーカス (入力待ち `!` が消える)
- `Enter` — フォーカス中のエージェントに次の指示 (同一セッション継続)
- `g` — 待機中の全員に「ボードを再確認して続けて」
- `n` — タスク追加 / `s` — メッセージ送信 (`frontend こんにちは` / `all ...`)
- `q` — 終了

エージェントは各自の worktree 内で、役割チャーターをシステムプロンプトに
注入された状態で動く。完全自律で走らせたい場合は
`agent_flags = ["--dangerously-skip-permissions"]` (信頼できるリポジトリのみ)。

### 人間もCLIで同じバスに参加できる

```bash
zeliji task add "ログインAPIを実装" --role backend
zeliji task add "ログイン画面" --after 1   # #1が終わるまでclaim不可
zeliji task list
zeliji say frontend "APIのスキーマ変えたよ"
zeliji status        # 進捗 + 同一ファイル編集の警告
```

状態は全部 `.zeliji/bus/` のプレーンファイル (flock で排他)。エージェントも
人間も、なんなら Codex や Gemini でも、同じ規約で参加できる。

## マージ (収穫)

```bash
zeliji merge                # 全役割のブランチを base へ
zeliji merge --cleanup      # マージ後に worktree とブランチも削除
```

コンフリクトしたら安全に abort して手動解決を案内する。

## レガシーブリッジ

生の対話ターミナルをどうしても触りたいとき:

```bash
zeliji up --zellij   # 従来どおり KDL レイアウトを生成して zellij で起動
```

## ライセンス

MIT
