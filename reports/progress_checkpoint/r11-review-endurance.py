"""Read-only technical review of one completed configured v9 run; never auto-approve release.

Short/live runs cannot pass. Manual human assessment and no-resume boundaries
from the original audit remain intact. No simulation, process control or repair.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import runpy

from sat_sim.interactive.continuous_endurance import EVIDENCE_FILE,SUMMARY_FILE
from sat_sim.interactive.continuous_endurance_audit import audit_endurance_workspace
from sat_sim.run_bundle import verify_run_bundle

ROOT=Path(__file__).resolve().parents[2]
REPORTS=ROOT/'reports/local_requirements_completion'
CORE='59d0f3613c8f21e267d8c2fd7886bb3af6a4be71a32f521d3e8b4646133a9cbe'
APPLICATION='4cce6d46062a27bc310cc168c1d9587a458c56b4692bd60cf2c60548c2698f82'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def review(run_dir):
    run_dir=run_dir.resolve()
    progress_path=run_dir/'progress.json'
    progress=read(progress_path)
    report={'status':'NOT_QUALIFIED','observed_at_utc':datetime.now(timezone.utc).isoformat(),
        'run_dir':str(run_dir),'checks':{},'reason_codes':[],
        'manual_review_required':True,'automatic_release_approval':False,
        'native_resume_supported':False,'automatic_tc_replay_allowed':False,
        'scope':'Technical evidence for one frozen Windows generic LEO engineering run. Not hardware/flight approval, cross-process resume or aggregate duration certification.'}
    if not progress.get('finished_at_utc'):
        report['status']='INCOMPLETE_NO_FINAL_VERDICT'
        report['reason_codes']=['ORIGINAL_RUN_NOT_TERMINAL']
        return report
    workspace=run_dir/'api/interactive_sessions/native-endurance'
    watched=[progress_path,run_dir/'source_provenance.before.json',run_dir/'source_provenance.after.json',
        run_dir/'application_source.before.json',run_dir/'application_source.after.json',
        workspace/'session_spec.json',workspace/'state_events.jsonl',workspace/'command_ids.sqlite',
        workspace/'telemetry_manifest.json',workspace/SUMMARY_FILE,workspace/'runtime_result.json',
        workspace/'validation_outcome.json',run_dir/'initial_configuration.input.json',
        run_dir/'initial_configuration.json']
    before={str(path):sha(path) for path in watched if path.is_file()}
    def check(name,passed,reason):
        report['checks'][name]=bool(passed)
        if not passed:report['reason_codes'].append(reason)
    check('driver_requested_at_least24h',progress.get('requested_wall_seconds',0)>=86400.,'INSUFFICIENT_REQUESTED_DURATION')
    check('driver_observed_at_least24h',progress.get('wall_observed_by_runner_s',0)>=86400.,'INSUFFICIENT_WALL_DURATION')
    check('original_driver_review_eligible',progress.get('status')=='REVIEW_ELIGIBLE','ORIGINAL_DRIVER_NOT_REVIEW_ELIGIBLE')
    check('same_run_runtime_source_bound',progress.get('runtime_source_unchanged') is True
        and progress.get('runtime_source_sha256_before')==progress.get('runtime_source_sha256_after')==CORE,'RUNTIME_SOURCE_NOT_BOUND')
    check('same_run_complete_application_bound',progress.get('application_source_unchanged') is True
        and progress.get('application_source_sha256_before')==progress.get('application_source_sha256_after')==APPLICATION,'APPLICATION_SOURCE_NOT_BOUND')
    check('same_runner_bound',progress.get('runner_source_unchanged') is True
        and progress.get('runner_sha256')==progress.get('runner_sha256_after')==sha(ROOT/'scripts/run_native_endurance.py'),'RUNNER_SOURCE_NOT_BOUND')
    module=runpy.run_path(str(ROOT/'scripts/run_native_endurance.py'))
    fingerprint=module['runtime_source_fingerprint']
    check('current_runtime_and_application_match',fingerprint()==CORE
        and fingerprint(module['application_source_manifest']())==APPLICATION,'CURRENT_SOURCE_CHANGED')
    for phase in ['before','after']:
        source_path=run_dir/('source_provenance.'+phase+'.json')
        app_path=run_dir/('application_source.'+phase+'.json')
        check('runtime_manifest_'+phase,fingerprint(read(source_path))==CORE
            and sha(source_path)==progress['source_provenance_'+phase+'_sha256'],'RUNTIME_MANIFEST_NOT_BOUND')
        check('application_manifest_'+phase,fingerprint(read(app_path))==APPLICATION
            and sha(app_path)==progress['application_source_provenance_'+phase+'_sha256'],'APPLICATION_MANIFEST_NOT_BOUND')
    session=progress.get('session',{})
    spec=read(workspace/'session_spec.json')['session_spec']
    configuration_path=run_dir/'initial_configuration.input.json'
    normalized_path=run_dir/'initial_configuration.json'
    expected_configuration={'orbit':{'environment_profile':'spice_wmm','epoch_utc':'2026-01-01T00:00:00Z'},
                            'thermal':{'solar_panel_structure_conductance_w_k':4.}}
    check('explicit_reference_input_snapshot_bound',configuration_path.is_file() and normalized_path.is_file()
        and sha(configuration_path)==progress.get('initial_configuration_input_sha256')=='12c50af31f2f8a5764d81a6aceaecc7aa8fab0ddb94087a38a725d5e0e57c7f4'
        and sha(normalized_path)==progress.get('initial_configuration_normalized_sha256')=='625e3717bd257ff59855858dab1f5f0a8cb7d952851a11bbe362222ff95919c4'
        and read(configuration_path)==read(normalized_path)==expected_configuration
        and progress.get('initial_configuration_unchanged') is True,'REFERENCE_INITIAL_CONFIGURATION_NOT_BOUND')
    applied=read(workspace/'session_spec.json')
    check('actual_reference_taskspec_bound',applied.get('task_spec_sha256')==progress.get('applied_task_spec_sha256')
        =='1f100d763a44e16dcb899d7759e9dbf8749548c01e7b24d3837d6d3041825d02'
        and spec['task_spec']['parameters']['values']=={'environment_profile':'spice_wmm',
            'epoch_utc':'2026-01-01T00:00:00Z','solar_panel_structure_conductance_w_k':4.},'ACTUAL_TASKSPEC_NOT_CONFIGURED_REFERENCE')
    tolerance=spec.get('drift_tolerance_s')
    peak=session.get('max_abs_drift_s')
    report['reported_pacing']={key:session.get(key) for key in
        ['rate','paced','pace_observation_count','last_drift_s','max_abs_drift_s','timing_status']}
    report['reported_pacing']['declared_drift_tolerance_s']=tolerance
    report['reported_pacing']['reported_peak_within_declared_tolerance']=(
        isinstance(peak,(int,float)) and isinstance(tolerance,(int,float)) and peak<=tolerance)
    if not report['reported_pacing']['reported_peak_within_declared_tolerance']:
        report.setdefault('review_warnings',[]).append(
            'Historical pacing peak exceeds the declared drift tolerance. Uptime and mission evidence must not be presented as an all-step strict real-time guarantee; preserve the peak and review its workload context.')
    check('session_completed_and_sealed',session.get('state')=='COMPLETED'
        and session.get('archive_status')=='SEALED' and session.get('terminal_error') is None,'SESSION_NOT_COMPLETED_AND_SEALED')
    validation=read(workspace/'validation_outcome.json')
    result=read(workspace/'runtime_result.json')
    summary=result['result']['summary']
    check('actual_validation_and_mission_pass',validation.get('result')=='PASS'
        and summary.get('execution_status')=='PASS' and summary.get('mission_status')=='PASS','MISSION_OR_VALIDATION_FAILED')
    links=[p for p in summary.get('thermal_conduction_paths',[])
           if {p['from_node'],p['to_node']}=={'solar_panel','structure'}]
    check('actual_native_single4w_link',len(links)==1 and links[0]['conductance_w_per_k']==4.,'ACTUAL_NATIVE_LINK_NOT_REFERENCE4W')
    check('actual_native_reference_environment',summary['thermal_environment']['earth_ir_exitance_w_m2']==234.
        and summary['thermal_environment']['earth_albedo']==.30,'ACTUAL_NATIVE_ENVIRONMENT_NOT_REFERENCE')
    report['physical_design_assumption']='Explicit hypothetical4W/K thermal link, not calibrated spacecraft hardware.'
    check('native_persistent_instance',result['result'].get('persistent_instance') is True,'NATIVE_PERSISTENCE_NOT_DECLARED')
    independent=audit_endurance_workspace(workspace)
    report['independent_audit']=independent
    check('independent_observed_running24h',independent.get('observed_running_wall_s',0)>=86400.,'INSUFFICIENT_INDEPENDENT_RUNNING_DURATION')
    check('independent_audit_review_eligible',independent.get('verdict')=='REVIEW_ELIGIBLE','INDEPENDENT_AUDIT_NOT_REVIEW_ELIGIBLE')
    source_audit=independent.get('original_source_audit',{})
    check('original_sources_consistent',independent.get('original_sources_verified') is True
        and source_audit.get('status')=='CONSISTENT','ORIGINAL_SOURCE_AUDIT_NOT_CONSISTENT')
    sampling=source_audit.get('telemetry_sampling',{})
    check('telemetry_cadence_verified',sampling.get('status')=='PASS'
        and sampling.get('off_grid_frame_count',1)==0 and sampling.get('unexpected_interval_count',1)==0,'ORIGINAL_TELEMETRY_CADENCE_NOT_VERIFIED')
    pids=set()
    with (workspace/EVIDENCE_FILE).open(encoding='utf-8') as stream:
        for line in stream:
            obs=json.loads(line)['observation']
            process=obs.get('process',{})
            if obs['session_state']=='RUNNING' and process.get('available') is True:
                pids.add(process.get('pid'))
    runtime_pid=result['result'].get('metadata',{}).get('native_cpu_scheduling',{}).get('pid')
    check('one_observed_native_pid_matches_runtime',len(pids)==1 and None not in pids and runtime_pid in pids,'NATIVE_INSTANCE_PID_NOT_CONSISTENT')
    report['observed_native_pids']=sorted(pids)
    integrity=verify_run_bundle(workspace/'run_bundle')
    check('sealed_bundle_integrity',integrity.get('ok') is True,'BUNDLE_INTEGRITY_FAILED')
    after={str(path):sha(path) for path in watched if path.is_file()}
    check('original_evidence_unchanged_by_review',before==after,'ORIGINAL_EVIDENCE_CHANGED_DURING_REVIEW')
    report['input_file_sha256']=before
    report['reason_codes']=sorted(set(report['reason_codes']))
    report['status']='TECHNICAL_EVIDENCE_READY_FOR_HUMAN_ASSESSMENT' if all(report['checks'].values()) else 'NOT_QUALIFIED'
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,default=REPORTS/'hardware-bounded-r11-endurance/endurance-24h')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    output=args.output.resolve()
    if not output.is_relative_to(ROOT) or output.exists():
        parser.error('A fresh review output inside this workspace is required')
    try:report=review(args.run_dir)
    except Exception as exc:
        report={'status':'REVIEW_ERROR','error':{'type':type(exc).__name__,'message':str(exc)},
            'manual_review_required':True,'automatic_release_approval':False}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'reason_codes':report.get('reason_codes'),'error':report.get('error')}))
    return int(report['status']!='TECHNICAL_EVIDENCE_READY_FOR_HUMAN_ASSESSMENT')


if __name__=='__main__':
    raise SystemExit(main())
