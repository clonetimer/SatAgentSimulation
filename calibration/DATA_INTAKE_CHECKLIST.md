# Real calibration data intake checklist

- [ ] Immutable raw files received
- [ ] Dataset owner and source reference recorded
- [ ] Export/license/access constraints recorded
- [ ] SHA-256 calculated for every file
- [ ] Measurement variables and units declared
- [ ] Sampling clock and sample count declared
- [ ] Test configuration and environmental conditions declared
- [ ] Measurement uncertainty supplied
- [ ] Calibration target parameters declared
- [ ] Training and independent validation split declared
- [ ] Residual/validation metrics supplied
- [ ] Applicability/validity envelope declared
- [ ] Reviewer and approval recorded
- [ ] `sat-agent calibration validate` returns PASS
- [ ] Dry-run registry registration reviewed before `--commit`
