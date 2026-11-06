from __future__ import annotations

from contextlib import contextmanager
import os
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from sat_sim.api import create_app
from sat_sim.scenario_templates import instantiate_scenario_template


@contextmanager
def _goal_workbench(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable:
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium")

    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        queue_database=tmp_path / "queue.sqlite",
        embedded_worker=False,
        auth_mode="disabled",
    )
    client = TestClient(app)
    spec = instantiate_scenario_template("adcs_pointing")
    saved = client.post(
        "/task-center/tasks",
        json={"task_id": "goal-ui-task", "task_spec": spec, "status": "READY"},
    )
    assert saved.status_code == 200, saved.text

    page_errors = []
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: page_errors.append(str(error)))

            def forward(route):
                request = route.request
                url = urlsplit(request.url)
                response = client.request(
                    request.method,
                    url.path + (("?" + url.query) if url.query else ""),
                    content=request.post_data_buffer,
                    headers={
                        key: value
                        for key, value in request.headers.items()
                        if key.lower() in ("content-type", "accept")
                    },
                )
                route.fulfill(
                    status=response.status_code,
                    body=response.content,
                    headers={
                        "content-type": response.headers.get(
                            "content-type", "application/octet-stream"
                        )
                    },
                )

            page.route("https://workbench.test/**", forward)
            page.goto("https://workbench.test/", wait_until="networkidle")
            page.wait_for_selector('.nav-button[data-view="goals"]')
            yield page, client, app, page_errors
        finally:
            browser.close()


def test_goal_run_workspace_creates_budgets_launches_and_interprets(tmp_path):
    with _goal_workbench(tmp_path) as (page, client, app, page_errors):
        page.locator('.nav-button[data-view="goals"]').click()
        page.wait_for_function(
            "document.querySelector('#goalRunSummary').textContent.includes('0 个目标')"
        )
        page.locator("#newGoalRunBtn").click()
        page.locator("#goalBaseTaskSelect").select_option("goal-ui-task")
        page.wait_for_function(
            "document.querySelectorAll('#goalAllowedParameterList input[type=checkbox]').length > 0"
        )

        page.locator("#goalObjective").fill(
            "比较仿真时长候选，验证目标指标并保留运行证据。"
        )
        checkboxes = page.locator("#goalAllowedParameterList input[type=checkbox]")
        for index in range(checkboxes.count()):
            checkbox = checkboxes.nth(index)
            if checkbox.input_value() == "simulation.duration_s":
                if not checkbox.is_checked():
                    checkbox.check()
            elif checkbox.is_checked():
                checkbox.uncheck()
        page.locator("#goalSuccessMetric").fill("final_pointing_error_deg")
        page.locator("#goalSuccessOperator").select_option("<=")
        page.locator("#goalSuccessValue").fill("0.5")
        page.locator("#goalBudgetExperiments").fill("2")
        page.locator("#goalBudgetRuns").fill("4")
        page.locator("#goalBudgetAttempts").fill("4")
        page.locator("#goalBudgetModelCalls").fill("1")
        page.locator("#createGoalRunBtn").click()

        page.wait_for_selector("#goalRunDetail:not(.hidden)")
        page.wait_for_function(
            "document.querySelector('#goalDetailStatus').textContent === '就绪'"
        )
        goal_id = client.get("/agent/goals").json()["goals"][0]["goal_id"]
        assert goal_id in page.locator("#goalDetailMeta").inner_text()

        page.locator("#goalExperimentName").fill("时长候选比较")
        page.locator("#goalExperimentPurpose").fill("比较两个仿真时长候选")
        page.locator("#goalSweepPath").select_option("simulation.duration_s")
        page.locator("#goalSweepValues").fill("10,20")
        page.locator("#previewGoalExperimentBtn").click()
        page.wait_for_function(
            "document.querySelector('#goalExperimentPreview').textContent.includes('variant_count')"
        )
        assert '"variant_count": 2' in page.locator("#goalExperimentPreview").inner_text()

        page.locator("#createGoalExperimentBtn").click()
        page.wait_for_function(
            "document.querySelectorAll('#goalRunExperiments .goal-linked-experiment').length === 1"
        )
        assert "时长候选比较" in page.locator("#goalRunExperiments").inner_text()

        page.locator("#goalRunExperiments button").filter(has_text="启动本轮运行").click()
        page.wait_for_function(
            "document.querySelector('#goalRunExperiments').textContent.includes('运行绑定 2/2')"
        )
        jobs = app.state.execution_queue.list_jobs(limit=10)["jobs"]
        assert len(jobs) == 2
        assert all(job["metadata"]["submitted_by"] == "agent" for job in jobs)

        page.locator("#goalRunExperiments button").filter(has_text="分析本轮证据").click()
        page.locator("#goalInterpretMode").select_option("deterministic")
        page.locator("#interpretGoalRunBtn").click()
        page.wait_for_function(
            "document.querySelector('#goalInterpretResult').textContent.length > 0"
        )
        result_text = page.locator("#goalInterpretResult").inner_text()
        assert "analysis_status" in result_text or "evidence_ready" in result_text

        page.locator("#refreshGoalDetailBtn").click()
        page.wait_for_function(
            "document.querySelectorAll('#goalDecisionLedger li.goal-decision-item').length >= 3"
        )
        assert not page_errors, page_errors
