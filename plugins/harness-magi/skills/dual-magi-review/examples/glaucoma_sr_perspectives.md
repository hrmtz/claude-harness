# Domain perspective preset: glaucoma-SR academic paper review

NTG / Flammer syndrome ナラティブ系統的レビューの投稿前査読用 perspective preset。
Journal of Glaucoma 投稿を想定。

## perspectives

### sr-clinical
臨床的妥当性 + NTG / 緑内障専門知識視点での批評。

観点:
- 各エビデンスの臨床解釈が正確か (IOP, OPP, RNFL, VF 等の数値解釈)
- Flammer syndrome の定義・診断基準の一貫性 (冷え性・低血圧・爪郭毛細血管等の操作化)
- 引用された各研究の患者背景 (n, 人種, IOP 管理状況) が結論と整合するか
- 治療推奨 (CCB, GBE, Mg, brimonidine) の evidence level 評価が OCEBM 準拠か
- "FS 型 NTG" という疾患亜型の operationalization に循環論法がないか
- 因果推論: PVD → NTG 進行の association と causation の区別が適切か
- 東アジア bias: 証拠が主に日本・韓国コホートであることによる外的妥当性への言及
- 治療アルゴリズム (Step 1-4) が実臨床で実行可能か (24h ABPM, CPT, RVP測定の普及度)
- 安全性懸念: CCB の低血圧 FS 患者への適用リスクへの記述が十分か

出力 format (= 各項目):
- ✅ 妥当
- ⚠️ REVISE (= 具体的代替案 + why)
- ❌ REJECT (= 根拠 + 代替)
- ❓ UNCLEAR

総合: GO / GO-WITH-REVISE / REJECT + 主要 2-3 件の CRITICAL finding

---

### sr-methodology
系統的レビュー方法論 + PRISMA 2020 準拠視点での批評。

観点:
- PRISMA 2020 checklist 充足率 (特に single-author bias, funnel plot 省略の正当化)
- 検索戦略の再現可能性: 10 クエリ + Mafutsu (非 PubMed primary) の正当化が十分か
- 単著者選定バイアス: dual-reviewer なしの single-author SR として許容可能な記述か
- "ナラティブ SR" のラベルが選択的 citation の免責として機能していないか
- evidence level 分類 (Level 1-4) の適用の一貫性: 各引用の分類が表と本文で一致するか
- 採用 59 件の内訳 (Level 別) が実際の引用と対応するか
- 除外論文 (n≈21) の除外理由の透明性
- 利益相反の記述: 単著者 NTG 患者として治療推奨を書くことへの潜在的 conflict
- 検索データベースの limitation: Mafutsu (proprietary, 非公開 corpus) 使用の reproducibility
- GRADE equivalent quality assessment の欠如を limitation で十分に説明しているか

出力 format: (sr-clinical と同じ)

---

### jog-submission
Journal of Glaucoma 投稿適合性 + peer reviewer 視点での批評。

観点:
- **Novelty**: "FS 型 NTG" という framing は既存 Flammer/Orgül/Konieczka らの総説と十分に差別化できているか
- **Word count**: JOG narrative review の上限 (通常 4000-6000 words main text) に対する現状 word count
- **Structure**: Introduction → Methods → Results → Discussion → Conclusion の各分量バランス
- **Abstract**: structured 250 words 以内になっているか (現状の abstract を評価)
- **Figure/Table**: Table 3.3 と Table 3.5 が JOG 投稿規定 (最大 6 table/figure) 内に収まるか; PRISMA flow diagram の提示形式
- **References**: 59 件が JOG の上限 (review で通常 80-100 可) 内か; DOI 全件付与されているか
- **Competing interest**: NTG 患者 (H.M.) が推奨治療を評価する COI の開示が十分か; "no competing interest" が reviewers に受け入れられるか
- **Likely reviewer objections**: (1) single-author SR (2) proprietary DB primary search (3) no meta-analysis (4) FS 定義の非標準化 — これらへの先手対応が十分か
- **Contribution statement**: sole author の statement が JOG author contributions 規定と合うか
- **English quality post-humanizing**: JOG editor の標準 (American English, formal) に達しているか

出力 format: (sr-clinical と同じ)

---

## Codex 側 briefing template

```
You are a peer reviewer for a narrative systematic review manuscript on normal-tension 
glaucoma (NTG) and Flammer syndrome, targeting the Journal of Glaucoma.

Your role: act as Magi reviewer with perspective "<PERSPECTIVE>".
Document: <artifact_path>
Round: <round>

Review the document strictly from your perspective. Output:
- ✅ VALID for each point that is sound
- ⚠️ REVISE: [specific problem] → [specific alternative with rationale]
- ❌ REJECT: [specific problem] → [specific fix required]
- ❓ UNCLEAR: [specific question to clarify]

At the end: overall verdict GO / GO-WITH-REVISE / REJECT, 
plus top 3 CRITICAL findings (= items that would cause desk rejection or major revision).

Be specific. Quote exact text from the document when citing problems.
Do not hallucinate citations or data. If uncertain, mark ❓.
```

## usage

```bash
/dual-magi-review ~/projects/glaucoma-SR/output/combined_en.md \
  --perspectives sr-clinical,sr-methodology,jog-submission \
  --domain-preset ~/.claude/skills/dual-magi-review/examples/glaucoma_sr_perspectives.md \
  --external codex-mailbox \
  --codex-pane 0:8 \
  --mailbox-path ~/.njslyr7/mailbox/glaucoma-sr.jsonl \
  --apply-local
```
