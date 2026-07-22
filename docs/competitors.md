# 競合調査 (2026-07-22)

zeliji が戦う市場の地図。Stars は調査日時点の GitHub API 実測値。

## 市場の流れ

2025年前半に「git worktree + tmux で複数の Claude Code を並列実行」パターンが
確立し OSS が乱立。2025年後半〜2026年に GUI 化(Crystal, Conductor,
vibe-kanban)と協調の高度化(claude-flow, claude-swarm)が進み、**2026年2月に
Anthropic 公式が `--worktree` フラグと Agent Teams(実験的)を投入**して、
単純な worktree 並列化はコモディティ化した。現在の競争軸は
(1) セッション間の協調、(2) レビュー/マージ体験、(3) マルチエージェント対応。

## 一覧

| ツール | UI | worktree | セッション間協調 | Stars | 状態 |
|---|---|---|---|---|---|
| [claude-flow → ruflo](https://github.com/ruvnet/ruflo) | CLI/MCP | △ | ◎ 共有メモリ・swarm | 65.5k | 活発だが機能過多で学習コスト大 |
| [vibe-kanban](https://github.com/BloopAI/vibe-kanban) | Web GUI | ○ | △ カンバン(エージェント間通信なし) | 27.5k | 活発 |
| [claude-squad](https://github.com/smtg-ai/claude-squad) | TUI (tmux) | ○ | × | 8.2k | 活発 |
| [Crystal](https://github.com/stravu/crystal) | GUI (Electron) | ○ | ×(比較のみ) | 3.1k | **非推奨化 → Nimbalyst** |
| [workmux](https://github.com/raine/workmux) | CLI (tmux) | ○ | × | 1.9k | 活発 |
| [claude-swarm → swarm](https://github.com/parruda/swarm) | CLI (YAML) | ○ | ◎ MCP 委譲ツリー | ~1.7k | v2 で汎用 SDK 化 |
| [dmux](https://github.com/standardagents/dmux) | CLI (tmux) | ○ | × | ~1.7k | 活発 |
| [parallel-code](https://github.com/johannesjo/parallel-code) | GUI | ○ | × | 0.9k | 活発 |
| [cmux](https://github.com/craigsc/cmux) | shell | ○ | △ 画面ポーリング委譲 | 0.6k | 活発 |
| [uzi](https://github.com/devflowinc/uzi) | CLI (tmux) | ○ | △ 同一プロンプト競争 | 0.6k | 鈍化 |
| [gwq](https://github.com/d-kuro/gwq) | CLI + fzf | ○ | × | 0.5k | 活発 |
| [Conductor](https://www.conductor.build/) | GUI (Mac) | ○ | × | 非公開 | 活発 (YC S24) |
| 公式 `--worktree` / [Agent Teams](https://code.claude.com/docs/en/agent-teams) | CLI | ○ | ◎ 共有タスク+メールボックス | – | Teams は実験的・Claude 専用 |

## zeliji の空白地帯(差別化)

1. **「隔離」と「協調」の軽量な両立**。worktree 系 CLI はほぼ全て協調ゼロ、
   協調系は重厚。両方を持つのは公式 Agent Teams のみ。
2. **ファイルベース = 人間可読・git 管理可能**。Agent Teams のタスクリストは
   内部状態。zeliji のボードは `cat` で見え、人間が直接編集でき、監査できる。
3. **エージェント非依存**。Agent Teams は Claude 専用。「ファイルを読んで
   タスクを取り、メッセージを書く」規約なら Codex / Gemini も混成参加できる。
4. **zellij は未開拓**。競合はほぼ全て tmux。KDL レイアウト自動生成を
   やるツールは調査で見つからず、明確なニッチ。
5. **依存ゼロ Python**。Go+tmux+gh、Rust+Webサーバ、Electron、Ruby、npm
   スタックに対する導入コストの優位。

## 競合から取り入れたもの

| 機能 | 手本 | zeliji での形 |
|---|---|---|
| 進捗つきダッシュボード | claude-squad / gwq / vibe-kanban | `zeliji status` / watch にコミット数・変更ファイル数 |
| ワンコマンド収穫 | uzi `checkpoint` / dmux | `zeliji merge` (+ `--cleanup`) |
| gitignore ファイルの持ち込み | 公式 `.worktreeinclude` / Conductor | `zeliji.toml` の `include` / `setup` |
| タスク依存関係 | 公式 Agent Teams / vibe-kanban | `task add --after <id>`、依存未完了なら claim 不可 |

## あえてやらないこと

- **競争モード**(uzi / Crystal の同一タスクN並列比較) — 需要はあるが別軸。
  ボード+worktree の上に将来安く足せるので今はやらない。
- **swarm 的メタ機能**(claude-flow) — 機能過多は claude-flow への懐疑論が
  反面教師。小さく確実に。
- **GUI / Webサーバ / デーモン** — zellij が画面、ファイルが状態。常駐なし。

## zellij 本体から学んだこと

- **discoverability**: zellij の最大の評価点は「画面下に今使えるキーを常時表示」
  = 覚えるのではなく見れば分かる。zeliji もコマンド出力に次の一手を出す。
- **宣言的レイアウト**: KDL 1ファイルで環境を再現・共有する思想は
  `zeliji.toml` の思想そのもの。
- **強すぎるデフォルトの失敗**: zellij 最大の不満はキーバインド衝突
  (公式が非衝突プリセットで是正)。強い既定は逃げ道とセットで。
- **重さへの不満**(RAM/38MiB バイナリ)は「依存ゼロ・常駐なし」の追い風。
- **AIエージェント監視プラグイン**(zellij-attention, zj-radar, zellaude)が
  一大カテゴリ化 = 「複数エージェントの見張り」は実需。watch ペインはここに刺さる。

主要出典は各リンク先。調査の詳細版はコミット履歴の調査ログを参照。
