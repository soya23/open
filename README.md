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
設計原則は [docs/design.md](docs/design.md)、改善50案と精査は
[docs/ideas.md](docs/ideas.md)、セキュリティは [SECURITY.md](SECURITY.md)。

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

覚えるコマンドは1つ:

```bash
zeliji
```

設定がなければ対話ウィザードが起動する — gitリポジトリでなくても
その場で作れる(新フォルダ提案つき)。チーム構成はテンプレート
(おまかせ開発 / Web開発 / 文書チーム)から選ぶだけ。答え終わると
cockpitが開き、全役割が自動で仕事を始める。

手で設定したい人は従来どおり `zeliji init` → `zeliji.toml` 編集 → `zeliji up`。

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

### cockpit の操作 (`?` でいつでも全キー一覧)

- `n` — **やってほしいことを書く**。ボードに載り、手が空いた役割が自動で
  拾いに行く。迷ったらこれだけでいい (ボードが空のときは画面が誘導する)
- `Enter` — フォーカス中の役割に直接指示 (同一セッション継続)
- `Tab` / `←→` — 役割の選択 / `↑↓` — その列の履歴をさかのぼる
- `f` — 選択中の列を全画面 / `<` `>` — 列の幅を調整
- `b` — タスクボード全画面 / `v` — 詳細表示 (ツールログ全文)
- `a` — 役割を実行中に追加 (`tester テスト担当` と打つだけ)
- `s` — 連絡 / `g` — 待機中の全員に「続けて」 / `K` — 緊急停止
- `q` — 終了 (作業中の役割がいれば確認が入る)

タイムラインは人間向けに要約される: エージェントの言葉は全文、ツール実行は
薄い1行 (生ログは `v`)。修飾キーは使わない (zellij最大の不満だったキー衝突を
構造的に回避)。入力待ちになると端末ベルで知らせる。

権限は既定で安全 (worktree内の編集と `zeliji`/`git` コマンドのみ)。
完全自律はウィザードで明示的に選ぶか
`agent_flags = ["--dangerously-skip-permissions"]` (信頼できるリポジトリ
のみ。有効中は画面に ⚠AUTO が出る)。詳細は [SECURITY.md](SECURITY.md)。

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
