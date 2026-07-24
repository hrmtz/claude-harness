---
name: magi
version: 0.1.0-kimi
description: |
  3 視点 pre-flight review の Kimi 契約 mirror。Kimi-native structural
  runner が ship されるまでは明示的な fail-closed availability boundary
  であり、prose だけで reviewer independence を成立済みと扱わない。
type: prompt
whenToUse: |
  高リスクな変更を kick する前。特に walltime ≥ 2h、≥ 100M 行 DML、不可逆な cutover、新しいパイプライン/サービスレイヤー、≥ $10 確実消費、1h 以上の sleep/poll を含むスクリプト、など。
arguments:
  - brief
disableModelInvocation: true
---

# magi — three-perspective preflight review (Kimi 移植版)

Claude harness の `magi` skill を Kimi 用に移植したもの。Claude/Codex の過剰な guardrail で議論しにくい高リスク変更を、Kimi で事前に検討するのに使う。

## 目的

高リスクな変更を始める前に、**技術・運用・商業**の 3 つの直交する視点から計画を審査する。実行者本人が見落としがちな silent failure、回復コスト、代替案、ROI を事前に浮き彫りにする。

## 発動条件

以下のいずれかに該当する変更を kick する前に発動する：

- walltime ≥ 2h
- ≥ 100M 行の DML
- 不可逆 / 6h 以上かかる rollback
- 新しいレイヤー / パイプライン / サービス
- ≥ $10 の確実な消費
- 1h 以上 sleep / poll ループを含む単一スクリプト

以下はスキップ：小さな修正、1 行 edit、ad-hoc クエリ、doc/memory 編集、既に同じプロトコルで審査済みの変更。

## 手順

### 1. 変更 brief を用意する

ユーザーから brief が与えられていない場合は、まずユーザーに聞くか、簡潔な brief を自分で作成する。brief は以下を含める：

- 何をするか（1 段落）
- なぜやるか（driver / deadline / dependency）
- 推定 walltime、コスト、peak（disk / memory / CPU / network）
- 可逆性（rollback 経路と推定コスト）
- 同時に走る可能性のある他タスクとの衝突

brief は 200 行以内で canonical non-symlink file に必ず保存する。canonical
path、path-derived artifact ID、exact byte SHA-256 を中央で計算し、その identity
と exact file への read access を全 reviewer に渡す。chat-only brief は gate
入力として禁止する。

### 2. Mechanical availability boundary

Kimi surface は truthful な `magi-preflight-run/v1` provenance を生成する
provider-specific structural runner をまだ ship していない。`AgentSwarm` output
から `magi-preflight-codex/v1` manifest を手作業で作ることは禁止する。

Kimi-native runner が ship されるまでは fail-closed `ABORT` とし、
`FAMILY_ROUTING` に missing phase `kimi-preflight-runner` を記録する。prose
synthesis への fallback も禁止する。templates / review contract は future
runner の lane/schema 定義であり、この surface の executable gate ではない。

structural runner のある surface でも Magi は一回限りで、`PIVOT` 後に Round 2
を起動しない。shipping / plateau authority は持たない。

## Anti-patterns

- 1 人の agent に 3 視点を全部やらせない。独立性が肝。
- 変更を始めてから Magi を回さない。沈没コストが synthesis を歪める。
- 些細な修正で発動しない。コストに見合わない。
- どの persona も見ていない領域は「未審査」として明示する。
- PIVOT 後に Magi を再実行しない。pre-flight を review loop に変えない。
