# One-shot artifact contract

Return one JSON object with schema `magi-preflight-review/v1`, reviewer
`MELCHIOR`, `BALTHASAR`, or `CASPAR`, and `round: 1`. Bind `brief` to its
canonical path, path-derived 16-hex artifact ID, and exact byte SHA-256. Use
verdict `PROCEED`, `PIVOT`, or `ABORT`.

Each finding must include a unique `finding_id`, stable lowercase
`root_cause_id`, severity (`CRITICAL`, `HIGH`, `MED`, `LOW`), impacts
(`technical`, `operational`, `commercial`, `security`, `data-loss`,
`irreversibility`), summary, rationale, required change, recommended decision
(`PIVOT` or `ABORT`), a question for the uncorroborated case, and zero or more
exact brief-line evidence entries.

An evidence entry is `{"kind":"brief-lines","start_line":N,"end_line":M,
"sha256":"..."}` where the digest covers those exact bytes including line
endings. Never claim grounding without exact evidence.
