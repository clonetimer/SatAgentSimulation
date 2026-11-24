"""Common real-browser inputs for the whole-spacecraft creation matrix."""
import json
import os
import time

from playwright.sync_api import expect


def apply_json_case(page):
    from sat_sim.scenario_templates import instantiate_scenario_template

    spec = instantiate_scenario_template("whole_spacecraft_unified_native", task_id="dt_creation_matrix")
    spec["simulation"].update(duration_s=20.0, step_s=0.1, sample_s=0.5, random_seed=0)
    values = spec["parameters"]["values"]
    values.update({f"initial_body_rate_{axis}_rad_s": value
                   for axis, value in zip("xyz", [-0.003, 0.0, 0.006])})
    values["epoch_utc"] = "2026-01-01T00:00:00Z"
    page.locator('[data-wizard-goal="taskspec"]').click()
    page.locator("#taskSpecEditor").fill(json.dumps(spec))
    page.locator("#wizardValidateTaskSpecBtn").click()
    expect(page.locator("#wizardNotice")).to_contain_text("TaskSpec 校验通过")
    page.locator("#wizardApplyTaskSpecBtn").click()
    expect(page.locator("#wizardConfigSections")).to_be_visible()
    return spec


def configure_graph_case(page):
    page.locator(".capability-card.active").wait_for(state="attached")
    page.locator("#wizardAdvancedBtn").click()
    page.locator("#creatorGraphTabButton").click()
    node = page.locator('#graphNodes [data-node-id="graph-config"] .graph-node-head')
    node.scroll_into_view_if_needed()
    box = node.bounding_box()
    x, y = box["x"] + 24, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 35, y + 12, steps=8)
    page.mouse.up()
    moved = node.bounding_box()
    assert abs(moved["x"] - box["x"]) > 5
    node.click()
    inspector = page.locator("#graphInspector")
    inputs = inspector.locator(".graph-inspector-field input")
    for i, value in enumerate([20, 0.5, 0.1]):
        inputs.nth(i).fill(str(value))
        inputs.nth(i).press("Tab")
    inspector.get_by_role("button", name="全部参数").click()
    for axis, value in zip("xyz", [-0.003, 0.0, 0.006]):
        path = f"parameters.values.initial_body_rate_{axis}_rad_s"
        page.locator("#formFieldSearch").fill(path)
        page.locator(f'[data-path="{path}"]').fill(str(value))
        page.locator("#formFieldSearch").focus()
    page.locator("#creatorGraphTabButton").click()
    page.locator("#graphValidateBtn").click()
    expect(page.locator("#graphStatus")).to_contain_text("图已校验", timeout=30000)


def register_local_model(page, base):
    profile = {"service_type": "modelscope", "model": os.environ["SAT_SIM_DT_MODEL_NAME"],
               "base_url": os.environ["SAT_SIM_DT_MODEL_URL"]}
    saved = page.request.post(base + "/models/local-services", data=profile)
    assert saved.ok, saved.text()


def apply_natural_language_case(page, evidence):
    page.locator('[data-wizard-goal="natural_language"]').click()
    page.locator("#agentRefineCurrent").uncheck()
    page.locator("#agentRoutingMode").select_option("local")
    page.locator("#agentBackend").select_option("local-modelscope")
    prompt = ("创建标称近地小卫星整星任务，使用 whole_spacecraft.unified_native.v1 和 Basilisk。"
              "仿真 20 秒，步长 0.1 秒，记录间隔 0.5 秒，随机种子 0。历元 2026-01-01T00:00:00Z。"
              "在机体系中，初始角速度 X 为 -0.003 rad/s，Y 为 0 rad/s，Z 为 0.006 rad/s。"
              "请在 parameters.values 中使用 initial_body_rate_x_rad_s、initial_body_rate_y_rad_s、"
              "initial_body_rate_z_rad_s。使用默认工程环境与默认输出，无故障或退化。")
    page.locator("#agentInput").fill(prompt)
    started = time.monotonic()
    with page.expect_response(lambda r: r.url.endswith("/tasks/parse") and r.request.method == "POST"
                              and r.request.post_data_json.get("input_kind") == "natural_language",
                              timeout=780000) as pending:
        page.locator("#wizardGenerateFromTextBtn").click()
    response = pending.value.json()
    (evidence / "generation.json").write_text(json.dumps(
        {"prompt": prompt, "elapsed_s": time.monotonic() - started, "response": response},
        ensure_ascii=False), encoding="utf8")
    result = response["result"]
    assert response["ok"], result.get("validation")
    identity = result["facade_result"]["backend_identity"]
    assert identity["actual_model_execution_verified"] and identity["actual_model_used"]
    assert identity["model_name"] == os.environ["SAT_SIM_DT_MODEL_NAME"]
    spec = result["task_spec"]
    assert not spec.get("metadata", {}).get("agent_safety", {}).get("model_fallback_used")
    for key, value in {"duration_s": 20, "step_s": 0.1, "sample_s": 0.5, "random_seed": 0}.items():
        assert spec["simulation"][key] == value, key
    for axis, value in zip("xyz", [-0.003, 0.0, 0.006]):
        assert spec["parameters"]["values"][f"initial_body_rate_{axis}_rad_s"] == value
    page.locator("#wizardApplyAgentCandidateBtn").click()
    expect(page.locator("#wizardConfigSections")).to_be_visible()
    return spec
