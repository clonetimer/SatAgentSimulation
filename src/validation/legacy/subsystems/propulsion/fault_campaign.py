"""
推进系统分系统故障场景测试脚本

定义并运行推进系统分系统的故障测试场景。
"""

from dataclasses import dataclass
from typing import List
from components.fault_spec import FaultSpec
from components.thruster.faults import ThrusterFaultType
from subsystems.propulsion.faults import PropulsionFaultType, aggregate_propulsion_faults


@dataclass
class PropulsionFaultScenario:
    """
    推进系统分系统故障场景数据类

    用于定义推进系统分系统的故障测试场景。

    属性:
        scenario_id: 场景唯一标识符
        fault_specs: 部件故障规范列表
        expected_fault_type: 预期的分系统故障类型
    """
    scenario_id: str
    fault_specs: List[FaultSpec]
    expected_fault_type: PropulsionFaultType


def _propulsion_fault_scenarios() -> List[PropulsionFaultScenario]:
    """
    定义推进系统分系统故障场景列表

    返回推进系统分系统的测试场景，包括 SF2 场景（推进系统部分失效）。

    返回:
        List[PropulsionFaultScenario]: 推进系统故障场景列表

    场景定义:
        SF2: 推进器集群2个失效
            - 推进器1点火失败（ThrusterFaultType.IgnitionFailure）
            - 推进器2点火失败（ThrusterFaultType.IgnitionFailure）
            - 预期聚合结果：PropulsionFaultType.ThrusterClusterFailure
    """
    scenarios = []

    # SF2: 推进器集群故障（2个推进器点火失败）
    sf2_fault_specs = [
        FaultSpec(
            fault_type=ThrusterFaultType.IgnitionFailure,
            onset_time_s=0.0,
            duration_s=-1.0,
            magnitude=2.0,  # 表示2个推进器失效
            target_id="thruster_1"
        ),
        FaultSpec(
            fault_type=ThrusterFaultType.IgnitionFailure,
            onset_time_s=0.0,
            duration_s=-1.0,
            magnitude=1.0,
            target_id="thruster_2"
        )
    ]

    sf2_scenario = PropulsionFaultScenario(
        scenario_id="SF2",
        fault_specs=sf2_fault_specs,
        expected_fault_type=PropulsionFaultType.ThrusterClusterFailure
    )

    scenarios.append(sf2_scenario)

    return scenarios


def run_propulsion_fault_scenario(scenario: PropulsionFaultScenario) -> PropulsionFaultType:
    """
    运行单个推进系统故障场景

    根据场景定义的故障规范，聚合并返回分系统故障类型。

    参数:
        scenario: 推进系统故障场景

    返回:
        PropulsionFaultType: 聚合后的分系统故障类型

    示例:
        scenario = _propulsion_fault_scenarios()[0]
        fault_type = run_propulsion_fault_scenario(scenario)
        print(f"场景 {scenario.scenario_id} 聚合结果: {fault_type}")
    """
    # 聚合部件故障为分系统故障
    aggregated_fault_type = aggregate_propulsion_faults(scenario.fault_specs)

    return aggregated_fault_type


def run_all_propulsion_fault_scenarios() -> List[PropulsionFaultType]:
    """
    运行所有推进系统故障场景

    遍历所有定义的推进系统故障场景并返回聚合结果列表。

    返回:
        List[PropulsionFaultType]: 所有场景的聚合结果列表

    示例:
        results = run_all_propulsion_fault_scenarios()
        for i, fault_type in enumerate(results):
            print(f"场景 {i+1} 结果: {fault_type}")
    """
    # 获取所有场景
    scenarios = _propulsion_fault_scenarios()

    # 运行所有场景并收集结果
    results = []
    for scenario in scenarios:
        fault_type = run_propulsion_fault_scenario(scenario)
        results.append(fault_type)

    return results
