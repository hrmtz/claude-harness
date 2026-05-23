# formation (njslyr7) skill v0.2.0 提案

_2026-04-25 起案、Day 17-18 race-pivot + 16 daemon + host death + mailbox relay の実運用知見を fold-in_

`~/.claude/skills/formation/SKILL.md` v0.1.0 (2026-04-21) を v0.2.0 に bump する提案。

> **Status: IMPLEMENTED** (2026-04-25)
> njslyr7 commit `ca8f1cc` で §1〜§6 を fold-in 完了 (`bin/formation`,
> `skills/formation/SKILL.md` v0.2.0, `skills/formation/templates/briefing.md`)。
> §7 副次提案 (`formation-briefing-author` sub-skill) は予定通り後回し。
> 本 doc は履歴目的で残置。次回 bump 時の参照点。

---

## TL;DR

v0.1.0 (現状) は core orchestration protocol (spawn/status/inbox/msg/reap) は記述済だが、**Day 17-18 の Phase 5 race 運用で発見した 6 件の知見が未反映**。

| 知見 | source | 提案 fold-in 先 |
|---|---|---|
| **mailbox auto-inject relay daemon** | `scripts/njslyr7_mailbox_relay.sh` (commit `da7a18f0`) | SKILL.md `## Prerequisites` + `formation spawn` の auto-start |
| **race-pivot pattern** | `formation/qdrant-parallel/race_pivot_strategy.md` | SKILL.md 新 `## Patterns` section |
| **worker mailbox poll habit** | `formation/qdrant-parallel/mailbox_poll_habit.md` | `templates/briefing.md` `## Standing orders` section |
| **R1-R4 host-death 規約** | `feedback_host_death_recovery.md` + `formation/qdrant-parallel/spec_evolution_day18.md` | SKILL.md 新 `## Long-run discipline (R1-R4)` section |
| **instance ID gotcha (new_contract != actual_id)** | `formation/qdrant-parallel/gotcha_new_contract_vs_instance_id.md` | `templates/briefing.md` の vast.ai 用途 example |
| **double Enter で text area stuck** | commit `5db8a7f3` (今日) | SKILL.md `## Troubleshooting` 5 件目 |

副次提案:
- **`formation-briefing-author`** sub-skill 切出し (interactive briefing 作成 ritual、低優先)
- worker memory namespace `formation/<id>/` の実例リンク強化 (qdrant-parallel/ 5 件を「現場リファレンス」として SKILL.md に明示)

---

## 1. Mailbox auto-inject relay daemon — `## Prerequisites` 強化

### 現状

SKILL.md `## Prerequisites` (line 48-53):
```
1. Running inside tmux ([[ -n "$TMUX" ]]).
2. formation is on PATH.
3. jq, flock, sops available.
```

### 課題 (2026-04-25 Day 17 overnight に判明)

mailbox は append-only jsonl で、target agent が **thinking 中だと新着を読まない**。
main-5 ↔ qdrant-parallel の 34+ msg のうち多くで user が tmux send-keys で手動 relay。

user コメント引用 (`feedback_njslyr7_mailbox_relay.md`):
> 「njslyr7 の通信機構の改善が必要。mailbox 透過後に awk が入らないので俺が催促しないと読まれない。」

### 提案 v0.2.0

`scripts/njslyr7_mailbox_relay.sh` (inotifywait-driven、debounce 1s) を **prerequisite + auto-start** に組込:

```markdown
## Prerequisites (v0.2.0 改訂)

1. Running inside tmux (`[[ -n "$TMUX" ]]`).
2. `formation` is on PATH.
3. `jq`, `flock`, `sops`, `inotifywait` (inotify-tools) available.
4. **Mailbox relay daemon active for each worker pane** (`formation spawn` で auto-start)。
   `inotifywait` で `~/.njslyr7/mailbox/log.jsonl` を監視、新 line の `to` field が
   watch 対象 agent なら該当 tmux pane に send-keys inject (debounce 1s)。
   user 手動 poke を不要化、async 双方向通信達成。
   起動 log: `/tmp/relay_<agent>_<pane>.log`
   詳細: `feedback_njslyr7_mailbox_relay.md` + `scripts/njslyr7_mailbox_relay.sh`
```

`formation spawn` 内部で:
```bash
# 既存: claude --session-name formation-<id> 起動 + briefing paste
# 追加: relay daemon kick
nohup bash scripts/njslyr7_mailbox_relay.sh <worker_id> <pane_name> \
  > /tmp/relay_<worker_id>.log 2>&1 &
echo $! > ~/.njslyr7/formation/<worker_id>.relay_pid
```

`formation reap <id>` 内部で:
```bash
# 追加: relay daemon kill
kill $(cat ~/.njslyr7/formation/<id>.relay_pid 2>/dev/null) 2>/dev/null
rm -f ~/.njslyr7/formation/<id>.relay_pid
# 既存: pane close + registry drop
```

### 既知の限界 (SKILL.md に明記)

- agent thinking 中の injection は input buffer に queue、次 prompt wait まで処理待ち (fundamental async、OK)
- pane name は session convention 頼み、将来 tmux socket auto-detect 拡張可
- **scripts/njslyr7_mailbox_relay.sh の項は project-local、global skill にするには ~/.claude/skills/formation/scripts/ に内蔵化必要**

---

## 2. Long-run discipline (R1-R4) — 新 section

### 課題

2026-04-24 20:23 UTC、Norway #35520277 host 完全死で **9h / 68M pts / $4 全損**。
原因: R2 checkpoint push 未実施 + host monitoring threshold 不在。

`feedback_host_death_recovery.md` で R1-R4 規約合意済だが、**SKILL.md には未反映**。

### 提案 v0.2.0

SKILL.md に新 section:

```markdown
## Long-run discipline (R1-R4)

Worker that runs **multi-hour or multi-day** (typical: vast.ai GPU rental,
160M+ chunk processing, multi-shard upsert) must obey 4 protocol rules.
These exist because vast.ai instances die without warning (host hardware
failure, network partition, proxy outage); idle local workers don't have
the same exposure but should still respect R3.

### R1 — Cadenced R2 checkpoint push
Long-run upsert / generate / transform writes intermediate state to R2 at
fixed cadence (e.g., 20M pts/daemon, or 100GB output). Local snapshot
deleted post-push to avoid disk pressure.
Path convention: `r2:mafutsu-<bucket>/checkpoints/<phase>/<worker>_<units>_<ts>.<ext>`

### R2 — Disk pre-flight (output × 1.5)
Before contract: `required_disk_gb = expected_output_bytes / 1e9 * 1.5`.
2026-04-23 #11 教訓 (`feedback_phase5_vastai_lessons_20260423.md`):
disk 150 GB で 88% loss、$55 焼却。default の `--disk 150` 禁止、計算必須。

### R3 — Stall alarm (15 min progress 0 → alert)
Worker spawns `stall_watchdog.sh` alongside main task. 15 min progress 不在で
parent agent に mailbox alert。誤検知許容、false positive cost << silent stall。

### R4 — Host death threshold (30 min unrecoverable → destroy)
ping packet loss 30 min 連続 + `vastai reboot instance` 1 回失敗 = host death 確定。
vast.ai dashboard `cur_state=running` の偽報告事例あり、信用するな。
30 min 以降の wait は sunk cost、即 destroy + re-contract。

### 適用条件
- 16+ daemon long-run、vast.ai $5+ rental、wall time 1h+ → 4 規約全適用
- local idle < 1h → R3 のみ (cron / ScheduleWakeup 経由でも代替可)
```

---

## 3. Patterns — 新 section (race-pivot 等)

### 課題

`formation/qdrant-parallel/race_pivot_strategy.md` に Day 17-18 で発見した「exp が main を超えたら本採用昇格」pattern が記述されてるが、worker side memory のみ。
SKILL.md には generic な orchestration patterns の記述が無く、**毎回設計から起こす無駄**が発生。

### 提案 v0.2.0

```markdown
## Patterns

Reusable workflows discovered through actual multi-worker runs.

### Race-pivot
- **When**: parent has a default approach; sub-worker explores experimental approach
- **Setup**: parent runs `single` baseline; worker runs `exp` variant in isolation
  (separate collection / DB / output dir to avoid contamination)
- **Pivot rule**: if exp metric ≥ X over Y minutes sustained → promote exp to canonical
  - 必ず数値 threshold を briefing に明記 (例: rate ≥ 3000 pts/s sustained 30+min)
  - rate < lower threshold → exp 打ち切り、single で完走
- **Promotion mechanics**: Qdrant snapshot → rename / DB swap / DNS cutover
- **Reference**: Phase 5 Day 17 cutover (mars-1 sustain + mars-2 cutover 並列 spinup)
- **Worker memory**: `formation/<id>/race_pivot_strategy.md` 推奨

### Synthetic-then-real progressive validation
- **When**: target dataset large (45 GB+) and pull cost high
- **Setup**: smoke test on synthetic data first (1-2M points, 10 min)
  - vector content irrelevant if downstream is BLOB-treating (e.g., Qdrant insert speed)
  - representative payload schema を最低限模擬 (10-field payload 等)
- **Promotion**: smoke baseline 確信後に real shard pull
- **Why**: R2 pull 4 MiB/s host throttle 罠 + 45 GB shard 3h 無駄を smoke で先回り検出
- **Reference**: `feedback_r2_not_for_transport.md` + `feedback_phase5_vastai_lessons_20260423.md`

### Touch-not contract
- **When**: parent has live production state that worker must read but not mutate
- **Briefing example**: 「main Claude の CPU instance #X / collection Y に PUT/DELETE 禁止。
  読むだけなら OK」
- **Why**: parallel worker の experimental config (PQ disabled, segment_number=16 等) が
  parent state に紛れ込む事故防止
- **Reference**: `formation/qdrant-parallel/briefing_received.md` の「触らない約束」section
```

---

## 4. Standing orders for workers — `templates/briefing.md` 拡張

### 課題

現 `templates/briefing.md` (1.8KB、Apr 21) は task description 中心。
**Worker discipline (poll habit / report cadence / namespace)** が briefing 毎に user 手書き。
qdrant-parallel worker が「mailbox poll 取りこぼし」「memory namespace 失念」を起こしている。

### 提案 v0.2.0

`templates/briefing.md` に section 追加:

```markdown
## Standing orders (適用 unless overridden by briefing)

### Mailbox discipline
- **Idle 時に必ず `tail -5 ~/.njslyr7/mailbox/log.jsonl` で新着確認**。
  Monitor tick の合間、長 script 実行後、bash 複数叩きの合間、turn end 前。
- 自分が send した msg は skip、他者 from の未 read msg だけ処理。
- **15 min 以上 reply 空かない**。main 側が応答待ちで blocker になる。
- 過去事案: 5 通まとめて後読みで main 長期 blocker (2026-04-24)

### Reporting cadence
- **30 min 毎に 1-line `formation report "<status>"`**: 現在地・rate・errors。
- **Rate 大幅変動 / 異常 / shard 完了**は即時 `formation report`。
- **Decision boundary 超え**は `formation ask`、parent answer 待ち中は idle で待機。

### Memory namespace
- Memory MCP / `~/.claude/projects/.../memory/` 書込時は **`formation/<self_id>/` 配下のみ**。
- 親の root entry を汚染禁止。例: `formation/qdrant-parallel/race_pivot_strategy.md`。
- worker 内で発見した generic learning は parent への `formation done` 報告で
  parent が root entry に格上げするか判断する (worker は格上げしない)。

### Long-run (R1-R4) when applicable
- Multi-hour task は R1-R4 (`SKILL.md ## Long-run discipline`) 全適用。
- R1 cadence は briefing で明示 (例: "20M pts/daemon ごとに R2 push")。

### vast.ai 操作 gotcha
- 契約後、`new_contract` ID と actual `instance_id` が **異なるケースあり**。
  destroy 前に必ず `vastai show instances --raw | grep label=<unique-label>` で actual ID verify。
- 契約単位で label を一意化 (例: `qdrant-parallel-exp-v1/v2/v3`)。
- 過去事案: 2026-04-24 UK $0.39 zombie 出血。
```

---

## 5. Troubleshooting 追加 — `## Troubleshooting`

### 提案追加 entry

```markdown
- **Worker pane で double Enter が text area で stuck**: Claude Code text area の
  仕様で連続 Enter が input flush されず空 prompt 化、worker が無反応に見える。
  fix: commit `5db8a7f3` 適用後の `formation spawn` は `tmux send-keys` の Enter を
  1 回 / 250ms スロットルで送る。古い installation は `formation` re-install
  推奨 (`bash ~/.claude/skills/formation/install.sh`)。

- **Mailbox に msg 着いてるのに worker が読まない**: relay daemon 落ちてる可能性。
  `ps aux | grep njslyr7_mailbox_relay` で確認、無ければ:
  ```bash
  nohup bash scripts/njslyr7_mailbox_relay.sh <worker_id> <pane_name> \
    > /tmp/relay_<worker_id>.log 2>&1 &
  ```
  inotifywait が動いてるかは `tail -f /tmp/relay_<worker_id>.log` で event 確認。
```

---

## 6. Memory namespace 例示強化 — `## Design invariants`

### 現状

SKILL.md line 144-145 (existing):
> **Memory MCP is shared** between lead and workers. Workers should namespace
> their writes under `formation/<worker_id>/` to avoid stomping lead entries.

### 課題

実例リンク無し、worker が「namespace って何書けばいいの」で迷子。
qdrant-parallel/ に **5 件の実用例** が蓄積されてるのに参照されない。

### 提案 v0.2.0

```markdown
## Design invariants (拡張)

### Memory namespace (詳細)

Worker は `~/.claude/projects/.../memory/formation/<self_id>/` 配下のみに書込。
parent の `feedback_*.md` / `project_*.md` を汚染禁止。

**実例 (qdrant-parallel worker、2026-04-24/25)**:
- `briefing_received.md` — 受領時の初期判断記録
- `race_pivot_strategy.md` — parent 指示の戦略変更を保存
- `spec_evolution_day18.md` — instance spec / rate 反復の自分用 log
- `mailbox_poll_habit.md` — 自分用の discipline rule
- `gotcha_new_contract_vs_instance_id.md` — 自分が踏んだ罠の cautionary

worker memory は **session 内のみ有効**で次 worker (別 spawn) には引継がれない。
generic learning として残すべき内容は `formation done` で parent に報告、
parent が root memory (`feedback_*` / `reference_*`) に昇格判定する。
```

---

## 7. 副次提案: `formation-briefing-author` sub-skill (低優先)

### 動機

SKILL.md `### 1. Clarify the briefing` で 4 軸 (mission / scope / boundary / criteria) の聞き出しを
推奨してるが、interactive ritual 化されてない。
worker は spawn 後数時間 / 数 dollar 動くので **briefing 質の ROI は極めて高い**。

### 提案

- skill 名: `formation-briefing-author`
- trigger: `/formation-briefing-author <task summary>`
- 動作: AskUserQuestion で 4 軸を逐次埋める → `formation/briefings/<id>.md` に書出
  - Mission (one sentence)
  - Scope IN / OUT
  - Decision boundary
  - Success criteria checklist
  - (optional) R1-R4 適用要否
  - (optional) race-pivot threshold
- 出力: briefing file + suggest 「`formation spawn formation/briefings/<id>.md <name>` で起動」

### 優先度

**低**。現状 SKILL.md の text guide で十分機能してる。Phase 6 で worker spawn 頻度が
上がる兆候があれば skill 化。

---

## 実装順序提案

1. **即時 (このセッションでも可)**: SKILL.md `## Troubleshooting` に double Enter stuck 1 件追加
   (commit `5db8a7f3` の補足、5 分作業)

2. **near-term (1 週間以内)**: `## Prerequisites` に relay daemon 必須化 + `formation spawn` auto-start
   - `scripts/njslyr7_mailbox_relay.sh` を `~/.claude/skills/formation/scripts/` に内蔵移動
   - global skill 化 (PRS-LLM 以外でも使用可能)

3. **mid-term (Phase 5 安定後 / Phase 6 kick 前)**: 残り 4 件 fold-in
   - `## Long-run discipline (R1-R4)`
   - `## Patterns` (race-pivot / synthetic-then-real / touch-not)
   - `templates/briefing.md` Standing orders section
   - `## Design invariants` memory namespace 例示

4. **後回し**: `formation-briefing-author` sub-skill (Phase 6 worker spawn 頻度次第)

---

## v0.2.0 で達成される効果

- **user 介在ゼロ化**: mailbox relay 自動化で「催促」reply は不要に
- **新 worker の質向上**: standing orders 既定化で briefing 漏れ削減、worker discipline 平均化
- **long-run の出血削減**: R1-R4 codified で host death / disk full / silent stall を構造防御
- **Phase 6 multi-worker scaling 準備**: race-pivot / touch-not pattern が再利用 template 化、
  毎回設計し直す無駄削減

---

## 関連

- 現 skill: `skills/formation/SKILL.md` v0.1.0 (本 doc 起案時)、v0.2.0 (commit `ca8f1cc` で fold-in 後)
- 現 template: `skills/formation/templates/briefing.md`
- 関連 commits (PRS-LLM-dev 側で生まれた前駆 work):
  - PRS-LLM-dev `da7a18f0` (mailbox relay daemon の最初の実装、scripts/njslyr7_mailbox_relay.sh)
  - PRS-LLM-dev `5db8a7f3` (double Enter fix)
  - njslyr7 `4b98fe2` / `bbb0c4e` で njslyr7 リポ側 lib に取り込み済 (上記 PRS 側 commit と等価)
- worker memory namespace の実例: `~/.claude/projects/-home-hrmtz-projects-PRS-LLM-dev/memory/formation/qdrant-parallel/` (5 件、Day 17-18 蓄積)
- root memory (PRS-LLM-dev): `feedback_njslyr7_mailbox_relay.md` / `feedback_host_death_recovery.md` / `user_orchestration_skill_evolution.md`
- 親 audit doc (PRS-LLM-dev): `docs/MEMORY_AUDIT_RESULT_20260425.md` Phase 5 (本 doc 起案と同 session)
- ritual doc (PRS-LLM-dev): `docs/MEMORY_SKILLS_AUDIT_RITUAL.md`

> **Note on doc location**: 本 doc は 2026-04-25 起案時点では PRS-LLM-dev/docs に置かれていたが、
> v0.2.0 実装完了後に njslyr7 リポ側 (`docs/FORMATION_SKILL_V0_2_0_PROPOSAL.md`) に移動した。
> 上記 reference の `~/.claude/projects/...` や PRS-LLM-dev 側 memory / docs への絶対参照はそのまま、
> njslyr7 リポ内ファイルは相対パス。
