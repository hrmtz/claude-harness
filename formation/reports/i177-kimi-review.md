# i177-kimi-review — PR #179 (`fix/dispatcher-chassis-177`) adapter 経路全量トレース

Reviewer: i177-kimi-review (kimi chassis) / parent: storm-raven (%178)
対象: `origin/fix/dispatcher-chassis-177` (aab05f2 + 5dee535) vs `origin/dev`
方法: read-only 全量トレース。branch blob を `git show` で直接読み、live 設定 (~/.claude, ~/.codex,
~/.grok, ~/.kimi-code, ~/.local/bin) と突合。repo 非変更 (本 report のみ)。

## 結論 (done 判定)

**修正後の正規配線では、4 chassis いずれについても「自分と違う chassis の adapter に到達する経路」は
ゼロ。BLOCKER なし。** ただし HIGH 1 件 (drift check が stamp 非対応で false-positive 化) と
MEDIUM 1 件 (grok compat.claude 経路は構造外) を検出。後述。

## 修正の構造 (確認した前提)

- `plugins/harness-core/bin/harness-hook:56-59` — `PLUGIN_ROOT` を pass-through 化
  (`[ -n "${PLUGIN_ROOT:-}" ] && export PLUGIN_ROOT`)。fabricate しない。`CLAUDE_PLUGIN_ROOT` と
  新規 `HARNESS_PLUGIN_ROOT` は従来通り export。
- `plugins/harness-core/hooks/tmux_self_name.sh:22-26` — codex adapter への分岐条件を
  `HARNESS_CHASSIS=codex` または `(HARNESS_CHASSIS 未設定 かつ PLUGIN_ROOT 設定)` に限定。
  それ以外は `tmux_self_name.sh:30-32` で `tmux_self_name_core.sh --chassis claude` へ fallthrough。
- `plugins/harness-core/hooks/codex_hippocampus_session_start.sh:12-16` — `HARNESS_CHASSIS` 明示を
  優先、未設定時のみ PLUGIN_ROOT fallback、他 chassis 値では即 no-op。
- installer 3 本が stamp: `install-codex-hooks.sh:247` (`HARNESS_CHASSIS=codex`、managed block の
  全 command に無条件適用 = overlay hooks + external 両方)、`install-grok-hooks.sh:125`
  (`HARNESS_CHASSIS=grok`、overlay + external)、`install-kimi-hooks.sh:132` (`HARNESS_CHASSIS=kimi`)。

## 経路表 (chassis × 起点 × 中継 × 到達 adapter × 判定)

### claude

| # | 起点 | 中継 | 到達 adapter | 判定 |
|---|------|------|--------------|------|
| A1 | `~/.claude/settings.json` SessionStart (sync_hooks_to_live.py が hooks.json:165 から生成) | `~/.local/bin/harness-hook` (repo symlink) → `tmux_self_name.sh` | `tmux_self_name_core.sh --chassis claude` (HARNESS_CHASSIS/PLUGIN_ROOT ともに不在) | ✓ 正到達 |
| A2 | 同上 SessionStart (hooks.json:180) | harness-hook → `codex_hippocampus_session_start.sh` | なし (case `""` + PLUGIN_ROOT 不在 → exit 0) | ✓ 意図的 no-op |
| A3 | Claude native plugin load (marketplace: `.claude-plugin/marketplace.json` → `plugins/harness-core`) | host が `CLAUDE_PLUGIN_ROOT` のみ設定 → hooks.json:165 | 同 A1 (PLUGIN_ROOT は Claude host は立てない) | ✓ 正到達 |

### codex

| # | 起点 | 中継 | 到達 adapter | 判定 |
|---|------|------|--------------|------|
| B1 | `~/.codex/config.toml` managed block, external SessionStart (cross_cli_hooks.json codex.external) | 直 path、`HARNESS_CHASSIS=codex bash .../codex_tmux_self_name.sh` | `codex_tmux_self_name.sh` (chassis は `--chassis codex` 直書き) | ✓ 正到達 |
| B2 | 同上 external | `HARNESS_CHASSIS=codex bash $HIPPOCAMPUS_HOME/scripts/hooks/codex_session_start.sh` | hippocampus companion (PLUGIN_ROOT/HARNESS_CHASSIS 非読取を確認) | ✓ |
| B3 | 同上 managed block overlay hooks (26 本) | `HARNESS_CHASSIS=codex` + hooks.json 由来 command (harness-hook 経由) | 各種 guard。codex の hook set に `tmux_self_name.sh` / `codex_hippocampus_session_start.sh` は**含まれない** (cross_cli_hooks.json codex.hooks を精査) | ✓ identity adapter 非到達 |
| B4 | native codex plugin (local marketplace: `plugins/harness-core/.codex-plugin/plugin.json` + `hooks/hooks.json:165`) | codex host が PLUGIN_ROOT を native に設定 → tmux_self_name.sh | HARNESS_CHASSIS 未設定 + PLUGIN_ROOT 設定 → fallback 分岐で `codex_tmux_self_name.sh` | ✓ 正到達 (legacy fallback が正当に機能) |
| B5 | 同上 (hooks.json:180) | codex_hippocampus_session_start.sh | 同条件で proceed (codex host で動くのは設計通り) | ✓ |
| B6 | magi fanout (`plugins/harness-magi-codex/scripts/magi_fanout_codex.sh:522`) | `harness-cross-cli --isolate-tmux` → `codex exec` | 子の全 adapter は kill switch 6 種 =1 で REFUSE(disabled) | ✓ 到達しても無力化 |

### kimi

| # | 起点 | 中継 | 到達 adapter | 判定 |
|---|------|------|--------------|------|
| C1 | `~/.local/bin/kimi` (= `kimi-wrapper.sh` 実体コピー、install-kimi-wrapper.sh が `identity_owner.sh` も隣に配置、live 確認済) | `kimi-wrapper.sh:104-116` → identity core 直接 | `harness_identity_claim --chassis kimi` (直書き、env signal 不依存) | ✓ 正到達 |
| C2 | `~/.kimi-code/config.toml` marker block (install-kimi-hooks.sh:132) | `HARNESS_CHASSIS=kimi bash/python3 <repo 直 path>` (harness-hook 非経由) | overlay kimi.hooks 15 本 = guard 類のみ。**SessionStart hook は kimi set に存在しない** (identity は C1 が担う) | ✓ identity adapter 非到達 |
| C3 | (kimi からの cross-cli 子起動) | harness-cross-cli 既定 | B6 と同じく kill switch 無力化 | ✓ |

### grok

| # | 起点 | 中継 | 到達 adapter | 判定 |
|---|------|------|--------------|------|
| D1 | `~/.grok/hooks/harness.json` external SessionStart (install-grok-hooks.sh:125) | `HARNESS_CHASSIS=grok bash .../grok_tmux_self_name.sh` | `grok_tmux_self_name.sh` (`--chassis grok` 直書き) | ✓ 正到達 |
| D2 | 同上 overlay hooks (10 本) | `HARNESS_CHASSIS=grok` 直 path | guard 類のみ、identity adapter 非含有 | ✓ |
| D3 | `~/.claude/settings.json` を grok が `[compat.claude]` 経由で読む経路 | claude 配線 (stamp なし) → harness-hook → tmux_self_name.sh | **claude core adapter が grok pane を claim** | ✗ 誤到達 (MEDIUM-1、条件付き。下記) |

### PLUGIN_ROOT を chassis signal として読む箇所の全数 (scope 3)

repo 全 sweep (`git grep PLUGIN_ROOT`、docs/tests 除く本体系 + tests も目視):

1. `plugins/harness-core/hooks/tmux_self_name.sh:23-24` — routing 条件 (既知)
2. `plugins/harness-core/hooks/codex_hippocampus_session_start.sh:14` — fallback gate (既知)

**3 件目の signal reader なし。** 非 signal の言及: `magi_autorun_hook.sh:3` (shell-local 自己代入、
export せず chassis 判定に不使用)、`harness-hook:56` (pass-through)、installer 3 本 (comment/stamp)、
`sync_hooks_to_live.py:47` / `check_cross_cli_hooks.sh:84` (`${CLAUDE_PLUGIN_ROOT}` 文字列置換、別変数)、
各 hooks.json (interpolation)。新規 export の `HARNESS_PLUGIN_ROOT` (harness-hook:59) の reader は
repo 内ゼロ → INFO-1。

### kill switch 貫通 (scope 4)

6 種 (`HARNESS_TMUX_SELF_NAME_DISABLE` / `{CLAUDE,CODEX,KIMI,GROK}_TMUX_NAME_DISABLE` /
`HIPPOCAMPUS_TMUX_NAME_DISABLE`) の reader は `identity_owner.sh:46-53` `harness_identity_disabled`
のみで、PR はこの関数に非接触。writer である `harness-cross-cli` の set/unset 列も diff 上無変更。
stamp は env 追加のみで switch の解釈に影響しない。kimi wrapper は core 呼出し無条件 (switch 判定は
core 内部)。**効き方に変化なし。** 一点、cross-cli は `HARNESS_CHASSIS`/`PLUGIN_ROOT` を strip
しない (INFO-2) が、既定 `disable_self_name=1` が子の identity claim を全無力化するため実害なし。

## Findings

### HIGH-1: `scripts/check_cross_cli_hooks.sh` が stamp 非対応 — installer 再実行後に drift check が全滅する

`git diff origin/dev...origin/fix/dispatcher-chassis-177 --stat -- scripts/` は空 = drift check は未更新。
codex section (want = overlay 由来の無 stamp command vs got = live config の stamped command)、
grok section (同)、kimi section (`# 4.`, 同) の 3 箇所すべてで、新 installer で再 install した環境は
全 command が MISSING + EXTRA に見え、`--live` が必ず失敗する。

失敗シナリオ: merge → install 3 本再実行 → release runbook / CI が
`check_cross_cli_hooks.sh --live` を回すと "codex managed block is missing overlay hooks" 等で赤。
release を block するか、最悪「この check はいつも壊れてる」と素通り運用が定着し、真の wiring drift を
検知する gate 自体が死ぬ (#177 の教訓 = 「green に見える gate は信用するな」と同型)。
対処: 3 section の want 側に `HARNESS_CHASSIS=<chassis>` prefix を付与するか、比較前に got から
prefix を strip する正規化を入れる。

### MEDIUM-1: grok の `[compat.claude] hooks = true` 環境では claude 配線が grok pane を claim する (D3)

stamp は codex/grok/kimi installer が生成する config 内 command にしか付かない。grok が claude の
`~/.claude/settings.json` を compat 読みする経路には stamp が載りようがなく (claude 配線は signal 不在
が正常)、HARNESS_CHASSIS 未設定 + PLUGIN_ROOT 未設定で claude core adapter が走る。

失敗シナリオ: compat.claude を切っていない grok 環境で SessionStart 二重発火 → claude adapter が先に
free pane を CLAIM (`@harness_chassis=claude`、window `claude-*`) → 後から grok 本来の
`grok_tmux_self_name.sh` が owner-live + foreign-owner-nested で REFUSE → pane は grok なのに
恒久的に `claude-*` 表示、formation routing も claude と誤認。#177 と同クラスの誤表示。
緩和: install-grok-hooks.sh の post-install 指示と docs/grok_hooks.md が `hooks = false` を明示、
本機の `~/.grok/config.toml` も false 設定済みを確認。構造的には直っていないので MEDIUM に留める。
(完全に閉じるなら claude 配線側にも `HARNESS_CHASSIS=claude` stamp を sync_hooks_to_live.py で
付け、core 側で chassis 不一致を no-op にする設計が必要 — scope 外として storm-raven 判断に委ねる。)

### INFO-1: `HARNESS_PLUGIN_ROOT` は reader ゼロの dead export

`harness-hook:59` で export されるが repo 内に読む箇所がない。害はないが、将来「もう一つの
PLUGIN_ROOT」として同型の誤読を招く余地がある。reader ができるまで export を止めるか、
意図を comment に残すかのどちらかを推奨。

### INFO-2: 手動 env 汚染は依然として誤 routing する (設計上の残存リスク)

`HARNESS_CHASSIS=codex` または `PLUGIN_ROOT` を shell rc 等で export した環境の claude session は、
tmux_self_name.sh:24 の分岐を通り codex adapter に行く。stamp は command スコープなので通常配線では
発生しえず、発生にはユーザーの明示的 env 操作が必要。harness-cross-cli はこの 2 変数を strip しないが、
既定で kill switch =1 が子の claim を無力化する。対応不要、認知事項として記録。

### INFO-3: deploy 注意 — live 配線はまだ pre-fix

live `~/.codex/config.toml` / `~/.kimi-code/config.toml` / `~/.grok/hooks/harness.json` に stamp なし
(installer 未再実行)、`~/.local/bin/harness-hook` symlink 先の working tree は dev = pre-fix コードで
#177 は live のまま。codex/kimi/grok は adapter 直書き or wrapper 直到達なので live での誤到達は
claude のみ。merge 後の実効化には sync_hooks_to_live.py + installer 3 本再実行 + codex re-trust が必要。

### INFO-4: claude 側で hippocampus companion 注入が止まる (意図的な挙動変更)

bug 動作中、claude session でも codex_hippocampus_session_start.sh が fabricate PLUGIN_ROOT で
動いていた = hippocampus context が注入されていた。修正後は no-op になり、claude は本来の
ghost_inject.sh / recent_topics_inject.sh のみになる。設計通りだが、注入量の変化に気づく人がいるかも
しれない。

## テストカバレッジ評価 (同梱の test_hook_dispatch_chassis.sh)

実 tmux pane で chain 全体を叩く統合 test で、claude chain 正到達 / dispatcher の非 fabrication /
明示 codex / 明示 claude が stray PLUGIN_ROOT に勝つ / legacy plugin host fallback /
hippocampus no-op ×2 / installer stamp ×3 を網羅。fix の急所は押さえられている。
カバー外: grok/kimi の直 path adapter (D1/C1)、compat.claude 経路 (MEDIUM-1)、drift check
(HIGH-1 がすり抜けた理由)。CI workflow への組込み (5dee535) も確認。
