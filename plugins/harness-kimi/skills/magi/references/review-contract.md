# One-shot artifact contract

Return one JSON object with schema `magi-preflight-review/v1`, the assigned
reviewer name, and `round: 1`. Bind the `brief` object to the canonical brief
path, its path-derived 16-hex artifact ID, and exact byte SHA-256. Use verdict
`PROCEED`, `PIVOT`, or `ABORT`.

Each finding includes unique `finding_id`, stable lowercase `root_cause_id`,
severity (`CRITICAL`, `HIGH`, `MED`, `LOW`), impact list (`technical`,
`operational`, `commercial`, `security`, `data-loss`, `irreversibility`),
`summary`, `rationale`, `required_change`, `recommended_decision` (`PIVOT` or
`ABORT`), `question_if_uncorroborated`, and an `evidence` array. Evidence
entries require `kind: brief-lines`, 1-based integer `start_line` and
`end_line`, and `sha256` of those exact bytes including line endings. No other
fields are accepted. Never self-assert grounding.
