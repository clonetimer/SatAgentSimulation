"""Persisted data must support selected curves and independently requested streams.

These are serializer contract tests, not model or physical simulation evidence.
"""
import copy
import csv
from dataclasses import replace
import json

import pytest

from sat_sim.dataset_writer import write_task_dataset
from sat_sim.scenario_templates import instantiate_scenario_template
from sat_sim.task_compiler import compile_task_spec


@pytest.fixture
def task():
    spec=instantiate_scenario_template('generic_leo_small_sat_engineering')
    return spec,compile_task_spec(spec)


def write(tmp_path,task,rows,*,fields,plots=(),fmt='csv',streams=()):
    spec,compiled=task
    outputs={'trace_format':fmt,'record_fields':fields,'plots':list(plots),
             'include_summary':False,'include_labels':False,'telemetry_streams':list(streams)}
    return write_task_dataset(output_root=tmp_path/'data',
        compiled=replace(compiled,outputs=outputs,simulation={**compiled.simulation,'sample_s':1.}),
        task_spec=spec,summary={},trace_rows=rows)


def read(result,fmt='csv'):
    path=result.output_root/result.files['trace']
    if fmt=='jsonl':
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    with path.open(encoding='utf-8',newline='') as stream:
        return list(csv.DictReader(stream))


@pytest.mark.parametrize('fmt',['csv','jsonl'])
def test_exported_curves_keep_their_values_and_time_coordinate(tmp_path,task,fmt):
    rows=[{'time_s':0.,'eps.battery_soc':.62,'thermal.payload_earth_ir_heat_w':3.2,
           'orbit.radius_m':7e6,'unselected_diagnostic':12.},
          {'time_s':1.,'eps.battery_soc':.61,'thermal.payload_earth_ir_heat_w':3.3,
           'orbit.radius_m':7e6+1.,'unselected_diagnostic':13.}]
    before=copy.deepcopy(rows)
    result=write(tmp_path,task,iter(rows),fields=['qoi.*','eps.battery_soc'],
        plots=['thermal.payload_earth_ir_heat_w','orbit.radius_m'],fmt=fmt)
    output=read(result,fmt)
    expected={'time_s','eps.battery_soc','thermal.payload_earth_ir_heat_w','orbit.radius_m'}
    assert set(output[0])==expected
    assert len(output)==len(rows)
    assert all(float(saved[key])==source[key] for saved,source in zip(output,rows) for key in expected)
    assert {field['name'] for field in result.manifest['trace_schema']}==expected
    assert rows==before


def test_recorder_wildcards_expand_to_observed_wheel_fields(tmp_path,task):
    row={'time_s':0.,'adcs.rw.speed_rad_s_0':1.,'adcs.rw.speed_rad_s_1':2.,
         'adcs.rw.speed_rad_s_2':3.,'adcs.pointing_error_deg':.1}
    output=read(write(tmp_path,task,[row],fields=['adcs.rw.speed_rad_s_*']))
    assert set(output[0])==set(row)-{'adcs.pointing_error_deg'}
    assert float(output[0]['adcs.rw.speed_rad_s_2'])==3.


@pytest.mark.parametrize('fields',[None,[]])
def test_absent_recorder_filter_preserves_the_existing_full_trace_contract(tmp_path,task,fields):
    row={'time_s':0.,'selected':1.,'other':2.}
    output=read(write(tmp_path,task,[row],fields=fields,plots=['selected']))
    assert set(output[0])==set(row)


def test_missing_observable_is_not_fabricated(tmp_path,task):
    output=read(write(tmp_path,task,[{'time_s':0.,'actual':1.}],fields=['missing'],plots=['missing.*']))
    assert output==[{'time_s':'0.0'}]


def test_stream_uses_original_samples_independently_of_main_trace_filter(tmp_path,task):
    rows=[{'time_s':float(i),'eps.battery_soc':.62-i*.001,
           'adcs.pointing_error_deg':8.-i} for i in range(5)]
    result=write(tmp_path,task,rows,fields=['eps.battery_soc'],streams=[{
        'stream_id':'attitude','sample_s':2.,'fields':['adcs.pointing_error_deg'],'format':'csv'}])
    with (result.output_root/result.files['telemetry_stream:attitude']).open(encoding='utf-8',newline='') as stream:
        streamed=list(csv.DictReader(stream))
    assert {'time_s','adcs.pointing_error_deg'}<=set(streamed[0])
    assert [float(row['time_s']) for row in streamed]==[0.,2.,4.]
    assert [float(row['adcs.pointing_error_deg']) for row in streamed]==[8.,6.,4.]
    assert set(read(result)[0])=={'time_s','eps.battery_soc'}
