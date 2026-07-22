# 系譜 — 40年の多重化ツール史から盗むもの

screen (1987) → tmux (2007) → dvtm/abduco → iTerm2 -CC → kitty/WezTerm →
zellij (2021) の深掘り。詳細な出典は末尾。[evolution.md](evolution.md) が
「zeliji が何を壊すか」なら、この文書は「歴史が何を証明済みか」。

## 各世代が壊した前提と死因

| 世代 | 壊した前提 | 教訓 |
|---|---|---|
| splitvt | 1画面=1シェル | 「見た目」だけのツールは必ず包含されて消える |
| screen (1987) | プロセスは端末と運命共同体 | **detach/reattachの発明は40年生き残った**。だが拡張不能なコード+無人メンテで実装は死んだ (6年のリリース空白、2025年SUSE監査) |
| tmux (2007) | ツールの内部は不透明でよい | 全操作=コマンド言語(API)にした結果、プラグイン文明が発生 |
| dvtm+abduco | 多重化はモノリスであるべき | 思想的に正しい分解も、**合成をユーザーに委ねた瞬間ニッチ化**する |
| iTerm2 -CC (2013) | 多重化ツールが描画も担うべき | エンジン(状態所有)とビュー(描画)のプロトコル分離は成功する |
| kitty/WezTerm | 多重化は端末の外の仕事 | UI機能は端末に吸収された。**「セッションが端末より長生き」だけは誰も吸収できていない** |
| zellij (2021) | 多重化ツールは不親切でよい | 発見可能性は本体の仕事 |

## tmuxエコシステム = 欲求の化石目録

本体が満たさなかった欲求は必ず外付けで生え、次世代で本体機能になる:

- tmux-resurrect/continuum → **再起動を超える永続化** → zellij本体のsession resurrection
- tmuxinator/teamocil → **宣言的セッション定義** → zellijのKDL
- byobu → **親切さ** → zellijのキーヒント常時表示
- wemux → **権限差のあるコラボ** (mirror=閲覧 / pair=共有 / rogue=独立) →
  wemuxは2014年に停止。**コラボと権限はラッパーで足せる機能ではなく、
  アーキテクチャの根に要る**という反例として重要

## zeliji への適用 (どれを設計に写像済みか)

1. **永続サーバ+着脱可能ビュー = 40年の不変量。ただし永続化の単位を
   プロセスから状態へ上げる** → zeliji: 状態は最初からファイル
   (バス+worktree+セッションID)。cockpitを閉じても何も死なず、
   `zeliji up` でリアタッチ (実装済み。エージェントは前回の記憶を保持)
2. **エンジンとビューはプロトコル分離、ただし入口は1つ** (iTerm2の成功 ×
   dvtmの失敗) → zeliji: エンジン=バスのプレーンファイル、ビュー=cockpit。
   別ビュー (Web閲覧など) はファイルを読むだけで作れる。ユーザーの入口は
   `zeliji` 一語のまま
3. **本体をAPIに** → zeliji: 全操作がCLI (task/say/status/merge) = 人間にも
   他エージェントにもスクリプトにも同じ面
4. **権限モデル付きコラボは根に組み込む** (wemuxの死の教訓) → zeliji:
   `mode = "mirror" | "pair" | "rogue"` — wemuxの3モードをエージェント権限
   として再演。mirror=読み取り+提案のみ / pair=worktree内編集+協調コマンド
   (既定) / rogue=全自動
5. **見られないものを描画しない** (kittyの「多重化は性能税」批判への回答)
   → zeliji: 端末エミュレーション自体を捨て、状態の購読だけを描画

## zellijがまだ壊していない前提 (= zeliji の主戦場)

調査の結論そのまま: 「操作主体は人間1人」「ペインの中身は不透明なバイト列」
「レイアウト=空間配置 (タスクの依存や成果物は宣言できない)」
「通知はステータスバー1行 (並行作業のトリアージ構造がない)」。
zeliji はこの4つを正面から扱う: ペイン=エージェント、中身=構造化イベント、
zeliji.toml とボード=依存つきの仕事の宣言、`!`/ベル/ボード=トリアージ。

## 主要出典

tmux作者インタビュー (undeadly.org 2009) / softpanorama screen史 /
SUSE screen監査 2025 / tmux Wiki: Control Mode / iTerm2 tmux統合ドキュメント /
tmux-resurrect · tmuxinator · wemux · byobu 各README / abduco・dvtm 公式 /
kitty FAQ (「多重化はアンチパターン」) / wezterm #336 / zellij About・
session resurrection・0.43/0.44 リリースノート / poor.dev
