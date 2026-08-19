"""
故障场景测试脚本模块

定义和运行各种故障场景测试，验证卫星系统在不同故障条件下的行为和性能。
"""

from dataclasses import dataclass
from typing import Any

from subsystems.fault_base import FaultSpec
from subsystems.adcs import faults as adcs_faults
from subsystems.propulsion import faults as propulsion_faults
from validation.legacy.whole_spacecraft.selective_unified_assembly import (
    run_selective_unified_assembly,
    SelectiveUnifiedAssemblySummary,
    SelectiveUnifiedAssemblyTraceRow,
)


@dataclass(frozen=True)
class FaultCampaignScenario:
    """
    故障场景数据类

    用于描述一个完整的故障测试场景，包括场景ID、名称、故障规格列表和预期性能损失。

    属性:
        scenario_id: 场景唯一标识符，如 "F1", "F2" 等
        scenario_name: 场景名称，简短描述故障类型
        fault_specs: 故障规格元组，定义需要在测试中注入的故障列表
        expected_delta_v_loss_pct: 预期 delta-V 损失百分比（推进器故障影响）
        expected_attitude_error_increase_pct: 预期姿态误差增加百分比（传感器故障影响）

    示例:
        # 创建推进器点火失败场景
        scenario = FaultCampaignScenario(
            scenario_id="F1",
            scenario_name="推进器点火失败",
            fault_specs=(
                FaultSpec(
                    fault_type=propulsion_faults.build_propulsion_direct_fault_specs("thruster_ignition_failure")[0].fault_type,
                    onset_time_s=50.0,
                    duration_s=-1.0,
                    magnitude=1.0,
                    target_id="thruster_1"
                ),
            ),
            expected_delta_v_loss_pct=50.0,
            expected_attitude_error_increase_pct=0.0
        )
    """
    scenario_id: str
    scenario_name: str
    fault_specs: tuple[FaultSpec, ...]
    expected_delta_v_loss_pct: float
    expected_attitude_error_increase_pct: float


@dataclass(frozen=True)
class FaultScenarioResult:
    """
    故障场景测试结果数据类

    用于记录单个故障场景测试的结果，包括场景信息和仿真摘要。

    属性:
        scenario_id: 场景唯一标识符
        scenario_name: 场景名称
        summary: 仿真摘要对象
        expected_delta_v_loss_pct: 预期 delta-V 损失百分比
        actual_delta_v_loss_pct: 实际 delta-V 损失百分比（从仿真数据计算）
        expected_attitude_error_increase_pct: 预期姿态误差增加百分比
        actual_attitude_error_increase_pct: 实际姿态误差增加百分比
        fault_injection_status: 故障注入状态
        test_passed: 测试是否通过（实际损失是否在预期范围内）
    """
    scenario_id: str
    scenario_name: str
    summary: SelectiveUnifiedAssemblySummary
    expected_delta_v_loss_pct: float
    actual_delta_v_loss_pct: float
    expected_attitude_error_increase_pct: float
    actual_attitude_error_increase_pct: float
    fault_injection_status: str | None
    test_passed: bool


def _fault_scenarios() -> tuple[FaultCampaignScenario, ...]:
    """
    获取故障场景列表

    返回预定义的故障测试场景列表，包括：
        F1: 推进器在50秒点火失败
        F2: 传感器在30秒偏差
        F3: 通信在60秒中断
        F4: 组合故障（推进器+传感器同时故障）

    返回:
        tuple[FaultCampaignScenario, ...]: 故障场景元组
    """
    # F1: 推进器点火失败场景
    # 推进器在50秒时发生点火失败，无法产生推力
    # 预期影响：delta-V 损失 50%（假设推进任务在故障前已完成部分）
    f1 = FaultCampaignScenario(
        scenario_id="F1",
        scenario_name="推进器点火失败",
        fault_specs=(
            FaultSpec(
                fault_type=propulsion_faults.build_propulsion_direct_fault_specs("thruster_ignition_failure")[0].fault_type,
                onset_time_s=50.0,
                duration_s=-1.0,  # 永久故障
                magnitude=1.0,  # 完全点火失败
                target_id="unifiedPropulsionThrusterDynamicEffector"
            ),
        ),
        expected_delta_v_loss_pct=50.0,
        expected_attitude_error_increase_pct=0.0
    )

    # F2: 传感器偏置漂移场景
    # IMU 在30秒时发生偏置漂移，影响姿态测量精度
    # 预期影响：姿态误差增加（具体数值需要根据仿真结果确定）
    f2 = FaultCampaignScenario(
        scenario_id="F2",
        scenario_name="传感器偏置漂移",
        fault_specs=(
            FaultSpec(
                fault_type=adcs_faults.ADCSFaultType.SENSOR_FAILURE,
                onset_time_s=30.0,
                duration_s=-1.0,  # 永久故障
                magnitude=0.05,  # 偏置值 0.05 rad/s
                target_id="unifiedImu"
            ),
        ),
        expected_delta_v_loss_pct=0.0,
        expected_attitude_error_increase_pct=20.0  # 预期姿态误差增加 20%
    )

    # F3: 通信链路中断场景
    # 通信系统在60秒时发生链路丢失，无法传输数据
    # 预期影响：主要影响数据传输，对 delta-V 和姿态控制影响较小
    f3 = FaultCampaignScenario(
        scenario_id="F3",
        scenario_name="通信链路中断",
        fault_specs=(
            FaultSpec(
                fault_type="link_loss",
                onset_time_s=60.0,
                duration_s=-1.0,  # 永久故障
                magnitude=1.0,  # 完全链路丢失
                target_id="unifiedTransmitter"
            ),
        ),
        expected_delta_v_loss_pct=0.0,
        expected_attitude_error_increase_pct=0.0
    )

    # F4: 组合故障场景
    # 推进器和传感器同时发生故障，测试系统的综合故障响应能力
    # 预期影响：delta-V 损失 + 姿态误差增加
    f4 = FaultCampaignScenario(
        scenario_id="F4",
        scenario_name="组合故障（推进器+传感器）",
        fault_specs=(
            # 推进器点火失败
            FaultSpec(
                fault_type=propulsion_faults.build_propulsion_direct_fault_specs("thruster_ignition_failure")[0].fault_type,
                onset_time_s=40.0,
                duration_s=-1.0,
                magnitude=1.0,
                target_id="unifiedPropulsionThrusterDynamicEffector"
            ),
            # IMU 偏置漂移
            FaultSpec(
                fault_type=adcs_faults.ADCSFaultType.SENSOR_FAILURE,
                onset_time_s=40.0,  # 同时发生
                duration_s=-1.0,
                magnitude=0.03,
                target_id="unifiedImu"
            ),
        ),
        expected_delta_v_loss_pct=50.0,
        expected_attitude_error_increase_pct=15.0
    )

    return (f1, f2, f3, f4)


def run_fault_scenario(
    scenario: FaultCampaignScenario,
    verbose: bool = False
) -> FaultScenarioResult:
    """
    运行单个故障场景测试

    执行以下步骤：
        1. 创建 FaultSpec 列表（从场景中获取）
        2. 调用 run_selective_unified_assembly(fault_specs=...)
        3. 运行仿真
        4. 收集故障响应结果
        5. 计算实际性能损失并与预期值比较

    参数:
        scenario: 故障场景对象
        verbose: 是否打印详细日志信息

    返回:
        FaultScenarioResult: 故障场景测试结果

    示例:
        >>> scenarios = _fault_scenarios()
        >>> result = run_fault_scenario(scenarios[0])
        >>> print(f"场景 {result.scenario_id}: {result.test_passed}")
    """
    if verbose:
        print(f"\n[FaultCampaign] 开始运行场景 {scenario.scenario_id}: {scenario.scenario_name}")
        print(f"  故障规格数量: {len(scenario.fault_specs)}")
        for i, spec in enumerate(scenario.fault_specs):
            print(f"  故障 {i+1}: {spec.fault_type} 在 {spec.onset_time_s}s, "
                  f"持续时间 {spec.duration_s}s, 目标 {spec.target_id}")

    # 创建故障规格列表
    fault_specs = scenario.fault_specs

    # 调用统一装配函数，传递故障规格
    summary, rows = run_selective_unified_assembly(fault_specs=fault_specs)

    if verbose:
        print(f"  仿真完成，状态: {summary.status}")
        print(f"  故障注入状态: {summary.fault_injection_status}")

    # 计算实际性能损失
    # 从仿真轨迹数据中提取相关信息
    actual_delta_v_loss_pct = 0.0
    actual_attitude_error_increase_pct = 0.0

    if len(rows) > 1:
        # 提取初始和最终的速度和姿态误差数据
        initial_row = rows[0]
        final_row = rows[-1]

        # 计算 delta-V 损失
        # 假设正常运行会达到某个预期的速度增量，故障后实际增量减少
        # 这里使用速度变化量作为 delta-V 的近似
        # 注意：需要参考基准场景来准确计算损失百分比
        # 当前实现：使用速度变化量占初始燃料推进能力的比例作为近似
        if initial_row.fuel_mass_kg > 0 and summary.initial_fuel_mass_kg > 0:
            # 如果燃料消耗异常，可以推断推进系统故障
            expected_fuel_consumption = summary.initial_fuel_mass_kg * 0.1  # 假设正常消耗 10%
            actual_fuel_consumption = initial_row.fuel_mass_kg - final_row.fuel_mass_kg
            if expected_fuel_consumption > 0:
                # 燃料消耗减少 => delta-V 损失
                fuel_consumption_ratio = actual_fuel_consumption / expected_fuel_consumption
                actual_delta_v_loss_pct = max(0.0, (1.0 - fuel_consumption_ratio) * 100.0)

        # 计算姿态误差增加
        # 比较初始和最终的姿态误差
        if initial_row.attitude_error_norm > 0:
            # 正常情况下姿态误差应收敛或保持稳定
            # 故障可能导致姿态误差增加
            attitude_error_ratio = final_row.attitude_error_norm / initial_row.attitude_error_norm
            # 预期姿态误差应该收敛（ratio < 1），故障可能导致 ratio > 1
            expected_attitude_error_ratio = 0.5  # 假设正常收敛到 50%
            actual_attitude_error_increase_pct = max(0.0, (attitude_error_ratio - expected_attitude_error_ratio) * 100.0)
        else:
            # 初始姿态误差为0，使用最终姿态误差的绝对值
            actual_attitude_error_increase_pct = final_row.attitude_error_norm * 100.0

    # 判断测试是否通过
    # 实际损失是否在预期范围内（允许一定的误差容忍度）
    tolerance_pct = 10.0  # 误差容忍度 10%

    delta_v_passed = abs(actual_delta_v_loss_pct - scenario.expected_delta_v_loss_pct) <= tolerance_pct
    attitude_passed = abs(actual_attitude_error_increase_pct - scenario.expected_attitude_error_increase_pct) <= tolerance_pct
    test_passed = delta_v_passed and attitude_passed

    if verbose:
        print(f"  预期 delta-V 损失: {scenario.expected_delta_v_loss_pct:.1f}%")
        print(f"  实际 delta-V 损失: {actual_delta_v_loss_pct:.1f}%")
        print(f"  delta-V 测试结果: {'PASS' if delta_v_passed else 'FAIL'}")
        print(f"  预期姿态误差增加: {scenario.expected_attitude_error_increase_pct:.1f}%")
        print(f"  实际姿态误差增加: {actual_attitude_error_increase_pct:.1f}%")
        print(f"  姿态误差测试结果: {'PASS' if attitude_passed else 'FAIL'}")
        print(f"  场景测试总结果: {'PASS' if test_passed else 'FAIL'}")

    return FaultScenarioResult(
        scenario_id=scenario.scenario_id,
        scenario_name=scenario.scenario_name,
        summary=summary,
        expected_delta_v_loss_pct=scenario.expected_delta_v_loss_pct,
        actual_delta_v_loss_pct=actual_delta_v_loss_pct,
        expected_attitude_error_increase_pct=scenario.expected_attitude_error_increase_pct,
        actual_attitude_error_increase_pct=actual_attitude_error_increase_pct,
        fault_injection_status=summary.fault_injection_status,
        test_passed=test_passed
    )


def run_all_fault_scenarios(
    verbose: bool = False
) -> tuple[FaultScenarioResult, ...]:
    """
    运行所有故障场景测试

    遍历所有预定义的故障场景（F1-F4），逐一运行测试并收集结果摘要。

    参数:
        verbose: 是否打印详细日志信息

    返回:
        tuple[FaultScenarioResult, ...]: 所有故障场景测试结果元组

    示例:
        >>> results = run_all_fault_scenarios(verbose=True)
        >>> passed_count = sum(1 for r in results if r.test_passed)
        >>> print(f"通过场景数: {passed_count}/{len(results)}")
    """
    scenarios = _fault_scenarios()
    results: list[FaultScenarioResult] = []

    if verbose:
        print(f"\n[FaultCampaign] 开始运行所有故障场景测试")
        print(f"  总场景数: {len(scenarios)}")

    for scenario in scenarios:
        result = run_fault_scenario(scenario, verbose=verbose)
        results.append(result)

    if verbose:
        print(f"\n[FaultCampaign] 所有故障场景测试完成")
        passed_count = sum(1 for r in results if r.test_passed)
        print(f"  通过场景数: {passed_count}/{len(results)}")
        for result in results:
            status = "✓ PASS" if result.test_passed else "✗ FAIL"
            print(f"  {result.scenario_id} ({result.scenario_name}): {status}")
            if not result.test_passed:
                print(f"    delta-V: 预期={result.expected_delta_v_loss_pct:.1f}%, "
                      f"实际={result.actual_delta_v_loss_pct:.1f}%")
                print(f"    姿态误差: 预期={result.expected_attitude_error_increase_pct:.1f}%, "
                      f"实际={result.actual_attitude_error_increase_pct:.1f}%")

    return tuple(results)


def generate_fault_campaign_report(
    results: tuple[FaultScenarioResult, ...],
    output_format: str = "text"
) -> str:
    """
    生成故障场景测试报告

    根据测试结果生成汇总报告，支持文本和 JSON 格式。

    参数:
        results: 故障场景测试结果元组
        output_format: 输出格式，可选 "text" 或 "json"

    返回:
        str: 格式化的测试报告

    示例:
        >>> results = run_all_fault_scenarios()
        >>> report = generate_fault_campaign_report(results)
        >>> print(report)
    """
    passed_count = sum(1 for r in results if r.test_passed)
    total_count = len(results)

    if output_format == "json":
        import json
        from dataclasses import asdict

        report_data = {
            "summary": {
                "total_scenarios": total_count,
                "passed_scenarios": passed_count,
                "failed_scenarios": total_count - passed_count,
                "pass_rate_pct": (passed_count / total_count * 100.0) if total_count > 0 else 0.0
            },
            "scenarios": [asdict(r) for r in results]
        }
        return json.dumps(report_data, indent=2, ensure_ascii=False)

    else:  # text format
        report_lines = [
            "=" * 60,
            "故障场景测试报告",
            "=" * 60,
            "",
            "总体摘要:",
            f"  总场景数: {total_count}",
            f"  通过场景数: {passed_count}",
            f"  失败场景数: {total_count - passed_count}",
            f"  通过率: {(passed_count / total_count * 100.0) if total_count > 0 else 0.0:.1f}%",
            "",
            "详细结果:",
        ]

        for result in results:
            status = "✓ 通过" if result.test_passed else "✗ 失败"
            report_lines.extend([
                "",
                f"场景 {result.scenario_id}: {result.scenario_name}",
                f"  状态: {status}",
                f"  故障注入: {result.fault_injection_status or '未注入'}",
                f"  delta-V 损失:",
                f"    预期: {result.expected_delta_v_loss_pct:.1f}%",
                f"    实际: {result.actual_delta_v_loss_pct:.1f}%",
                f"  姿态误差增加:",
                f"    预期: {result.expected_attitude_error_increase_pct:.1f}%",
                f"    实际: {result.actual_attitude_error_increase_pct:.1f}%",
            ])

        report_lines.extend([
            "",
            "=" * 60,
            "报告结束",
            "=" * 60,
        ])

        return "\n".join(report_lines)


if __name__ == "__main__":
    # 示例：运行所有故障场景并生成报告
    results = run_all_fault_scenarios(verbose=True)
    report = generate_fault_campaign_report(results)
    print("\n" + report)
