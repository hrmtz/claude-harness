# Pre-flight reviewer contract

Return one JSON object and no prose. Bind it to the exact brief:

```json
{
  "schema": "magi-preflight-review/v1",
  "reviewer": "MELCHIOR",
  "round": 1,
  "brief": {
    "canonical_path": "/absolute/path/to/brief.md",
    "artifact_id": "<first 16 hex of sha256(canonical_path)>",
    "sha256": "<sha256 of exact brief bytes>"
  },
  "verdict": "PROCEED",
  "findings": []
}
```

Use your assigned reviewer name. `verdict` is `PROCEED`, `PIVOT`, or `ABORT`.
Each finding has:

```json
{
  "finding_id": "M-1",
  "root_cause_id": "stable.lowercase.root",
  "severity": "HIGH",
  "impact": ["technical"],
  "summary": "Concise concern",
  "rationale": "Why it matters",
  "required_change": "Concrete mitigation",
  "recommended_decision": "PIVOT",
  "evidence": [{
    "kind": "brief-lines",
    "start_line": 1,
    "end_line": 2,
    "sha256": "<sha256 of those exact line bytes, including line endings>"
  }],
  "question_if_uncorroborated": "What must be verified before proceeding?"
}
```

Allowed severities: `CRITICAL`, `HIGH`, `MED`, `LOW`. Allowed impacts:
`technical`, `operational`, `commercial`, `security`, `data-loss`,
`irreversibility`. `recommended_decision` is `PIVOT` or `ABORT`.

Use evidence only when the cited exact brief lines support the claim. A
self-asserted grounding flag is invalid. Use the same stable `root_cause_id`
for the same underlying cause; do not coordinate with sibling reviewers.
