"""Geometry is validated before compile for model, form and external drafts."""
import copy

import jsonschema
import pytest

from sat_sim.capability_agent import _request_allows_custom_rw_geometry
from sat_sim.form_schema import capability_form_schema
from sat_sim.unified_agent import normalize_form_task_spec
from sat_sim.task_models import to_runtime_task_spec
from sat_sim.task_validator import validate_task_spec
from sat_sim.llm_backends import taskspec_draft_response_schema

CID = 'whole_spacecraft.unified_native.v1'


def task(values):
    spec = normalize_form_task_spec(capability_form_schema(CID)['default_form'])
    spec['parameters']['values'].update(values)
    return spec


@pytest.mark.parametrize('values', [
    {'rw_configuration':'orthogonal_3', 'rw_mount_b_x_m_3':0.},
    {'rw_geometry_mode':'legacy_axis_offset', 'rw_mount_b_x_m_0':0.},
    {'rw_geometry_mode':'per_wheel', 'rw_axis_b_x_0':1.},
    {'rw_geometry_mode':'per_wheel', 'rw_axis_b_x_0':0.,'rw_axis_b_y_0':0.,'rw_axis_b_z_0':0.},
    {'rw_max_torque_nm_3':.05},
    {'rw_motor_current_limit_a_3':1.},
    {'rw_max_torque_nm_0':0.},
    {'rw_friction_mode':'disabled', 'rw_coulomb_friction_nm_0':.001},
])
def test_inconsistent_native_geometry_is_rejected_before_runtime(values):
    spec = task(values)
    before = copy.deepcopy(spec)
    result = validate_task_spec(spec)
    assert 'NATIVE_RW_GEOMETRY_INVALID' in {i.code for i in result.errors}
    assert spec == before


@pytest.mark.parametrize('configuration', ['orthogonal_3','pyramid_4'])
def test_registered_standard_wheel_configurations_still_validate(configuration):
    result = validate_task_spec(task({'rw_configuration':configuration}))
    assert result.ok, result.to_dict()


def test_explicit_consistent_custom_geometry_remains_available():
    spec = task({'rw_configuration':'orthogonal_3','rw_geometry_mode':'per_wheel',
                 'rw_axis_b_x_0':1.,'rw_axis_b_y_0':0.,'rw_axis_b_z_0':0.,
                 'rw_mount_b_x_m_0':.1})
    assert validate_task_spec(spec).ok
    assert _request_allows_custom_rw_geometry('自定义反作用轮安装位置')
    assert _request_allows_custom_rw_geometry('rw_axis_b_x_0=1')
    assert not _request_allows_custom_rw_geometry('创建近地卫星热环境脚本，无故障、退化或约束事件')
    assert _request_allows_custom_rw_geometry('已有任务。本轮补充指令：仅增加温度曲线')


def test_explicit_per_wheel_driver_edits_remain_available():
    from sat_sim.capability_agent import _request_allows_per_wheel_overrides
    assert validate_task_spec(task({'rw_configuration':'pyramid_4','rw_max_torque_nm_3':.05})).ok
    for request in ['rw_max_torque_nm_3=.05', 'per-wheel torque limits', '第二个轮独立驱动', '轮2的最大力矩', '已有任务。本轮补充指令：增加温度曲线']:
        assert _request_allows_per_wheel_overrides(request), request
    assert not _request_allows_per_wheel_overrides('创建整星热环境脚本')


def test_no_unrequested_indexed_driver_parameters_in_a_new_task_schema():
    schema = taskspec_draft_response_schema(CID,allow_geometry_overrides=False,allow_per_wheel_overrides=False)
    parameters = schema['anyOf'][0]['properties']['parameters']['properties']
    assert 'rw_motor_current_limit_a' in parameters
    assert 'rw_max_torque_nm_3' not in parameters
    assert 'rw_configuration' in parameters


def test_new_task_schema_omits_unrequested_overrides_without_editing_an_answer():
    draft = to_runtime_task_spec(task({}))
    # Remove explicitly represented default geometry knobs from this test draft.
    draft['parameters'] = {k:v for k,v in draft['parameters'].items()
                           if not k.startswith(('rw_axis_b_', 'rw_mount_b_')) and k not in {'rw_geometry_mode','rw_mount_radius_m'}}
    schema = taskspec_draft_response_schema(CID, allow_geometry_overrides=False)
    jsonschema.validate(draft,schema)
    draft['parameters']['rw_mount_b_x_m_3'] = 0.
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(draft,schema)
    jsonschema.validate(draft,taskspec_draft_response_schema(CID,allow_geometry_overrides=True))
