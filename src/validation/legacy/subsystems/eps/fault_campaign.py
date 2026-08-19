"""
EPS 分系统故障场景测试脚本

定义并运行 EPS 分系统的故障测试场景。
"""

from dataclasses import dataclass
from typing import List
from components.fault_spec import FaultSpec
from components.battery.faults import BatteryFaultType
from components.solar_panel.faults import SolarPanelFaultType
from subsystems.eps.faults import EPSFaultType, aggregate_eps_faults


@dataclass
class EPSFaultScenario:
    """
    EPS 分系统故障场景数据类

    用于定义 EPS 分系统的故障测试场景。

    属性:
        scenario_id: 场景唯一标识符
        fault_specs: 部件故障规范列表
        expected_fault_type: 预期的分系统故障类型
    """
    scenario_id: str
    fault_specs: List[FaultSpec]
    expected_fault_type: EPSFaultType


def _eps_fault_scenarios() -> List[EPSFaultScenario]:
    """
    定义 EPS 分系统故障场景列表

    返回 EPS 分系统的测试场景，包括 SF1 场景（EPS功率总线故障）。

    返回:
        List[EPSFaultScenario]: EPS 故障场景列表

    场景定义:
        SF1: EPS功率总线在30秒失效
            - 电池开路故障（BatteryFaultType.OpenCircuit）
            - 太阳能帆板失效（SolarPanelFaultType.Failure）
            - 预期聚合结果：EPSFaultType.PowerBusFailure
    """
    scenarios = []

    # SF1: EPS功率总线故障
    sf1_fault_specs = [
        FaultSpec(
            fault_type=BatteryFaultType.OpenCircuit,
            onset_time_s=30.0,
            duration_s=-1.0,
            magnitude=1.0,
            target_id="battery_1"
        ),
        FaultSpec(
            fault_type=SolarPanelFaultType.Failure,
            onset_time_s=30.0,
            duration_s=-1.0,
            magnitude=1.0,
            target_id="solar_panel_1"
        )
    ]

    sf1_scenario = EPSFaultScenario(
        scenario_id="SF1",
        fault_specs=sf1_fault_specs,
        expected_fault_type=EPSFaultType.PowerBusFailure
    )

    scenarios.append(sf1_scenario)

    return scenarios


def run_eps_fault_scenario(scenario: EPSFaultScenario) -> EPSFaultType:
    """
    运行单个 EPS 故障场景

    根据场景定义的故障规范，聚合并返回分系统故障类型。

    参数:
        scenario: EPS 故障场景

    返回:
        EPSFaultType: 聚合后的分系统故障类型

    示例:
        scenario = _eps_fault_scenarios()[0]
        fault_type = run_eps_fault_scenario(scenario)
        print(f"场景 {scenario.scenario_id} 聚合结果: {fault_type}")
    """
    # 聚合部件故障为分系统故障
    aggregated_fault_type = aggregate_eps_faults(scenario.fault_specs)

    return aggregated_fault_type


def run_all_eps_fault_scenarios() -> List[EPSFaultType]:
    """
    运行所有 EPS 故障场景

    遍历所有定义的 EPS 故障场景并返回聚合结果列表。

    返回:
        List[EPSFaultType]: 所有场景的聚合结果列表

    示例:
        results = run_all_eps_fault_scenarios()
        for i, fault_type in enumerate(results):
            print(f"场景 {i+1} 结果: {fault_type}")
    """
    # 获取所有场景
    scenarios = _eps_fault_scenarios()

    # 运行所有场景并收集结果
    results = []
    for scenario in scenarios:
        fault_type = run_eps_fault_scenario(scenario)
        results.append(fault_type)

    return results
