# Incident: epifan wrapper self-round-trip passed but subaruEdit rejected the file

Date: 2026-08-16
Status: contained; fixed-XOR write path disproved; no flash attempted

## Summary

`nakamura-fdr` generated a SubaruEdit-format candidate by XOR-wrapping a patched
plaintext ROM. Local checks passed, but SubaruEdit rejected the resulting `.hex`
with `file cant be opened details:`.

The local checks proved only that the chosen XOR bytes were self-consistent. A
subsequent no-change decrypt/re-encrypt produced a file byte-identical to the
source and SubaruEdit opened it. A one-byte plaintext change at `0x100038`, with
the same keystream and header, was rejected. The keystream/header path is
therefore working; an additional content-integrity condition remains unresolved.

## Affected artifacts

- Plain candidate: `build/racerom_telemetry_core_600cc.rom`
  - SHA-256: `4573f61f2800377b692149a03f1631de1e33262b6869737bff6eac6e155c5b46`
- Rewrapped candidate: `build/racerom_telemetry_core_600cc.epifan.hex`
  - SHA-256: `dee020fb3da4941f2d1cb7bed66f230135ec61d6359c50f8b1174c0f93745162`
- Plain base extracted from a SubaruEdit process dump:
  `data/raw/subaruEdit/ROMs/decrypted_exact/ZN6_FA22_2026-08-09_600cc.dec.bin`
  - SHA-256: `d454542e630d0f152640ad803c20024e64bebd4e545274b8d33e2402459895dd`

The wrapper had the expected `0x140300` size, retained the selected 768-byte
header, decrypted to the local candidate under the locally derived XOR bytes,
contained CALID `ZA1JA02A`, and passed all 17 Denso checksum checks. None of those
checks established SubaruEdit acceptance.

## Root-cause status

The first hypothesis was an unproven plaintext/encrypted-partner binding. The
no-change round-trip opening in SubaruEdit refuted that as the cause of this
failure: the exact source bytes survived the local transform and transfer.

The checksum-repaired one-byte test was also rejected. A later native-save
experiment closed the remaining ambiguity:

1. SubaruEdit opened the current 600 cc file.
2. The operator changed the rev limit from 8200 to 8500 and saved it natively.
3. The native saved file was opened in SubaruEdit and its exact plaintext was
   recovered from a process dump.
4. The recovered plaintext had CALID `ZA1JA02A`, contained 8500.0 at `0x10CC6C`,
   and passed all 17 Denso checksum records.
5. Reusing the exact native plaintext/ciphertext XOR bytes to change 8500 back to
   8200, followed by Denso checksum repair and preservation of the 768-byte header,
   produced a file that SubaruEdit rejected.

The native save changed 13,913 encrypted-body bytes for a two-byte calibration
change plus checksum changes, concentrated in three distant regions. This is
incompatible with a reusable position-fixed XOR stream. The current native-save
format applies a content-dependent transform and/or integrity layer. Treating the
remaining failure as an ordinary Denso checksum issue is disproved.

Exact evidence:

- Native SubaruEdit save: SHA-256
  `2693c97f94eb35c45ce9d991bfe74dffa3c81f605022cd1d1451e7e72f1a76a1`
- Process dump: SHA-256
  `dc06812b2d8f44828157f0e96942e83bfb3eb60435fb0dab993bd1710842ce0e`
- Recovered native plaintext: SHA-256
  `92449d0713d36ff6a512699b407a94c86e0a4ecc08aef1cae729895f87b1b5e6`
- Rejected exact-key 8500-to-8200 test: SHA-256
  `65f384e3b03b3ccc3e610997cee3835066f5ff2e4c2775bc68c9824526f9eb33`

## Impact and containment

- SubaruEdit rejected the file before flashing.
- No ECU write was attempted from this artifact.
- The rejected wrapper must not be used for flashing.
- The plaintext RaceROM candidate remains useful for offline byte/checksum
  analysis, but it is not a SubaruEdit-loadable artifact.

## Corrective actions

1. Stop labeling fixed-XOR output as SubaruEdit-native or flashable. The no-op
   round-trip remains a transform self-test only.
2. If the native format remains a target, capture multiple native saves with
   controlled plaintext deltas and analyze the content-dependent regions.
3. Pursue an independent owner-operated J2534 flash path as a separate project;
   do not infer EcuFlash FA20 write support from BRZ read metadata.
4. Require an actual SubaruEdit open result before labeling any generated file
   `subaruEdit`-compatible. Local XOR round-trip is a transform self-test only.
5. Keep the exact source/plaintext/ciphertext/header binding in a receipt even
   though it was not the cause of this incident.
