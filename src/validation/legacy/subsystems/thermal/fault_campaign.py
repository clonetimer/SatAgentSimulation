"""
热控系统分系统故障场景测试脚本

定义并运行热控系统分系统的故障测试场景。
"""

from dataclasses import dataclass
from typing import List
from components.fault_spec import FaultSpec
from components.heater.faults import HeaterFaultType
from subsystems.thermal.faults import ThermalFaultType, aggregate_thermal_faults


@dataclass
class ThermalFaultScenario:
    """
    热控系统分系统故障场景数据类

    用于定义热控系统分系统的故障测试场景。

    属性:
        scenario_id: 场景唯一标识符
        fault_specs: 部件故障规范列表
        expected_fault_type: 预期的分系统故障类型
    """
    scenario_id: str
    fault_specs: List[FaultSpec]
    expected_fault_type: ThermalFaultType


def _thermal_fault_scenarios() -> List[ThermalFaultScenario]:
    """
    定义热控系统分系统故障场景列表

    返回热控系统分系统的测试场景，包括 SF4 场景（加热器失效）。

    返回:
        List[ThermalFaultScenario]: 热控系统故障场景列表

    场景定义:
        SF4: 加热器在45秒失效
            - 加热器失效（HeaterFaultType.Failure）
            - 预期聚合结果：ThermalFaultType.HeaterFailure
    """
    scenarios = []

    # SF4: 加热器失效故障
    sf4_fault_specs = [
        FaultSpec(
            fault_type=HeaterFaultType.Failure,
            onset_time_s=45.0,
            duration_s=-1.0,
            magnitude=1.0,
            target_id="heater_1"
        )
    ]

    sf4_scenario = ThermalFaultScenario(
        scenario_id="SF4",
        fault_specs=sf4_fault_specs,
        expected_fault_type=ThermalFaultType.HeaterFailure
    )

    scenarios.append(sf4_scenario)

    return scenarios


def run_thermal_fault_scenario(scenario: ThermalFaultScenario) -> ThermalFaultType:
    """
    运行单个热控系统故障场景

    根据场景定义的故障规范，聚合并返回分系统故障类型。

    参数:
        scenario: 热控系统故障场景

    返回:
        ThermalFaultType: 聚合后的分系统故障类型

    示例:
        scenario = _thermal_fault_scenarios()[0]
        fault_type = run_thermal_fault_scenario(scenario)
        print(f"场景 {scenario.scenario_id} 聚合结果: {fault_type}")
    """
    # 聚合部件故障为分系统故障
    aggregated_fault_type = aggregate_thermal_faults(scenario.fault_specs)

    return aggregated_fault_type


def run_all_thermal_fault_scenarios() -> List[ThermalFaultType]:
    """
    运行所有热控系统故障场景

    遍历所有定义的热控系统故障场景并返回聚合结果列表。

    返回:
        List[ThermalFaultType]: 所有场景的聚合结果列表

    示例:
        results = run_all_thermal_fault_scenarios()
        for i, fault_type in enumerate(results):
            print(f"场景 {i+1} 结果: {fault_type}")
    """
    # 获取所有场景
    scenarios = _thermal_fault_scenarios()

    # 运行所有场景并收集结果
    results = []
    for scenario in scenarios:
        fault_type = run_thermal_fault_scenario(scenario)
        results.append(fault_type)

    return results
