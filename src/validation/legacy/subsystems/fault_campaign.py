"""
分系统故障场景汇总模块

整合所有分系统的故障测试场景，提供统一的运行接口。
"""

from typing import List, Union
from validation.legacy.subsystems.eps.fault_campaign import (
    EPSFaultScenario,
    _eps_fault_scenarios,
    run_eps_fault_scenario,
)
from validation.legacy.subsystems.propulsion.fault_campaign import (
    PropulsionFaultScenario,
    _propulsion_fault_scenarios,
    run_propulsion_fault_scenario,
)
from validation.legacy.subsystems.adcs.fault_campaign import (
    ADCSFaultScenario,
    _adcs_fault_scenarios,
    run_adcs_fault_scenario,
)
from validation.legacy.subsystems.thermal.fault_campaign import (
    ThermalFaultScenario,
    _thermal_fault_scenarios,
    run_thermal_fault_scenario,
)


# 定义故障场景类型的联合类型
FaultScenario = Union[
    EPSFaultScenario,
    PropulsionFaultScenario,
    ADCSFaultScenario,
    ThermalFaultScenario
]


def _subsystem_fault_scenarios() -> List[FaultScenario]:
    """
    定义分系统故障场景列表

    返回所有分系统的测试场景，包括 SF1-SF4 场景。

    返回:
        List[FaultScenario]: 分系统故障场景列表

    场景列表:
        SF1: EPS功率总线在30秒失效
            - BatteryFaultType.OpenCircuit + SolarPanelFaultType.Failure
            - 预期：EPSFaultType.PowerBusFailure

        SF2: 推进器集群2个失效
            - ThrusterFaultType.IgnitionFailure (magnitude=2)
            - 预期：PropulsionFaultType.ThrusterClusterFailure

        SF3: ADCS姿态控制降级
            - RWFaultType.Jamming
            - 预期：ADCSFaultType.ActuatorFailure

        SF4: 加热器在45秒失效
            - HeaterFaultType.Failure
            - 预期：ThermalFaultType.HeaterFailure
    """
    scenarios = []

    # 添加 EPS 场景（SF1）
    eps_scenarios = _eps_fault_scenarios()
    scenarios.extend(eps_scenarios)

    # 添加推进系统场景（SF2）
    propulsion_scenarios = _propulsion_fault_scenarios()
    scenarios.extend(propulsion_scenarios)

    # 添加 ADCS 场景（SF3）
    adcs_scenarios = _adcs_fault_scenarios()
    scenarios.extend(adcs_scenarios)

    # 添加热控系统场景（SF4）
    thermal_scenarios = _thermal_fault_scenarios()
    scenarios.extend(thermal_scenarios)

    return scenarios


def run_all_subsystem_fault_scenarios() -> List:
    """
    运行所有分系统故障场景

    遍历所有定义的分系统故障场景并返回聚合结果列表。

    返回:
        List: 所有场景的聚合结果列表，包含各分系统故障类型

    示例:
        results = run_all_subsystem_fault_scenarios()
        for result in results:
            print(f"场景结果: {result}")
    """
    # 获取所有场景
    scenarios = _subsystem_fault_scenarios()

    # 运行所有场景并收集结果
    results = []
    for scenario in scenarios:
        # 根据场景类型调用对应的运行函数
        if isinstance(scenario, EPSFaultScenario):
            fault_type = run_eps_fault_scenario(scenario)
        elif isinstance(scenario, PropulsionFaultScenario):
            fault_type = run_propulsion_fault_scenario(scenario)
        elif isinstance(scenario, ADCSFaultScenario):
            fault_type = run_adcs_fault_scenario(scenario)
        elif isinstance(scenario, ThermalFaultScenario):
            fault_type = run_thermal_fault_scenario(scenario)
        else:
            # 未知场景类型，跳过
            continue

        results.append(fault_type)

    return results
