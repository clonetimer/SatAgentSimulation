#!/usr/bin/env python3
"""用途：离线对照已核验 NASA ISS OEM 星历，量测原生轨道传播残差。
参数：--source-dir 含 source.txt/provenance.json；--epoch、--duration-s、--output-dir。
输出：原始 TaskSpec、原生遥测与逐时刻位置/速度残差；只量测，不宣称卫星标定通过。
"""
from __future__ import annotations

import argparse
import csv
from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from sat_sim.reference_orbits import parse_earth_oem, parse_utc  # noqa: E402
from sat_sim.form_schema import capability_form_schema  # noqa: E402
from sat_sim.unified_agent import normalize_form_task_spec  # noqa: E402
from sat_sim.task_models import canonicalize_task_spec  # noqa: E402
from sat_sim.task_compiler import compile_task_spec  # noqa: E402
from sat_sim.unified_execution import execute_compiled_task  # noqa: E402


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--epoch', default='2026-10-26T00:00:00Z')
    parser.add_argument('--duration-s', type=float, default=3600.)
    parser.add_argument('--step-s', type=float, default=.2)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error('Choose a fresh output directory to preserve earlier evidence')
    if not (math.isfinite(args.duration_s) and args.duration_s > 0):
        parser.error('duration must be positive and finite')
    source = args.source_dir/'source.txt'
    provenance = json.loads((args.source_dir/'provenance.json').read_text(encoding='utf-8'))
    if digest(source) != provenance['sha256']:
        raise ValueError('REFERENCE_SOURCE_HASH_MISMATCH')
    reference = parse_earth_oem(source.read_text(encoding='utf-8-sig'))
    epoch = parse_utc(args.epoch)
    stop = epoch + timedelta(seconds=args.duration_s)
    usable_start = parse_utc(reference.metadata.get('USEABLE_START_TIME', reference.metadata['START_TIME']))
    usable_stop = parse_utc(reference.metadata.get('USEABLE_STOP_TIME', reference.metadata['STOP_TIME']))
    if not usable_start <= epoch < stop <= usable_stop:
        raise ValueError('REFERENCE_WINDOW_OUTSIDE_USABLE_RANGE')
    selected = [state for state in reference.states if epoch <= state.epoch <= stop]
    if len(selected) < 3 or selected[0].epoch != epoch or selected[-1].epoch != stop:
        raise ValueError('REFERENCE_WINDOW_MUST_USE_EXACT_OEM_EPOCHS')
    cadence = (selected[1].epoch - epoch).total_seconds()
    if any((right.epoch-left.epoch).total_seconds() != cadence for left,right in zip(selected, selected[1:])):
        raise ValueError('REFERENCE_WINDOW_REQUIRES_UNIFORM_CADENCE')

    import numpy as np
    from Basilisk.utilities import orbitalMotion, simIncludeGravBody
    mu = simIncludeGravBody.gravBodyFactory().createEarth().mu
    initial = selected[0]
    elements = orbitalMotion.rv2elem(mu, np.array(initial.position_m), np.array(initial.velocity_m_s))
    values = {
        'epoch_utc':epoch.isoformat().replace('+00:00','Z'),
        'orbit_radius_m':float(elements.a), 'eccentricity':float(elements.e),
        'inclination_deg':math.degrees(elements.i),
        'raan_deg':math.degrees(elements.Omega) % 360.,
        'arg_perigee_deg':math.degrees(elements.omega) % 360.,
        'initial_true_anomaly_rad':float(elements.f) % (2*math.pi),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        'schema_version':'sat-external-oem-orbit-comparison.v1', 'status':'RUNNING',
        'source_provenance':provenance, 'reference_metadata':reference.metadata,
        'epoch_utc':values['epoch_utc'], 'duration_s':args.duration_s, 'reference_cadence_s':cadence,
        'initialization':'Only first reference state; no later-state fitting or parameter tuning.',
        'frame_comparison':'EME2000 components compared to native J2000; frame bias is not removed and remains in residuals.',
        'spacecraft_representation':'Generic ADCS spacecraft; gravity-only orbit comparison, not an ISS hardware/configuration model.',
        'unmodeled_reference_effects':['atmospheric drag', 'higher Earth gravity harmonics', 'planned maneuvers not independently excluded', 'EME2000/J2000 frame bias'],
        'source_sha256':{str(path.relative_to(ROOT)):digest(path) for path in
            (Path(__file__), ROOT/'src/sat_sim/reference_orbits.py', ROOT/'src/sat_sim/bsk_engine/unified_native.py', ROOT/'src/sat_sim/bsk_engine/orbital_environment.py')},
        'accuracy_acceptance':'NOT_DEFINED_BY_USER; measurements only, no accuracy certification.',
        'can_claim_flight_validity':False, 'profiles':{},
    }
    def save():
        (args.output_dir/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save()
    for profile in ('simplified','spice_wmm'):
        directory = args.output_dir/profile
        directory.mkdir()
        spec = canonicalize_task_spec(normalize_form_task_spec(
            capability_form_schema('subsystem.adcs_unified_native.v1')['default_form']))
        spec['parameters']['values'].update(values, environment_profile=profile)
        spec['simulation'].update(duration_s=args.duration_s, step_s=args.step_s, sample_s=cadence)
        (directory/'task_spec.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
        result = execute_compiled_task(compile_task_spec(spec), task_spec=spec,
            output_root=directory/'dataset', write_dataset=True)
        if (result.summary.get('runtime_truth_status') != 'instantiated_connected_recorded_unified'
                or result.summary.get('orbital_environment', {}).get('profile') != profile):
            raise ValueError('REFERENCE_COMPARISON_REQUIRES_ACTUAL_SELECTED_NATIVE_RUNTIME')
        actual = {int(float(row['time_s'])*1e9+.5):row for row in result.trace_rows}
        residuals = []
        for state in selected:
            elapsed = (state.epoch - epoch).total_seconds()
            row = actual[int(elapsed*1e9+.5)]
            r = np.array(state.position_m)
            v = np.array(state.velocity_m_s)
            dr = np.array([row[f'orbit.position_m_{axis}'] for axis in 'xyz'])-r
            dv = np.array([row[f'orbit.velocity_m_s_{axis}'] for axis in 'xyz'])-v
            radial = r/np.linalg.norm(r)
            normal = np.cross(r,v)
            normal /= np.linalg.norm(normal)
            along = np.cross(normal,radial)
            residuals.append({'elapsed_s':elapsed, 'position_error_m':float(np.linalg.norm(dr)),
                'velocity_error_m_s':float(np.linalg.norm(dv)),
                'radial_error_m':float(dr@radial), 'along_track_error_m':float(dr@along),
                'cross_track_error_m':float(dr@normal)})
        with (directory/'residuals.csv').open('w',newline='',encoding='utf-8') as stream:
            writer = csv.DictWriter(stream,fieldnames=list(residuals[0]))
            writer.writeheader()
            writer.writerows(residuals)
        if not all(math.isfinite(value) for row in residuals for value in row.values()):
            raise ValueError('REFERENCE_COMPARISON_NONFINITE')
        if residuals[0]['position_error_m'] > 1e-5 or residuals[0]['velocity_error_m_s'] > 1e-7:
            raise ValueError('REFERENCE_INITIAL_STATE_ROUNDTRIP_MISMATCH')
        heldout = residuals[1:]
        report['profiles'][profile] = {
            'status':'MEASURED', 'reference_states_compared':len(residuals),
            'initial_position_error_m':residuals[0]['position_error_m'],
            'initial_velocity_error_m_s':residuals[0]['velocity_error_m_s'],
            'heldout_position_rms_m':math.sqrt(sum(row['position_error_m']**2 for row in heldout)/len(heldout)),
            'heldout_position_max_m':max(row['position_error_m'] for row in heldout),
            'heldout_velocity_rms_m_s':math.sqrt(sum(row['velocity_error_m_s']**2 for row in heldout)/len(heldout)),
            'final_residual':residuals[-1], 'summary':result.summary,
            'task_spec_sha256':digest(directory/'task_spec.json'),
            'residuals_sha256':digest(directory/'residuals.csv'),
        }
        save()
        print(json.dumps({'profile':profile, 'position_rms_m':report['profiles'][profile]['heldout_position_rms_m']}),flush=True)
    report['status'] = 'MEASURED_NOT_QUALIFIED'
    save()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
