# R84-R1 raw-IMU feature exploration for R84-R2 design

> Status: **EXPLORATORY ONLY**  
> Source: immutable R84-R1 artifact #13498019393, SHA-256 \`4bc1f9d0f788dce5e8d33aace1fe8860d739158793bcf6fae28352c785742a8e\`.  
> This report is not a confirmatory test and does not change the frozen R84-R1 result.

## 1. Question

R84-R1's preregistered scalar \`delta_gyro_rms_3d\` showed weak separation under independent seeds. Before defining R84-R2, a read-only exploratory pass computed additional candidate summaries from the already archived raw IMU gyro channels. The same 27 physical contexts and independent calibration/control/fault cohort separation were retained.

No simulator truth, RW state, fault label, target wheel, actuator torque or QA field entered any feature value. Wheel and severity labels were used only to stratify the offline analysis.

## 2. Candidate feature families

For each original R84-R1 pre/post window, the exploration calculated:

- per-axis signed mean change;
- per-axis RMS change;
- per-axis sample-standard-deviation change;
- per-axis first-difference RMS change;
- vector mean-norm change;
- original vector RMS change.

Each scalar candidate was independently centered on the 20-run healthy calibration median and thresholded using its 90th nearest-rank absolute calibration deviation within each physical context. This was exploratory reuse of R1 data, not a new independent evaluation.

## 3. Pooled primary-noise results

| Candidate | Healthy control exceedance | loss50 exceedance | loss75 exceedance |
|---|---:|---:|---:|
| Per-axis RMS change, Y | 65/540 (12.04%) | 76/540 (14.07%) | 70/540 (12.96%) |
| Per-axis standard-deviation change, Y | 73/540 (13.52%) | 93/540 (17.22%) | 86/540 (15.93%) |
| Per-axis first-difference RMS change, X | 77/540 (14.26%) | 83/540 (15.37%) | 70/540 (12.96%) |
| Original vector RMS change | 93/540 (17.22%) | 99/540 (18.33%) | 84/540 (15.56%) |

The Y-axis RMS candidate has the lowest observed healthy-control exceedance rate among these univariate candidates, but its fault exceedance rates are only slightly higher. Selecting Y after inspecting these results would be post-hoc axis selection and is explicitly not carried forward as a confirmatory claim.

A 14-feature maximum robust-z score using per-axis mean/RMS/std/difference-RMS plus vector RMS and mean-norm candidates yielded:

| Composite | Healthy control exceedance | loss50 exceedance | loss75 exceedance |
|---|---:|---:|---:|
| 14-feature max robust-z | 99/540 (18.33%) | 107/540 (19.81%) | 95/540 (17.59%) |

The exploratory max-statistic did not improve the operating separation.

A target-wheel-axis-only post-hoc projection also did not provide a reliable separation: its four candidate metrics produced healthy-control exceedance rates between 16.48% and 20.00%, with fault exceedance rates remaining in a similar range. This projection is not promoted to an inference feature.

## 4. Interpretation

The R84-R1 limitation is not simply that the feature family was too small. Under one short excitation pulse and only six post-event samples, raw gyro noise dominates many candidate summaries. Axis selection and adding features to a maximum score do not automatically improve observability.

Therefore R84-R2 does **not** freeze a post-hoc “best axis” or tune a threshold using R1 fault outcomes. Instead, it tests a different physical information regime:

1. extend the native run from 6 s to 18 s;
2. retain the original R83m waveform over the first 6 s for exact physical comparison;
3. add three further alternating native 0.9° guidance pulses after 6 s;
4. aggregate each pulse's pre/post raw-gyro response into a fixed 16-feature vector;
5. use a max robust-z score calibrated only on 30 independent healthy calibration runs per context, then evaluate a separate healthy-control cohort and two fault severities.

The repeated-pulse design is a prospective test of whether more physical excitation and repeated observations improve signal accumulation. It is not assumed to succeed. A negative result remains valid.

## 5. Boundaries

- No training or classifier accuracy.
- No inference-time simulator-truth subtraction.
- No use of the R1 post-hoc Y-axis finding to select an R2 axis.
- No transfer of R1 thresholds.
- No formal false-alarm qualification, flight/hardware qualification or Stable claim.
