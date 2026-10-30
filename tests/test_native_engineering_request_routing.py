"""Supported native engineering mechanisms must not imply flight qualification."""
import pytest
from sat_sim.capability_planner import plan_capability_for_request


@pytest.mark.parametrize("request_text", [
    "创建一个反作用轮，轮速初始值为100 r/min，在力矩作用下，提升到150 r/min",
    "创建一个反作用轮正常仿真，运行10秒，输出轮速。",
    "Simulate a reaction wheel accelerating from 100 rpm to 150 rpm",
])
def test_wheel_speed_only_request_keeps_component_scope(request_text):
    result = plan_capability_for_request(request_text)
    assert result.supported
    assert result.selected_capability_id == "component.reaction_wheel.v1"


@pytest.mark.parametrize("request_text", [
    "反作用轮 ADCS 姿态控制，输出指向误差和轮速",
    "ADCS unified runtime with reaction wheels and star tracker",
])
def test_wheel_in_attitude_control_keeps_subsystem_scope(request_text):
    result = plan_capability_for_request(request_text)
    assert result.supported
    assert result.selected_capability_id.startswith("subsystem.adcs")


@pytest.mark.parametrize('mechanism', ['SPICE ephemeris', 'spice_wmm', 'third-body forces', '第三体摄动', 'multi-node thermal network', '热网络'])
def test_explicit_whole_native_implemented_mechanisms_are_accepted(mechanism):
    result = plan_capability_for_request(f'whole_spacecraft.unified_native.v1 工程仿真20秒，使用{mechanism}')
    assert result.supported, result
    assert result.selected_capability_id == 'whole_spacecraft.unified_native.v1'


@pytest.mark.parametrize('claim', ['flight-grade orbit', 'calibrated force-model tolerances', 'nrlmsise-00', 'truth ephemeris', '高保真轨道', 'finite element thermal network'])
def test_realism_and_finite_element_claims_remain_rejected_with_spice(claim):
    result = plan_capability_for_request(f'whole_spacecraft.unified_native.v1 SPICE，要求 {claim}')
    assert not result.supported
    assert result.unsupported_requirements


def test_basic_orbit_route_does_not_inherit_native_spice_support():
    result = plan_capability_for_request('orbit_environment.medium_fidelity.v1 SPICE ephemeris')
    assert not result.supported
    assert any(item.domain == 'high_fidelity_orbit' for item in result.unsupported_requirements)
