"""Nearest thermal sampling preserves the original stable minimum semantics."""
import random
from types import SimpleNamespace

from sat_sim.bsk_engine.unified_native import _overlay_exact_thermal_trace


def test_binary_lookup_preserves_exact_ties_duplicates_and_tolerance():
    times = [2., 0., 2., 4.]
    trace = [SimpleNamespace(node_name='battery', time_s=t, temp_k=280.+i,
                             thermal_safe=i!=1) for i,t in enumerate(times)]
    ordered = sorted(trace,key=lambda item:item.time_s)
    rows = [{'time_s':t} for t in (-10.,0.,1.,2.,3.,4.,20.)]
    _overlay_exact_thermal_trace(rows,SimpleNamespace(trace=trace),tolerance_s=1.)
    for row in rows:
        wanted = min(ordered,key=lambda item:abs(item.time_s-row['time_s']))
        if abs(wanted.time_s-row['time_s']) > 1.:
            assert 'thermal.battery_temp_k' not in row
        else:
            assert row['thermal.battery_temp_k']==wanted.temp_k
            assert row['thermal.battery_safe']==int(wanted.thermal_safe)


def test_binary_lookup_matches_full_scan_over_irregular_multi_node_histories():
    rng = random.Random(71)
    trace = [SimpleNamespace(node_name=node, time_s=rng.randrange(100)/3.,
                             temp_k=270.+i/10., thermal_safe=i%7!=0)
             for node in ('battery','payload') for i in range(100)]
    rows = [{'time_s':rng.random()*40.} for _ in range(200)]
    _overlay_exact_thermal_trace(rows,SimpleNamespace(trace=trace),tolerance_s=2.)
    for node in ('battery','payload'):
        ordered = sorted((item for item in trace if item.node_name==node),key=lambda item:item.time_s)
        for row in rows:
            wanted = min(ordered,key=lambda item:abs(item.time_s-row['time_s']))
            if abs(wanted.time_s-row['time_s']) <= 2.:
                assert row[f'thermal.{node}_temp_k']==wanted.temp_k
                assert row[f'thermal.{node}_safe']==int(wanted.thermal_safe)
            else:
                assert f'thermal.{node}_temp_k' not in row
