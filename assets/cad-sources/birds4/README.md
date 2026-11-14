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

## Flight-unit and functional references

The separate `BIRDSOpenSource/BIRDS4-AntennaPanel` repository is pinned in the manifest. It contains distinct -Y antenna-panel Eagle board files for GuaraniSat, MAYA-2 and TSURU. Its README says those board variants differ mainly aesthetically; that statement applies only to those Eagle board files, not the entire spacecraft assembly. The mount-top and mount-bottom PDFs are also pinned by source blob and byte size.

A published BIRDS-4 antenna paper identifies the -X outer panel as a PCB that participates in the Loop Hentenna structure. Therefore the -X panel must not be automatically categorized as a generic solar panel. Hardware-function classification must follow subsystem/electrical documentation, not the visible panel geometry or filename alone.

These references are useful for variant and functional classification. They do not establish the numeric CAD-to-spacecraft datum transform or prove which flight-unit-specific -Y board is installed in the generic `BIRDS4_FM_Final v7.step` assembly.
