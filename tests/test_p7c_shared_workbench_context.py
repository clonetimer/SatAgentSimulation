from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_p7c_shared_context_is_mounted_in_all_specialist_workspaces():
    html = (ROOT / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    assert html.count("data-p7-context") == 4
    for view in ("experimentCenterView", "goalRunView", "runCenterView", "diagnosticView"):
        section = html.split('id="' + view + '"', 1)[1].split("</main>", 1)[0]
        assert "data-p7-context" in section, view


def test_p7c_shared_context_behaviour_against_production_javascript():
    result = subprocess.run(
        ["node", "tests/js/shared_context_vm.js"], cwd=ROOT,
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
