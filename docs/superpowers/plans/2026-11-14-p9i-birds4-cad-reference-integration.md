# BIRDS-4 CAD reference integration — P9-I3

## Source verification

- Repository: https://github.com/BIRDSOpenSource/BIRDS4-CAD
- Pinned upstream commit: `3bab47a0f53410ef79005d21321ff659726f0a1c`
- Pinned CAD directory tree: `0b84617fd98db32b5d997dc24585da95d5bd2b55`
- CAD inventory: 46 STEP files, each recorded with upstream Git blob SHA-1 and byte size in `birds4-cad-source-manifest.json`.
- Main FM assembly: `BIRDS4_FM_Final v7.step`, 22,964,753 bytes, upstream blob `0a1eea524ad16c9f06961a62bb550cad4d2d9e36`.
- Repository license: MIT, copyright Kyushu Institute of Technology (2021). Preserve the upstream license notice in redistributed derived assets.
- README identifies BIRDS-4 as a 1U (10×10×10 cm) CubeSat programme; satellite names include Tsuru, Guarani-1 and Maya-2.

## Selected conversion targets

The reproducible conversion job will import and export:
1. `BIRDS4_FM_Final v7.step` — complete FM reference assembly.
2. `B4_RW_Assembly v55.step` — reaction-wheel assembly reference.
3. `B4_OBC v3.step` — OBC board reference.
4. `B4_COM_board v19.step` — communications board reference.
5. `B4_battery v1.step` — battery reference.
6. `B4_FM_+X_panel v1.step` — FM +X panel reference.

All six are pinned by exact source blob SHA-1 and size. CI will compute SHA-256 after download, import STEP in metres, export GLB, and produce an assembly tree / bounding-box / conversion report. The GLB and reports are CI artifacts initially; no large third-party binary is committed to the application source tree in this stage.

## Critical engineering boundary

This is a **BIRDS-4 flight-model CAD reference**, not a claim that the user's Basilisk spacecraft model is BIRDS-4. The reference CAD must remain separately identified until the following are independently reviewed:
- assembly datum and handedness;
- each part's local and parent coordinate frame;
- exact flight-unit applicability (Tsuru / Guarani-1 / Maya-2);
- FM versus EM variant and revision;
- installation transform evidence from the FM assembly procedure and drawings.

The Newsletter Issue 49, page 158, is a datum/orientation reference only. It does not, by itself, establish a complete numeric transform table. BIRDS-3 coordinates must never be applied to BIRDS-4.

## Promotion gates

1. Source bytes match pinned Git blob SHA-1 and declared byte size.
2. CAD kernel imports every selected STEP without error.
3. GLB parses as glTF 2.0, contains non-empty geometry and a non-empty node hierarchy.
4. Assembly tree records original names, parent paths and local transforms; no flattening of the only archival representation.
5. Model envelope is physically plausible against the 1U nominal envelope, while preserving the measured CAD envelope rather than forcibly scaling to 100 mm.
6. Derived GLB is registered with its computed SHA-256 and provenance.
7. No instance is marked `verified` until its source assembly placement has been reviewed against the FM assembly procedure and coordinate drawings.

## References

- CAD: https://github.com/BIRDSOpenSource/BIRDS4-CAD
- FM assembly procedure: https://github.com/BIRDSOpenSource/BIRDS4-ProceduresAndReports/blob/main/Procedures/BIRDS4-FM%20Assembly%20Procedure.docx
- Newsletter Issue 49: https://birds1.birds-project.com/files/BIRDS_Newsletter_Issue_No.49.pdf
- BIRDS-3 reference (separate project): https://github.com/BIRDSOpenSource/BIRDS3-CAD
