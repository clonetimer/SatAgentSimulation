# BIRDS-4 CAD source pin

This folder stores provenance and source inventory only. The upstream STEP files
are downloaded by the reproducible CI conversion job; they are not duplicated
here.

- `birds4-cad-source-manifest.json`: 46 STEP files pinned by upstream Git blob
  SHA-1 and exact byte size, plus the source repository commit and tree.
- `NOTICE.md`: MIT attribution and redistribution notice.
- The full-FM assembly is `BIRDS4_FM_Final v7.step` (22,964,753 bytes).
- The conversion job generates GLB, per-node assembly transforms, and reports
  as a GitHub Actions artifact.

FM and EM filenames are kept distinct. A CAD file being present in the repository
does not prove it was installed on every BIRDS-4 flight unit. In particular,
do not infer missing FM face panels from similarly named EM/B3 files.

The generated CAD is a BIRDS-4 reference model. It is not the user's current
Basilisk spacecraft configuration and must not inherit P9-H telemetry until
coordinate-frame and model-identity alignment has been independently verified.
