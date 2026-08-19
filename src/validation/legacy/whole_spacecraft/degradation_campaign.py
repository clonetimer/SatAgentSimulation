"""退化场景测试脚本。

用于运行整星级退化场景测试，验证不同退化条件下的卫星性能。
"""
from dataclasses import dataclass
from typing import Dict, Any

from validation.legacy.whole_spacecraft.degradation_config import (
    WholeSatelliteDegradation,
    DegradationScenario,
    get_degradation_scenario_config,
)
from validation.legacy.whole_spacecraft.selective_unified_assembly import (
    run_selective_unified_assembly,
    SelectiveUnifiedAssemblyConfig,
)


@dataclass
class DegradationCampaignScenario:
    """退化场景测试配置。

    用于定义和执行退化场景测试。

    Attributes:
        scenario_id: 场景唯一标识符
        scenario_name: 场景名称
        degradation: 整星级退化状态配置
        expected_soc_min: 预期最低SOC（电池荷电状态）
        expected_power_balance: 预期功率是否平衡
    """
    scenario_id: str
    scenario_name: str
    degradation: WholeSatelliteDegradation
    expected_soc_min: float
    expected_power_balance: bool


@dataclass
class DegradationCampaignResult:
    """退化场景测试结果。

    用于记录退化场景测试的执行结果。

    Attributes:
        scenario_id: 场景唯一标识符
        scenario_name: 场景名称
        actual_soc_min: 实际最低SOC
        actual_soc_final: 实际最终SOC
        actual_power_balance: 实际功率是否平衡
        propellant_used_kg: 推进剂消耗量
        delta_v_achieved_m_s: 达成的delta-V值
        simulation_status: 仿真状态（PASS/FAIL）
        expected_soc_min: 预期最低SOC
        expected_power_balance: 预期功率是否平衡
        degradation_loss_pct: 总任务能力损失百分比
    """
    scenario_id: str
    scenario_name: str
    actual_soc_min: float
    actual_soc_final: float
    actual_power_balance: bool
    propellant_used_kg: float
    delta_v_achieved_m_s: float
    simulation_status: str
    expected_soc_min: float
    expected_power_balance: bool
    degradation_loss_pct: float


def _degradation_scenarios() -> list[DegradationCampaignScenario]:
    """定义退化场景测试列表。

    返回D1-D4场景的测试配置：
    - D1: 电池容量损失30%，预期SOC最低降至0.3
    - D2: 帆板效率损失20%，预期功率平衡可能波动
    - D3: 推进器推力损失15%，预期delta-V达成率降低
    - D4: 组合退化，预期综合性能下降

    Returns:
        退化场景测试配置列表
    """
    scenarios = []

    # D1: 电池容量损失30%
    d1_degradation = get_degradation_scenario_config(DegradationScenario.D1)
    scenarios.append(DegradationCampaignScenario(
        scenario_id="D1",
        scenario_name="电池容量损失30%",
        degradation=d1_degradation,
        expected_soc_min=0.3,  # 容量损失30%，SOC最低可能降至0.3
        expected_power_balance=True,  # 功率平衡应该维持
    ))

    # D2: 帆板效率损失20%
    d2_degradation = get_degradation_scenario_config(DegradationScenario.D2)
    scenarios.append(DegradationCampaignScenario(
        scenario_id="D2",
        scenario_name="帆板效率损失20%",
        degradation=d2_degradation,
        expected_soc_min=0.4,  # 帆板效率降低，SOC可能更低
        expected_power_balance=False,  # 功率平衡可能波动（发电能力降低）
    ))

    # D3: 推进器推力损失15%
    d3_degradation = get_degradation_scenario_config(DegradationScenario.D3)
    scenarios.append(DegradationCampaignScenario(
        scenario_id="D3",
        scenario_name="推进器推力损失15%",
        degradation=d3_degradation,
        expected_soc_min=0.5,  # 推力损失不影响SOC
        expected_power_balance=True,  # 功率平衡应该维持
    ))

    # D4: 组合退化
    d4_degradation = get_degradation_scenario_config(DegradationScenario.D4)
    scenarios.append(DegradationCampaignScenario(
        scenario_id="D4",
        scenario_name="组合退化（电池30%+帆板20%+推力15%）",
        degradation=d4_degradation,
        expected_soc_min=0.2,  # 组合退化，SOC最低可能降至0.2
        expected_power_balance=False,  # 功率平衡可能波动
    ))

    return scenarios


def run_degradation_scenario(scenario: DegradationCampaignScenario) -> DegradationCampaignResult:
    """运行单个退化场景测试。

    执行流程：
    1. 获取退化配置
    2. 创建带有退化参数的仿真配置
    3. 调用 selective_unified_assembly 运行仿真
    4. 收集结果（SOC、功率、delta-V）

    Args:
        scenario: 退化场景测试配置

    Returns:
        退化场景测试结果
    """
    # 创建带有退化参数的仿真配置
    config = SelectiveUnifiedAssemblyConfig(
        degradation=scenario.degradation,
        duration_s=300.0,  # 仿真时长300秒
        propulsion_enabled=True,  # 启用推进系统
    )

    # 运行仿真
    summary, trace_rows = run_selective_unified_assembly(config)

    # 分析结果
    # 提取SOC轨迹数据
    soc_values = [row.battery_soc for row in trace_rows]
    actual_soc_min = min(soc_values) if soc_values else 0.0
    actual_soc_final = soc_values[-1] if soc_values else 0.0

    # 分析功率平衡
    # 判断功率是否平衡：如果SOC下降过快或出现负载shedding，则功率不平衡
    load_shed_count = summary.load_shed_event_count
    actual_power_balance = (load_shed_count == 0 and actual_soc_final >= 0.1)

    # 提取推进数据
    propellant_used_kg = summary.propellant_used_kg
    delta_v_achieved_m_s = abs(summary.final_velocity_x_m_s)

    # 判断仿真状态
    # PASS条件：SOC不低于预期最低值（有一定容差），仿真状态为PASS
    simulation_status = summary.status if actual_soc_min >= (scenario.expected_soc_min - 0.05) else "FAIL"

    # 计算总任务能力损失百分比
    degradation_loss_pct = scenario.degradation.total_mission_capability_loss_pct()

    # 构建结果
    result = DegradationCampaignResult(
        scenario_id=scenario.scenario_id,
        scenario_name=scenario.scenario_name,
        actual_soc_min=actual_soc_min,
        actual_soc_final=actual_soc_final,
        actual_power_balance=actual_power_balance,
        propellant_used_kg=propellant_used_kg,
        delta_v_achieved_m_s=delta_v_achieved_m_s,
        simulation_status=simulation_status,
        expected_soc_min=scenario.expected_soc_min,
        expected_power_balance=scenario.expected_power_balance,
        degradation_loss_pct=degradation_loss_pct,
    )

    return result


def run_all_degradation_scenarios() -> Dict[str, Any]:
    """运行所有退化场景测试。

    遍历所有退化场景（D1-D4），执行测试并返回结果摘要。

    Returns:
        包含所有场景测试结果的字典，格式：
        {
            "scenarios": [DegradationCampaignResult, ...],
            "summary": {
                "total_scenarios": int,
                "pass_count": int,
                "fail_count": int,
                "average_soc_min": float,
                "average_power_balance_rate": float,
                "average_degradation_loss_pct": float,
            }
        }
    """
    scenarios = _degradation_scenarios()
    results = []

    # 运行每个场景
    for scenario in scenarios:
        result = run_degradation_scenario(scenario)
        results.append(result)

    # 生成摘要
    total_scenarios = len(results)
    pass_count = sum(1 for r in results if r.simulation_status == "PASS")
    fail_count = total_scenarios - pass_count

    average_soc_min = sum(r.actual_soc_min for r in results) / total_scenarios if total_scenarios > 0 else 0.0
    power_balance_count = sum(1 for r in results if r.actual_power_balance)
    average_power_balance_rate = power_balance_count / total_scenarios if total_scenarios > 0 else 0.0

    average_degradation_loss_pct = sum(r.degradation_loss_pct for r in results) / total_scenarios if total_scenarios > 0 else 0.0

    summary = {
        "total_scenarios": total_scenarios,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "average_soc_min": average_soc_min,
        "average_power_balance_rate": average_power_balance_rate,
        "average_degradation_loss_pct": average_degradation_loss_pct,
    }

    return {
        "scenarios": results,
        "summary": summary,
    }


if __name__ == "__main__":
    """命令行入口。

    执行所有退化场景测试并打印结果摘要。
    """
    import json
    from dataclasses import asdict

    # 运行所有退化场景
    campaign_results = run_all_degradation_scenarios()

    # 转换为可序列化的格式
    output = {
        "scenarios": [asdict(r) for r in campaign_results["scenarios"]],
        "summary": campaign_results["summary"],
    }

    # 打印结果
    print(json.dumps(output, indent=2, ensure_ascii=False))
