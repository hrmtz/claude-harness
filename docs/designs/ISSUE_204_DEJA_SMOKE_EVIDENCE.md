# Issue 204: bounded real Magi smoke evidence

Date: 2026-07-27
Target: `docs/designs/ISSUE_204_DEJA_REVIEW_MAGI_WIRING.md`
Target SHA-256: `2c175ee708afa4cdba69fbf5653758173e4feb77898afca95e378c4349a11c1e`
Protocol SHA-256: `8e8e467041a86a3225b7c9144bd861deded479635d03faa593edf0d99082a8f0`

This is a bounded runtime smoke receipt, not a plateau or merge verdict. The
same-family arm ran three real Codex reviewers. The cross-family arm ran
Claude, returned `REVISE` with grounding `PASS`, and published a validator-clean
finding/metadata pair. Independent PR acceptance remains a separate gate.

## Seed

The fresh corpus contained exactly one synthetic, schema-valid Slice 0 source
whose `artifact_sha` equals the target SHA:

- source:
  `/home/hrmtz/sanada_backup_persistent/issue204_deja_smoke_20260727_094700/seed/round_0_seed.json`
- source SHA-256:
  `a42fef097a3cab5fdabf6c1461709eb30943c141532e61048513d9641a532f6f`
- selected occurrence:
  `564e2a4fe2915695cae7cf6d17b6bde753858ed71b61d7d600aadcfaff9cee8f`
- selected count: `1`

## Receipt parity

Machine assertions passed:

- selection status is `injected-candidate`;
- fanout has `injected: true` and `prompt_count: 3`;
- cross-family has `injected: true` and `prompt_count: 1`;
- both arms equal the selection receipt's selection and rendered-block digests;
- the hidden publication transaction no longer exists after recovery/commit;
- all normal fanout, synthesis, cross-family findings, and cross-family metadata
  artifacts exist and are non-empty.

Shared semantic digests:

- selection SHA-256:
  `450182ca86868a9000371442263683d240e47163f944784a8dfc5645a941a93f`
- rendered block SHA-256:
  `9597961e55249c762b115e34a488d0986392583e028084111e9ef1f27bc20653`

Durable receipt files:

| Receipt | Persistent path | File SHA-256 |
|---|---|---|
| selection | `/home/hrmtz/sanada_backup_persistent/issue204_deja_smoke_20260727_094700/final-clean-state/deja-context.receipt.json` | `b6f36ce716cde37d3958f9759670d108d773e09c2a7c4116cc8661f5fdc03b5b` |
| fanout consumption | `/home/hrmtz/sanada_backup_persistent/issue204_deja_smoke_20260727_094700/final-clean-state/deja-consumption-fanout-r1.json` | `347cb21dbbad14db99f825bb53bba63bc45d3298f4988a919a3ea471f109ab4f` |
| cross-family consumption | `/home/hrmtz/sanada_backup_persistent/issue204_deja_smoke_20260727_094700/final-clean-state/deja-consumption-xfamily-r2.json` | `610d74414841bceb94e81ac77e94c9b712fcfd0bf4bbe5ae48b7769349ddb628` |

Normal provider artifact SHA-256 values:

| Artifact | SHA-256 |
|---|---|
| `round_1_melchior.json` | `87539b19064e54d5af3ac3902037dc054648f9420b2f5add4ff708d4e85cd7a0` |
| `round_1_balthasar.json` | `a1310c48e2bd557fc0c7fb3870e7c253054047988e52a10eaa99d67c5306d7ee` |
| `round_1_caspar.json` | `10900f3f6316c8ad5b5cc90187748430968e6d7432d90331e678c49d940dba56` |
| `round_1_magi_synthesis.json` | `8b11aac35133d7c9e36845e4c3b2aecc6a90c31cb811167c437f1882bd1b52b4` |
| `round_2_xfamily.json` | `98e47f8666e905c8063b71c0ffc01ed2a4ff988f692b0a2feacaebafd0acfab4` |
| `round_2_xfamily.meta.json` | `cd477e093645efade5e15fe56126bc89344037633c880566dea57a557b03fdef` |

`magi_verify_xfamily_artifacts.py` independently revalidated the final
cross-family pair after these hashes were recorded.
