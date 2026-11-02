"""Verify archived distribution evidence; no network, inference or simulation."""
from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET

BASE = Path(__file__).resolve().parent


def load(name):
    return json.loads((BASE / name).read_bytes())


def require(value, message):
    if not value:
        raise RuntimeError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    manifest = load('evidence-manifest.json')
    files = set()
    for row in manifest['files']:
        path = (BASE / row['path']).resolve()
        require(path.is_relative_to(BASE) and row['path'] not in files, 'Invalid manifest path')
        files.add(row['path'])
        raw = path.read_bytes()
        require(len(raw) == row['bytes'] and sha(raw) == row['sha256'], 'Changed evidence: ' + row['path'])
    require(files == {p.name for p in BASE.iterdir() if p.is_file() and p.name != 'evidence-manifest.json'}, 'Packet membership changed')
    verification = load('verification.json')
    require(all(verification['checks'].values()), 'Verification check failed')
    cases = list(ET.parse(BASE / 'installer-green.xml').getroot().iter('testcase'))
    require(len(cases) == 37 and all(c.find('failure') is None and c.find('error') is None and c.find('skipped') is None for c in cases), 'Scoped regression differs')
    governance = load('governance.json')
    require(governance['status'] == 'PASS' and not governance['issues'], 'Governance differs')
    lock = load('release-lock.json')
    release = load('github-release-state.json')
    require(not release['draft'] and release['tag'] == lock['release_id'], 'Release not published')
    assets = {a['name']: a for a in release['assets']}
    archive = assets['satagent-v5-r47-lora.zip']
    require(archive['size'] == lock['archive']['bytes'] and archive['digest'] == 'sha256:' + lock['archive']['sha256'], 'Release artifact differs')
    online = load('public-online-install-proof.json')
    require(online['status'] == 'PASS_PUBLIC_ANONYMOUS_CLONE_FULL_NETWORK_INSTALL_AND_CHECK', 'Public installation failed')
    require(online['cloud_commit'] == verification['source_commit'] and not online['local_archive_override']
            and not online['local_base_directory_override'] and online['installer_uses_no_authentication'], 'Public install scope differs')
    require(online['archive_sha256'] == lock['archive']['sha256'] and online['base_snapshot_content_sha256'] == lock['base']['snapshot_content_sha256'], 'Installed model differs')
    real = load('relocated-generation-proof.json')
    require(real['request_count'] == real['completed_count'] == 1 and real['automatic_retries'] == 0
            and real['template_used'] is False and real['script_exit_code'] == 0, 'Actual generation differs')
    require(real['script_sha256'] == sha((BASE / 'generated.py').read_bytes()), 'Generated script changed')
    call = load('model_call.json')
    require(call['ok'] is True and call['metadata']['model'] == lock['model_name']
            and call['invocation_evidence']['verified'] is True, 'Actual model provenance differs')
    raw = call['raw_text']
    require(sha(raw.encode('utf-8')) == call['invocation_evidence']['response_sha256'], 'Raw response digest differs')
    # The original Windows write_text preserves its newline translation bytes.
    # Bind that deterministic serialization without normalizing JSON values.
    artifact = (BASE / 'raw_model_output.txt').read_bytes()
    require(artifact in (raw.encode('utf-8'), raw.replace('\n', '\r\n').encode('utf-8')), 'Raw artifact serialization differs')
    native = load('native-script-result.json')
    require(native['ok'] is True and native['validation_outcome']['result'] == 'PASS'
            and native['summary']['engine'] == 'basilisk_unified_native_runtime', 'Original native script failed')
    before, after = load('model-health-before.json'), load('model-health-after.json')
    require(before['counts']['accepted'] == 0 and after['counts']['accepted'] == after['counts']['completed'] == 1, 'Inference count differs')
    require(before['source']['training_provenance_sha256'] == after['source']['training_provenance_sha256'] == lock['training_provenance_sha256'], 'Training provenance differs')
    root = BASE.parents[2]
    for rel, expected in verification['source_crlf_to_lf_sha256'].items():
        require(sha((root / rel).read_bytes().replace(b'\r\n', b'\n')) == expected, 'Distribution source differs: ' + rel)
    print(json.dumps({'status': 'PASS_ARCHIVED_MODEL_DISTRIBUTION_EVIDENCE', 'scoped_tests_passed': 37,
                      'actual_new_model_requests': 1, 'public_online_install_verified': True,
                      'new_network_or_model_calls': False}))


if __name__ == '__main__':
    main()
