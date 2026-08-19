# Calibration data landing area

This delivery contains no real calibration measurements. This tree is a controlled landing area for future datasets.

Rules:

1. Put immutable source files under `<component>/raw/`.
2. Record SHA-256, units, uncertainty, validity and provenance in a dataset manifest.
3. Generated/cleaned data goes under `<component>/processed/` and must retain links to the raw files.
4. Validate with `sat-agent calibration validate <manifest>`.
5. Register only qualified evidence with `sat-agent calibration register <manifest> --commit`.
6. Synthetic/demo/literature/datasheet data cannot be relabeled as ground calibration or flight correlation.
