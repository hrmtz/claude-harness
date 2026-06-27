# Domain perspective preset: medical RAG / PRS-LLM-dev

= 大型 medical RAG (= Mafutsu / PRS-LLM-dev) project 内の design doc review 用 perspective preset。 generic skill body には含めず、 domain-specific adapter として 隔離。

## perspectives

### medical-informatics
- OCEBM evidence level (= study design ベース) と citation centrality (= network position) の axis 分離
- GRADE / Cochrane / DORA / Leiden Manifesto compliance
- scholarly publishing history (= Garfield 1955 → Seglen 1997 → Eigenfactor → DORA → Leiden → OpenAlex 2022)
- 形成 specialty bias (= surgical specialty で RCT 稀、 case series dominant)
- non-EN paper authority (= JA / CN local authority、 EN bias 排除)
- retraction propagation (= Retraction Watch + COPE + reason taxonomy)
- citation rendering UI ethics (= 単独 score 表示禁、 multi-axis disclaimer pair)

### production-retrieval
- 既 H1-H7 retriever (= dense + sparse + recency + evidence + cited + specialty + LoE) との統合
- bench faithfulness baseline (= 51% /pro、 81% Sonnet alone) からの regression
- feature flag (= PAGERANK_ENABLED 等) + H1-H7 only fallback invariant
- bench-first cutover + A/B 1 week
- pagerank_version triple (= deploy_sha + pagerank_version + bench_score) reproducibility
- production blast radius (= /chat /pro user 影響、 p95 latency 4x risk)

### graph-theory + algorithm rigor
- PageRank / HITS / EigenFactor / TrustRank / BadRank specifics
- 80M node × 800M edge scale での memory + walltime
- networkx vs scipy.sparse vs graph-tool tradeoff
- dangling node teleport (= year-weighted vs uniform)
- specialty / language / publication-year cohort normalization
- temporal decay λ specialty 別 (= 形成 30y / 内科 10y / 腫瘍 5y)
- 2 separate graph (= primary clean + integrity) for retraction
- additive penalty vs multiplicative (= retrieval invert 防止)

## perspective brief template (= sub-agent system prompt 用)

```
あなたは <medical RAG / PRS-LLM> の <perspective name> reviewer として、
design doc を厳しく弾く。

context:
- 既 corpus: 7.89M papers + 92 textbook + 70K medical_facts
- 既 bench: faithfulness 51% baseline
- 既 retrieval: H1-H7 scoring (= core/retriever_pg.py)
- production hosts: mars (= canonical PG) / laddie (= primary) / talisker (= 2 号機)

review 観点 (= <perspective> 視点):
1. <observation 1>
2. <observation 2>
...

出力 format:
- ✅ 妥当
- ⚠️ REVISE (= 具体 alternative + why)
- ❌ REJECT (= 根拠 + 代替)
- ❓ UNCLEAR

総合: GO / GO-WITH-REVISE / REJECT。
```

## usage

skill invocation で domain preset 指定時:

```
/dual-magi-review docs/designs/<doc>.md \
  --perspectives medical-informatics,production-retrieval,graph-theory \
  --domain-preset ~/.claude/skills/dual-magi-review/examples/medical_rag_perspectives.md
```

= skill core は generic、 domain bind は flag 経由で 別 file load。

## related domain presets (= 将来 候補)

- `web_app_perspectives.md`: UX / API / SEO / accessibility / performance
- `data_pipeline_perspectives.md`: schema / observability / replay / SLA / data quality
- `ml_training_perspectives.md`: hyperparameter / overfit / evaluation / fairness / deployment
- `legal_document_perspectives.md`: precedent / jurisdiction / enforceability / ambiguity / liability
