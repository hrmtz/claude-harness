# Domain perspective preset: hippocampus (= personal library / corpus / chatbot)

= personal library platform + chatbot RAG project (= hippocampus-mcp) 内の design doc review 用 preset。

## perspectives

### schema-and-migration
- library schema 進化 (= v1 → v4 等の versioned migration history)
- platform enum (= manga / social / subtitle / web / etc) の boundary 判定
- 多 source 統合時の identity 重複 / dedupe strategy
- 各 source の primary key choice (= URL / DOI 不在で hash-based ALC)
- migration reversibility + downgrade path
- schema change の cross-parser impact

### parser-correctness
- 各 source (= dejiko_diary / facebook / sac_subtitle / manga / etc) の HTML / format dialect 差異
- encoding handling (= UTF-8 / Shift-JIS / EUC-JP 混在、 日本 source 特有)
- timestamp normalize (= source timezone / format 多様)
- metadata extraction (= author / date / chapter / scene 等) の precision
- edge case (= empty content / 文字化け / partial dump) handling
- parser idempotency (= 同 source 再 ingest で stable output)

### ingestion-pipeline
- queue + dedupe + retry logic
- rate-limit per source (= robots.txt + ethical scraping)
- incremental vs full re-ingest decision
- failure recovery + resume from checkpoint
- storage layout (= raw / parsed / chunked / embedded)
- cross-platform reference (= 同 user の dejiko diary + facebook post を統合)

### chatbot-integration
- personal corpus retrieval (= user own data に対する RAG)
- context window management (= 長期 history + current query)
- citation rendering (= 「あなたの 2019 年 6 月の日記から」 等 personal source attribution)
- multi-modal handling (= manga panel image + text / subtitle text + audio)
- privacy boundary (= user 自身 only access、 public corpus と分離)

### privacy-and-security
- personal data classification (= public / semi-private / private)
- access control (= 唯一 user / 家族 / friend tier)
- encryption at rest (= 日記 / 個人写真 / etc は sensitive)
- breach risk model (= disk loss / cloud compromise / etc)
- data retention + right-to-delete (= user-driven purge)
- GDPR / 個人情報保護法 compliance for personal data

## perspective brief template

```
あなたは <hippocampus> の <perspective name> reviewer として、
design doc を厳しく弾く。

context:
- hippocampus = personal library + corpus + chatbot RAG platform
- 既 parsers: dejiko_diary / facebook / sac_subtitle / manga / etc
- 既 schema: library_books v4 (= 2026-05-11 migration 004)
- runtime: chatbot_server.py + hippocampus-mcp Memory MCP
- privacy boundary: 唯一 user own data、 personal corpus

review 観点 (= <perspective> 視点):
1. <observation 1>
2. <observation 2>
...

出力 format:
- ✅ 妥当
- ⚠️ REVISE
- ❌ REJECT
- ❓ UNCLEAR

総合: GO / GO-WITH-REVISE / REJECT
```

## usage

```bash
# hippocampus 専用 3 視点 (= default generic perspectives を上書き)
/dual-magi-review ~/projects/hippocampus-mcp/docs/ARCHITECTURE.md

# ingestion pipeline 詳細 — schema + parser + pipeline 視点
/dual-magi-review ~/projects/hippocampus-mcp/docs/INGEST_PIPELINE.md \
  --perspectives schema-and-migration,parser-correctness,ingestion-pipeline \
  --domain-preset ~/.claude/skills/dual-magi-review/examples/hippocampus_perspectives.md

# cross-family も使う (= migration SQL review、 schema + parser + security 視点)
/dual-magi-review ~/projects/hippocampus-mcp/migrations/004_library_books_schema.sql \
  --perspectives schema-and-migration,parser-correctness,privacy-and-security \
  --domain-preset ~/.claude/skills/dual-magi-review/examples/hippocampus_perspectives.md \
  --external codex-mailbox
```

## hippocampus session の isolation setup (= v0.3.0 canonical)

session context 濁り回避のため、 hippocampus 専用 Codex pane + 専用 mailbox channel で **完全分離** が canonical (= shared pane pattern は v0.4.0 で削除候補):

**β. 専用 pane + 専用 channel (= long-running 推奨)**:

```bash
# hippocampus 初回 bootstrap (= 1 度 manual、 再起動まで持続)
tmux new-window -n codex-hippocampus
codex  # = 専用 process
touch ~/.njslyr7/mailbox/hippocampus.jsonl

# 以降 invocation
/dual-magi-review ~/projects/hippocampus-mcp/docs/foo.md \
  --domain-preset ~/.claude/skills/dual-magi-review/examples/hippocampus_perspectives.md \
  --external codex-mailbox \
  --codex-pane 0:<hippocampus-window> \
  --mailbox-path ~/.njslyr7/mailbox/hippocampus.jsonl
```

**γ. formation 経由 都度起動 (= short-lived)**:

```bash
/dual-magi-review ~/projects/hippocampus-mcp/docs/foo.md \
  --domain-preset ~/.claude/skills/dual-magi-review/examples/hippocampus_perspectives.md \
  --external codex-mailbox \
  --spawn-via formation \
  --codex-briefing "Magi reviewer for hippocampus, single doc review"
```

= seq stream / context / Codex prompt 完全分離、 PRS-LLM session ↔ hippocampus session 干渉ゼロ。 「[hippocampus]」 / 「[prs-llm]」 subject prefix で識別する shared pattern より cleaner。

## related (= 想定 hippocampus 内 doc)

invoke 候補 doc:
- `docs/ARCHITECTURE.md` (= 全体構成)
- `docs/INGEST_PIPELINE.md` (= ingestion 詳細)
- `migrations/004_library_books_schema.sql` の design rationale doc
- 新 parser 追加時の design doc
- chatbot prompt design + retrieval strategy doc

## extending

hippocampus が evolve したら preset 拡張:
- 新 source parser 追加 → parser-correctness perspective に 新 source 追記
- 新 modality (= audio / video / 3D / etc) → multi-modal perspective 追加
- 新 user tier (= family / friend etc) → privacy-and-security 拡張
