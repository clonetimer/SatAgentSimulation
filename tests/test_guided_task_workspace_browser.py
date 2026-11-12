"""Guided task workspace prioritizes common actions and preserves task history."""
import os
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from sat_sim.api import create_app


def _task_spec():
    return {
        "schema_version": "1.0.0",
        "task": {"id": "guided-task-ux", "name": "整星姿态控制验证"},
        "simulation": {"duration_s": 30.0, "sample_s": 0.5, "level": "component"},
        "model": {"capability_id": "component.reaction_wheel.v1", "target": {"mode": "nominal"}},
        "parameters": {"profile": "demo", "values": {}},
        "events": {"faults": [], "degradations": []},
        "outputs": {"qoi": [], "plots": [], "output_root": "datasets/guided-task-ux"},
        "assurance": {"parameter_profile": "demo"},
    }


def test_guided_task_workspace_discloses_secondary_actions_and_opens_details(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable:
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium")

    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    with TestClient(app) as client:
        saved = client.post("/task-center/tasks", json={
            "task_id": "guided-task-ux", "task_spec": _task_spec(), "status": "READY",
        })
        assert saved.status_code == 200, saved.text
        assert saved.json()["task"]["name"] == "整星姿态控制验证"
        assert saved.json()["task"]["capability_id"] == "component.reaction_wheel.v1"
        listed = client.get("/task-center/tasks?limit=200")
        assert listed.status_code == 200, listed.text
        assert any(
            task.get("task_id") == "guided-task-ux"
            and task.get("name") == "整星姿态控制验证"
            and task.get("capability_id") == "component.reaction_wheel.v1"
            for task in listed.json().get("tasks", [])
        )
        errors = []
        task_list_payloads = []
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=executable)
            try:
                page = browser.new_page(viewport={"width": 1366, "height": 900})
                page.on("pageerror", lambda error: errors.append(str(error)))

                def proxy(route):
                    request = route.request
                    url = urlsplit(request.url)
                    response = client.request(
                        request.method,
                        url.path + ("?" + url.query if url.query else ""),
                        content=request.post_data_buffer,
                        headers={k: v for k, v in request.headers.items() if k in ("content-type", "accept")},
                    )
                    if request.method == "GET" and url.path == "/task-center/tasks":
                        task_list_payloads.append(response.json())
                    route.fulfill(
                        status=response.status_code,
                        body=response.content,
                        headers={"content-type": response.headers.get("content-type", "application/octet-stream")},
                    )

                page.route("https://workbench.test/**", proxy)
                page.goto("https://workbench.test/", wait_until="networkidle")
                page.locator('#wizardOptions [data-wizard-goal="history"]').click()
                page.locator("#wizardTaskWorkspace").wait_for(state="visible", timeout=10000)
                card = page.locator("#wizardTaskList .wizard-task-card").filter(has_text="整星姿态控制验证")
                try:
                    card.wait_for(state="visible", timeout=10000)
                except Exception as error:
                    raise AssertionError(
                        "Task history did not render; "
                        f"summary={page.locator('#wizardTaskSummary').inner_text()!r}; "
                        f"API task payloads={task_list_payloads!r}"
                    ) from error
                assert card.get_by_role("button", name="任务详情").is_visible()
                assert card.get_by_role("button", name="编辑配置").is_visible()
                assert card.get_by_role("button", name="运行任务").is_visible()

                more = card.locator("details.wizard-task-more")
                assert more.get_attribute("open") is None
                more.locator("summary").click()
                assert more.get_attribute("open") is not None
                assert more.get_by_role("button", name="克隆任务").is_visible()
                assert more.get_by_role("button", name="删除任务").is_visible()

                more.get_by_role("button", name="克隆任务").click()
                page.wait_for_function("document.querySelectorAll('#wizardTaskList .wizard-task-card').length === 2")
                assert "已克隆任务" in page.locator("#wizardNotice").inner_text()

                card = page.locator("#wizardTaskList .wizard-task-card").filter(has_text="整星姿态控制验证")
                card.get_by_role("button", name="任务详情").click()
                page.locator("#taskDetailsModal").wait_for(state="visible")
                assert "V1" in page.locator("#taskDetailsSummary").inner_text()
                assert page.locator("#taskVersionTableBody tr").count() >= 1
                page.locator("#closeTaskDetailsBtn").click()

                # Return to the wizard's welcome step, then enter each specialist
                # workspace through the same visible "其他专业工具" cards as users.
                page.locator("#wizardBackBtn").click()
                tools = page.locator("#wizardAdditionalTools")
                tools.wait_for(state="visible")
                if tools.get_attribute("open") is None:
                    tools.locator("summary").click()
                assert tools.get_attribute("open") is not None

                specialist_views = (
                    ("experiments", "experimentCenterView"),
                    ("goals", "goalRunView"),
                    ("diagnostics", "diagnosticView"),
                    ("runs", "runCenterView"),
                )
                for goal, view_id in specialist_views:
                    tools.locator(f'[data-wizard-goal="{goal}"]').click()
                    page.locator(f"#{view_id}").wait_for(state="visible", timeout=10000)
                    page.locator(f"#{view_id} [data-return-wizard]").click()
                    assert page.locator("body").get_attribute("data-active-view") == "wizard"
                    assert "当前配置、任务版本和已有运行记录保持不变" in page.locator("#wizardConversation").inner_text()
                    assert tools.is_visible()
                    assert tools.get_attribute("open") is not None

                    # Reopen task history after every round trip to prove the
                    # task list and cloned version records remain reachable.
                    page.locator('#wizardOptions [data-wizard-goal="history"]').click()
                    original = page.locator("#wizardTaskList .wizard-task-card").filter(
                        has_text="整星姿态控制验证"
                    )
                    original.wait_for(state="visible", timeout=10000)
                    assert page.locator("#wizardTaskList .wizard-task-card").count() == 2
                    page.locator("#wizardBackBtn").click()
                    assert tools.is_visible() and tools.get_attribute("open") is not None

                assert not errors, errors
            finally:
                browser.close()
