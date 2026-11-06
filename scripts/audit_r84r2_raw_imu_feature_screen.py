"""Retrospective R84-R2-E0 screen of richer features from frozen R84-R1 raw IMU data.

用途：对已完成 R84-R1 原始官方 IMU 观测进行多特征探索性筛查，不启动新物理仿真。
参数：--artifact 指向 R84-R1 Actions ZIP；--report 指定机器可读 JSON 输出路径。
输出：固定 8 维 IMU 特征、独立健康控制对照和故障分布；不训练模型。
边界：复用 R84-R1 样本，结果仅为 retrospective exploratory evidence，不是独立确认。
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics
from zipfile import BadZipFile, ZipFile

SCHEMA = "sat-sim.r84r2.e0.raw-imu-feature-screen.v1"
ARTIFACT_SHA256 = "4bc1f9d0f788dce5e8d33aace1fe8860d739158793bcf6fae28352c785742a8e"
R83M_SHA256 = "db83c8b50a833d2f9c5074cb3f6f9d44c8e0109a2a7be4ddcec5f70bff754796"
R0_SHA256 = "5d1439272f651519fec011d9728165f3afc46b1b3d14d305357ae271b26752af"
RUNS, EXECUTIONS, SIGMA = 3159, 3160, 0.0001
COHORTS = ("healthy_calibration", "healthy_false_alarm_control", "loss50_primary", "loss75_primary")
AXES = ("imu.raw_gyro_rad_s_x", "imu.raw_gyro_rad_s_y", "imu.raw_gyro_rad_s_z")
FEATURES = (
    "delta_mean_x_rad_s", "delta_mean_y_rad_s", "delta_mean_z_rad_s",
    "delta_std_x_rad_s", "delta_std_y_rad_s", "delta_std_z_rad_s",
    "delta_rms_3d_rad_s", "delta_derivative_rms_3d_rad_s2",
)
SENSOR_KEYS = {"time_s", *AXES}
PRE, POST, DT, SCALE_FLOOR, Q = (0.4, 1.6), (2.6, 3.6), 0.2, 1e-8, 0.90


class ScreenError(ValueError):
    pass


def require(ok: bool, code: str) -> None:
    if not ok:
        raise ScreenError(code)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_jsonl(data: bytes, label: str) -> list[dict]:
    try:
        rows = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    except (UnicodeDecodeError, ValueError) as exc:
        raise ScreenError(f"INVALID_JSONL:{label}") from exc
    require(all(isinstance(row, dict) for row in rows), f"INVALID_JSONL_OBJECTS:{label}")
    return rows


def nearest_rank(values: list[float], probability: float = Q) -> float:
    require(bool(values), "EMPTY_NEAREST_RANK_INPUT")
    ordered = sorted(float(x) for x in values)
    return ordered[max(1, math.ceil(probability * len(ordered))) - 1]


def center_scale(matrix: list[list[float]]) -> tuple[list[float], list[float]]:
    require(bool(matrix) and all(len(row) == len(FEATURES) for row in matrix), "FEATURE_MATRIX_SHAPE")
    center = [statistics.median(row[i] for row in matrix) for i in range(len(FEATURES))]
    scale = []
    for i in range(len(FEATURES)):
        mad = statistics.median(abs(row[i] - center[i]) for row in matrix)
        scale.append(max(1.4826 * mad, SCALE_FLOOR))
    return center, scale


def multi_score(row: list[float], center: list[float], scale: list[float]) -> float:
    z = [(x - center[i]) / scale[i] for i, x in enumerate(row)]
    return math.sqrt(sum(x * x for x in z) / len(z))


def cliffs_delta(a: list[float], b: list[float]) -> float:
    gt = lt = 0
    for x in a:
        for y in b:
            gt += x > y
            lt += x < y
    return (gt - lt) / (len(a) * len(b))


def wasserstein_equal_n(a: list[float], b: list[float]) -> float:
    require(bool(a) and len(a) == len(b), "WASSERSTEIN_SAMPLE_SIZE")
    return sum(abs(x-y) for x, y in zip(sorted(a), sorted(b))) / len(a)


def extract_features(rows: list[dict]) -> dict[str, float]:
    require(len(rows) == 31, "SENSOR_ROW_COUNT")
    for i, row in enumerate(rows):
        require(set(row) == SENSOR_KEYS, f"SENSOR_WHITELIST:{i}")
        require(all(type(row[k]) in (int, float) and math.isfinite(float(row[k])) for k in SENSOR_KEYS),
                f"SENSOR_NONFINITE:{i}")
        require(abs(float(row["time_s"]) - i * DT) <= 1e-12, f"SENSOR_TIME_GRID:{i}")
    pre = [r for r in rows if PRE[0]-1e-12 <= r["time_s"] <= PRE[1]+1e-12]
    post = [r for r in rows if POST[0]-1e-12 <= r["time_s"] <= POST[1]+1e-12]
    require(len(pre) == 7 and len(post) == 6, "FEATURE_WINDOW_SIZE")
    p = [[float(r[k]) for k in AXES] for r in pre]
    q = [[float(r[k]) for k in AXES] for r in post]

    def mean(a): return sum(a)/len(a)
    def std(a):
        m=mean(a)
        return math.sqrt(sum((x-m)**2 for x in a)/len(a))
    def rms3(a):
        return math.sqrt(sum(sum(v*v for v in row) for row in a)/len(a))
    pm = [mean([r[i] for r in p]) for i in range(3)]
    qm = [mean([r[i] for r in q]) for i in range(3)]
    ps = [std([r[i] for r in p]) for i in range(3)]
    qs = [std([r[i] for r in q]) for i in range(3)]
    pd = [[(p[i+1][j]-p[i][j])/DT for j in range(3)] for i in range(6)]
    qd = [[(q[i+1][j]-q[i][j])/DT for j in range(3)] for i in range(5)]
    values = [qm[i]-pm[i] for i in range(3)] + [qs[i]-ps[i] for i in range(3)]
    values += [rms3(q)-rms3(p), rms3(qd)-rms3(pd)]
    require(all(math.isfinite(x) for x in values), "DERIVED_FEATURE_NONFINITE")
    return dict(zip(FEATURES, values))


def context(case: dict) -> tuple[int, int, str]:
    return (int(case["wheel_index_QA_ONLY"]), int(case["initial_state_index"]), str(case["reference_mode"]))


def analyse(records: dict[str, list[dict]], feature_map: dict[str, dict[str, float]]) -> dict:
    matrices = {
        cohort: [[feature_map[x["record_id"]][name] for name in FEATURES] for x in rows]
        for cohort, rows in records.items()
    }
    require(all(len(matrices[c]) == 20 for c in COHORTS), "COHORT_COUNT_NOT_20")
    cal = matrices["healthy_calibration"]
    loo_multi, loo_uni = [], {name: [] for name in FEATURES}
    for i, row in enumerate(cal):
        train = cal[:i] + cal[i+1:]
        c, s = center_scale(train)
        loo_multi.append(multi_score(row, c, s))
        for j, name in enumerate(FEATURES):
            loo_uni[name].append(abs((row[j]-c[j])/s[j]))
    threshold = nearest_rank(loo_multi)
    center, scale = center_scale(cal)
    scores = {
        cohort: [multi_score(row, center, scale) for row in matrices[cohort]]
        for cohort in COHORTS[1:]
    }
    control = scores["healthy_false_alarm_control"]
    multi = {
        "threshold": threshold,
        "healthy_false_alarms": sum(x > threshold for x in control),
        "healthy_false_alarm_rate": sum(x > threshold for x in control)/20,
        "severity": {},
    }
    for cohort, severity in (("loss50_primary","loss50"),("loss75_primary","loss75")):
        vals=scores[cohort]
        multi["severity"][severity]={
            "threshold_exceedances":sum(x>threshold for x in vals),
            "threshold_exceedance_rate":sum(x>threshold for x in vals)/20,
            "score_median":statistics.median(vals),
            "cliffs_delta_vs_healthy_control":cliffs_delta(vals,control),
            "wasserstein_1_vs_healthy_control":wasserstein_equal_n(vals,control),
        }
    uni={}
    for j,name in enumerate(FEATURES):
        t=nearest_rank(loo_uni[name])
        one={}
        for cohort in COHORTS[1:]:
            one[cohort]=[abs((row[j]-center[j])/scale[j]) for row in matrices[cohort]]
        ctrl=one["healthy_false_alarm_control"]
        uni[name]={
            "threshold":t,
            "healthy_false_alarms":sum(x>t for x in ctrl),
            "healthy_false_alarm_rate":sum(x>t for x in ctrl)/20,
            "loss50_exceedance_rate":sum(x>t for x in one["loss50_primary"])/20,
            "loss75_exceedance_rate":sum(x>t for x in one["loss75_primary"])/20,
            "loss50_cliffs_delta":cliffs_delta(one["loss50_primary"],ctrl),
            "loss75_cliffs_delta":cliffs_delta(one["loss75_primary"],ctrl),
        }
    return {"multi_feature":multi,"univariate_features":uni}


def screen_artifact(path: Path) -> dict:
    raw=path.read_bytes()
    require(sha(raw)==ARTIFACT_SHA256,"R84R1_ARTIFACT_SHA256_MISMATCH")
    try:
        with ZipFile(path) as z:
            require(z.testzip() is None,"R84R1_ARTIFACT_CRC")
            names=set(z.namelist())
            required={"r84r1_execution_manifest.json","r84r1_case_manifest.jsonl",
                      "r84r1_sensor_observations.jsonl","r84r1_features.jsonl",
                      "source/r83m-original.zip","source/r84-r0-reference.zip"}
            require(required <= names,"R84R1_ARTIFACT_MEMBER_MISSING")
            manifest=json.loads(z.read("r84r1_execution_manifest.json"))
            require(manifest.get("status")=="PASS","R84R1_SOURCE_NOT_PASS")
            require(manifest.get("unique_native_run_count")==RUNS and
                    manifest.get("native_execution_count_including_replay")==EXECUTIONS,
                    "R84R1_SOURCE_RUN_COUNT")
            require(manifest.get("r83m_original_archive_sha256")==R83M_SHA256,"R83M_HASH_IN_MANIFEST")
            for name,digest in manifest["artifacts"].items():
                require(name in names and sha(z.read(name))==digest,f"R84R1_MEMBER_HASH:{name}")
            require(sha(z.read("source/r83m-original.zip"))==R83M_SHA256,"R83M_EMBEDDED_HASH")
            require(sha(z.read("source/r84-r0-reference.zip"))==R0_SHA256,"R84R0_EMBEDDED_HASH")
            case_rows=read_jsonl(z.read("r84r1_case_manifest.jsonl"),"cases")
            sensor_rows=read_jsonl(z.read("r84r1_sensor_observations.jsonl"),"sensors")
            baseline_features=read_jsonl(z.read("r84r1_features.jsonl"),"features")
    except (BadZipFile,KeyError,OSError) as exc:
        raise ScreenError("R84R1_ARTIFACT_DECODE") from exc
    require(len(case_rows)==EXECUTIONS and len(sensor_rows)==EXECUTIONS and len(baseline_features)==RUNS,
            "R84R1_RECORD_COUNTS")
    cases={x["record_id"]:x for x in case_rows}
    sensors={x["record_id"]:x for x in sensor_rows}
    original={x["record_id"]:x for x in baseline_features}
    require(len(cases)==EXECUTIONS and len(sensors)==EXECUTIONS and len(original)==RUNS,
            "R84R1_DUPLICATE_RECORD_ID")
    require(set(cases)==set(sensors),"R84R1_CASE_SENSOR_ID_MISMATCH")
    unique=[x for x in case_rows if not x.get("repeatability_control")]
    require(len(unique)==RUNS and len({int(x["rng_seed"]) for x in unique})==RUNS,"R84R1_UNIQUE_RUN_SEEDS")
    feature_map={}
    for case in unique:
        rid=case["record_id"]
        f=extract_features(sensors[rid]["rows"])
        require(rid in original and abs(f["delta_rms_3d_rad_s"]-float(original[rid]["delta_rms_3d_rad_s"]))<=1e-12,
                f"R84R1_RMS_RECOMPUTE:{rid}")
        feature_map[rid]=f
    groups=defaultdict(lambda:defaultdict(list))
    for case in unique:
        if case["cohort"] in COHORTS and abs(float(case["gyro_white_noise_std_rad_s"])-SIGMA)<=1e-15:
            groups[context(case)][case["cohort"]].append(case)
    require(len(groups)==27,"R84R1_CONTEXT_COUNT")
    per=[]
    for key,records in sorted(groups.items()):
        require(set(records)==set(COHORTS),f"R84R1_CONTEXT_COHORTS:{key}")
        per.append({"wheel_index_QA_ONLY":key[0],"initial_state_index":key[1],
                    "reference_mode":key[2],**analyse(records,feature_map)})
    n=540
    fpr=sum(x["multi_feature"]["healthy_false_alarms"] for x in per)
    loss50=sum(x["multi_feature"]["severity"]["loss50"]["threshold_exceedances"] for x in per)
    loss75=sum(x["multi_feature"]["severity"]["loss75"]["threshold_exceedances"] for x in per)
    original_counts={"healthy_false_alarms":0,"loss50_exceedances":0,"loss75_exceedances":0}
    for key, records in groups.items():
        calibration=[float(original[item["record_id"]]["delta_rms_3d_rad_s"])
                     for item in records["healthy_calibration"]]
        center0=statistics.median(calibration)
        cal_scores=[abs(value-center0) for value in calibration]
        threshold0=nearest_rank(cal_scores)
        for cohort, target in (
            ("healthy_false_alarm_control","healthy_false_alarms"),
            ("loss50_primary","loss50_exceedances"),
            ("loss75_primary","loss75_exceedances"),
        ):
            values=[float(original[item["record_id"]]["delta_rms_3d_rad_s"])
                    for item in records[cohort]]
            original_counts[target]+=sum(abs(value-center0)>threshold0 for value in values)
    require(original_counts=={
        "healthy_false_alarms":93,"loss50_exceedances":99,"loss75_exceedances":84
    },"R84R1_ORIGINAL_UNIVARIATE_BASELINE_RECOMPUTE_MISMATCH")
    cliffs50=[x["multi_feature"]["severity"]["loss50"]["cliffs_delta_vs_healthy_control"] for x in per]
    cliffs75=[x["multi_feature"]["severity"]["loss75"]["cliffs_delta_vs_healthy_control"] for x in per]
    uni={}
    for name in FEATURES:
        vals=[x["univariate_features"][name] for x in per]
        uni[name]={
            "healthy_false_alarms":sum(round(x["healthy_false_alarm_rate"]*20) for x in vals),
            "loss50_exceedances":sum(round(x["loss50_exceedance_rate"]*20) for x in vals),
            "loss75_exceedances":sum(round(x["loss75_exceedance_rate"]*20) for x in vals),
            "mean_loss50_cliffs_delta":statistics.mean(x["loss50_cliffs_delta"] for x in vals),
            "mean_loss75_cliffs_delta":statistics.mean(x["loss75_cliffs_delta"] for x in vals),
        }
    return {
        "schema_version":SCHEMA,
        "status":"EXPLORATORY_RETROSPECTIVE_ONLY",
        "source_artifact_sha256":ARTIFACT_SHA256,
        "source_original_r83m_sha256":R83M_SHA256,
        "source_r84r0_artifact_sha256":R0_SHA256,
        "source_unique_native_runs":RUNS,
        "source_total_executions_including_replay":EXECUTIONS,
        "source_primary_noise_sigma_rad_s":SIGMA,
        "statistical_unit":"one complete independent-seed Basilisk execution; time samples within a run are not independent",
        "feature_source":"official raw Basilisk IMU gyro channels only",
        "feature_names":list(FEATURES),
        "pre_window_s":list(PRE),"post_window_s":list(POST),
        "robust_score":{
            "center":"per-feature median of 20 healthy calibration runs",
            "scale":"max(1.4826*MAD, 1e-8)",
            "score":"sqrt(mean(((feature-center)/scale)^2)) over 8 fixed features",
            "calibration":"leave-one-out scores within 20 healthy calibration runs",
            "threshold":"nearest-rank 90th percentile of leave-one-out calibration scores",
        },
        "multi_feature_aggregate":{
            "healthy_control_count":n,"healthy_false_alarms":fpr,"healthy_false_alarm_rate":fpr/n,
            "loss50_count":n,"loss50_exceedances":loss50,"loss50_exceedance_rate":loss50/n,
            "loss75_count":n,"loss75_exceedances":loss75,"loss75_exceedance_rate":loss75/n,
            "mean_context_cliffs_delta_loss50":statistics.mean(cliffs50),
            "median_context_cliffs_delta_loss50":statistics.median(cliffs50),
            "mean_context_cliffs_delta_loss75":statistics.mean(cliffs75),
            "median_context_cliffs_delta_loss75":statistics.median(cliffs75),
        },
        "original_r84r1_delta_rms_baseline":{
            **original_counts,
            "healthy_control_count":n,
            "healthy_false_alarm_rate":original_counts["healthy_false_alarms"]/n,
            "loss50_count":n,
            "loss50_exceedance_rate":original_counts["loss50_exceedances"]/n,
            "loss75_count":n,
            "loss75_exceedance_rate":original_counts["loss75_exceedances"]/n,
        },
        "individual_feature_aggregate":uni,
        "per_context":per,
        "interpretation":{
            "independent_confirmation":False,
            "reason":"This screen reuses the R84-R1 artifact; it is retrospective feature exploration, not an independent validation cohort.",
            "multi_feature_separation": "weak; fault score distributions remain close to the independent healthy control distribution",
            "decision":"Do not launch another large IMU-only campaign from this retrospective screen. Assess additional onboard telemetry channels and their measurement fidelity before expanding the fault dataset.",
        },
        "diagnostic_model_trained":False,
        "formal_training_or_test_eligible":False,
        "formal_false_alarm_qualification":False,
        "flight_or_hardware_safety_qualified":False,
        "stable_release_eligible":False,
    }


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact",type=Path,required=True)
    parser.add_argument("--report",type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        result=screen_artifact(args.artifact)
    except (ScreenError,OSError) as exc:
        result={"schema_version":SCHEMA,"status":"FAILED","reason":str(exc),
                "diagnostic_model_trained":False}
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(result,sort_keys=True))
        return 2
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,indent=2,ensure_ascii=False,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"status":result["status"],"multi_feature_aggregate":result["multi_feature_aggregate"],
                      "independent_confirmation":False},ensure_ascii=False,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
