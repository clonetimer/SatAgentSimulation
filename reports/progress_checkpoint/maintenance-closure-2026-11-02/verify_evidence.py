"""Verify archived evidence with Python stdlib; does not rerun tests or simulation."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_manifest(base, manifest):
    rows = manifest['files']
    seen = set()
    for row in rows:
        rel = row['path']
        path = (base / rel).resolve()
        require(path.is_relative_to(base.resolve()) and rel not in seen, 'Invalid/duplicate manifest path')
        seen.add(rel)
        require(path.is_file(), 'Missing payload: ' + rel)
        require(path.stat().st_size == row['bytes'] and sha(path) == row['sha256'], 'Changed payload: ' + rel)
    return seen


def cases(path):
    for case in ET.parse(path).getroot().iter('testcase'):
        state = ('FAILED' if case.find('failure') is not None or case.find('error') is not None else
                 'SKIPPED' if case.find('skipped') is not None else 'PASSED')
        yield (case.attrib['classname'], case.attrib['name']), state


def verify(base, source_root=None):
    manifest = load(base / 'evidence-manifest.json')
    listed = verify_manifest(base, manifest)
    actual = {p.relative_to(base).as_posix() for p in base.rglob('*')
              if p.is_file() and '__pycache__' not in p.parts and p.name != 'evidence-manifest.json'}
    require(listed == actual, 'Packet files differ from manifest')
    coverage = load(base / 'coverage.json')
    expected_rows = load(base / 'expected-cases.json')
    expected = {(r['classname'], r['name']): r for r in expected_rows}
    require(len(expected) == len(expected_rows) == 2516, 'Collection identity mismatch')
    require(sha(base / 'expected-cases.json') == coverage['expected_cases_sha256'], 'Collection hash mismatch')
    require(sha(base / 'current-test-files.json') == coverage['current_test_files_sha256'], 'Test manifest hash mismatch')
    definitions = [('original_full', 1200, 1, 5), ('finite_revalidated', 1, 0, 0),
                   ('remaining_first', 977, 1, 0), ('run_panel_revalidated', 1, 0, 0),
                   ('governance', 10, 0, 0), ('tail', 322, 0, 0)]
    latest = {}
    replacements = []
    for name, passed, failed, skipped in definitions:
        path = base / (name + '-junit.xml')
        raw = next(r for r in coverage['raw_executions'] if r['name'] == name)
        require(sha(path) == raw['junit_sha256'], 'Raw JUnit hash mismatch')
        counts = Counter()
        seen = set()
        for key, state in cases(path):
            require(key in expected and key not in seen, 'Unknown/duplicate case within execution')
            seen.add(key)
            counts[state] += 1
            if key in latest:
                require(latest[key]['state'] == 'FAILED' and state == 'PASSED', 'Invalid result replacement')
                replacements.append({'nodeid': expected[key]['nodeid'],
                                     'failed_execution': latest[key]['segment'], 'passed_execution': name})
            latest[key] = {'state': state, 'segment': name}
        stats = {s: counts[s] for s in ['PASSED', 'FAILED', 'SKIPPED']}
        require(stats == {'PASSED': passed, 'FAILED': failed, 'SKIPPED': skipped}, 'Execution counts differ')
        require(stats == coverage['execution_segments'][name], 'Coverage segment differs')
        if name != 'governance':
            returned = load(base / (name + '-returned.json'))
            require(returned['exit_code'] == (1 if failed else 0), 'Execution exit mismatch')
            require(returned['source_and_imports_stable'] is True, 'Source/import instability')
            require(returned['application_before'] == returned['application_after'] == coverage['application'],
                    'Application changed across executions')
    require(set(latest) == set(expected), 'Incomplete collection coverage')
    totals = Counter(row['state'] for row in latest.values())
    require(totals == Counter(PASSED=2511, SKIPPED=5), 'Latest case results differ')
    require(replacements == coverage['failed_cases_revalidated'] and len(replacements) == 2,
            'Unexpected revalidations')
    require(coverage['single_full_command_pass_claim'] is False and
            coverage['original_full_status'] == coverage['original_remaining_status'] == 'FAILED_RETAINED',
            'Original failures were relabelled')
    for key, row in latest.items():
        if row['segment'] in {'original_full', 'remaining_first'}:
            require(expected[key]['file'] not in coverage['fixture_changes'], 'Changed test prefix reused')
    for name, count in [('targeted-junit.xml', 23), ('installed-browser-junit.xml', 4)]:
        result = list(cases(base / name))
        require(len(result) == count and all(s == 'PASSED' for _, s in result), 'Supplemental check differs')
    installed = load(base / 'installed-browser-returned.json')
    require(installed['exit_code'] == 0 and installed['all_imports_owned'] is True and
            not installed['foreign_project_imports'], 'Installed browser origin mismatch')
    build = load(base / 'build-install-proof.json')
    require(all(build['checks'].values()) and len(build['wheel_closure']['matched_payload']) == 869,
            'Build/install closure incomplete')
    require(build['wheel']['sha256'] == coverage['wheel_sha256'], 'Wheel identity differs')
    require(all(load(base / 'environment-proof.json')['checks'].values()), 'Environment check failed')
    child = load(base / 'finite-decoder-child.json')
    require(child['tests_run'] == 17 and child['failures'] == child['errors'] == child['skipped'] == 0,
            'Finite decoder checks differ')
    require(child['audit']['socket'] == child['audit']['weights'] == 0 and child['cuda_initialized'] is False,
            'Unexpected decoder fixture scope')
    require(child['tokenizer_fixture'] == {'actual_preprocessing_builds': 1, 'reused_preprocessing': 5,
                                         'fresh_parser_per_path': True}, 'Tokenizer fixture changed')
    live = load(base / 'live-native-web.json')
    require(live['status'] == 'PASS_ACTUAL_INSTALLED_NATIVE_WEB_NOT24H' and
            len(live['checks']) == 11 and all(live['checks'].values()), 'Actual installed web checks differ')
    require(live['model_before'] == live['model_after'], 'Unexpected new inference')
    smoke = load(base / 'native-smoke.json')
    require(smoke['status'] == 'SMOKE_COMPLETE_NOT_24H' and smoke['session']['state'] == 'COMPLETED'
            and smoke['session']['archive_status'] == 'SEALED' and smoke['validation_outcome']['result'] == 'PASS',
            'Actual native smoke failed')
    acceptance = load(base / 'acceptance.json')
    require(acceptance['all_scoped_local_functional_tasks_completed'] is True and
            acceptance['original24_automatic'] == 'NOT_QUALIFIED' and
            acceptance['original24_override'] is False, 'Acceptance boundary mismatch')
    for folder, binding in load(base / 'prior-packets-current-verification.json').items():
        previous = base.parent / folder
        previous_manifest = previous / 'evidence-manifest.json'
        require(sha(previous_manifest) == binding['manifest_sha256'], 'Historical manifest changed')
        require(len(verify_manifest(previous, load(previous_manifest))) == binding['payloads_verified'],
                'Historical payload count mismatch')
    normalized = load(base / 'source-normalized-hashes.json')['files']
    require(len(normalized) == len({r['path'] for r in normalized}) == 1188, 'Source manifest incomplete')
    if source_root is not None:
        source_root = source_root.resolve()
        for row in normalized:
            path = (source_root / row['path']).resolve()
            require(path.is_relative_to(source_root), 'Source path outside root')
            raw = path.read_bytes()
            value = raw.replace(b'\r\n', b'\n') if row['normalization'] == 'CRLF_TO_LF' else raw
            require(hashlib.sha256(value).hexdigest() == row['sha256'], 'Source mismatch: ' + row['path'])
    return {'status': 'PASS_ARCHIVED_EVIDENCE_VERIFIED', 'collected': 2516, 'passed': 2511, 'skipped': 5,
            'unverified': 0, 'latest_case_failures': 0, 'source_files_verified': len(normalized) if source_root else 0,
            'single_full_command_pass_claim': False, 'new_test_or_simulation_execution': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(Path(__file__).resolve().parent, args.source_root)))
