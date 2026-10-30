#!/usr/bin/env python3
"""用途：通过真实注册执行链核对组件默认工况的输出目录与运行字段。
参数：--output 指定审计 JSON；不修改合同、不生成教师标签、不使用物理协议桩。
输出：真实遥测、摘要字段与未在合同声明的字段；不证明全部工况或飞行有效性。
"""
import argparse
import fnmatch
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from sat_sim.capability_registry import active_capability_ids, get_capability  # noqa: E402
from sat_sim.form_schema import capability_form_schema  # noqa: E402
from sat_sim.task_models import canonicalize_task_spec  # noqa: E402
from sat_sim.unified_agent import normalize_form_task_spec  # noqa: E402
from sat_sim.task_compiler import compile_task_spec  # noqa: E402
from sat_sim.unified_execution import execute_compiled_task  # noqa: E402


def leaves(value,prefix=''):
    if isinstance(value,dict):
        for name,item in value.items():
            yield from leaves(item,f'{prefix}.{name}' if prefix else name)
    elif isinstance(value,list):
        for index,item in enumerate(value):
            yield from leaves(item,f'{prefix}.{index}')
    else:
        yield prefix,value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/local_requirements_completion/component-output-surfaces.json')
    args = parser.parse_args()
    report = {'schema_version':'sat-capability-output-surface-audit.v1','status':'RUNNING',
              'scope':'Actual registered component nominal default runs; not all modes or flight validation.', 'capabilities':[]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    for cid in active_capability_ids():
        contract = get_capability(cid)
        if contract.target_level!='component':
            continue
        row = {'capability_id':cid,'status':'NOT_EXECUTED'}
        try:
            form = capability_form_schema(cid)['default_form']
            spec = canonicalize_task_spec(normalize_form_task_spec(form))
            result = execute_compiled_task(compile_task_spec(spec),task_spec=spec,write_dataset=False,
                                           output_root=args.output.parent/'output-surface-runs'/cid)
            trace = {}
            for item in result.trace_rows:
                for name,value in leaves(item):
                    trace.setdefault(name,type(value).__name__)
            summary = {name:type(value).__name__ for name,value in leaves(result.summary)
                       if name.startswith('qoi.')}
            declarations = contract.operator_contract.observability
            numeric = [name for name,dtype in trace.items() if dtype in ('int','float') and name not in ('time_s','sample_index')]
            form_outputs = capability_form_schema(cid)['outputs']['trace_fields']
            row.update(status='EXECUTED',trace_fields=trace,summary_qoi_fields=summary,
                trace_row_count=len(result.trace_rows),
                declared_trace_fields=list(declarations.trace_fields),declared_summary_qoi=list(declarations.qoi),
                trace_not_declared=[name for name in trace if not any(fnmatch.fnmatchcase(name,p) for p in declarations.trace_fields)],
                numeric_trace_not_declared=[name for name in numeric if not any(fnmatch.fnmatchcase(name,p) for p in declarations.trace_fields)],
                numeric_trace_not_in_form=[name for name in numeric if name not in form_outputs],
                summary_qoi_not_declared=[name for name in summary if not any(
                    fnmatch.fnmatchcase(name,p) or fnmatch.fnmatchcase(name,'qoi.'+p) for p in declarations.qoi)],
                generic_summary_contract=any('*' in p for p in declarations.qoi))
        except Exception as exc:
            row.update(status='FAILED',error={'type':type(exc).__name__,'message':str(exc)})
        report['capabilities'].append(row)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'capability_id':cid,'status':row['status'],'trace_rows':row.get('trace_row_count')}),flush=True)
    report['execution_status'] = 'PASS' if report['capabilities'] and all(row['status']=='EXECUTED' for row in report['capabilities']) else 'FAIL'
    report['trace_contract_status'] = 'PASS' if report['execution_status']=='PASS' and all(not row['numeric_trace_not_declared'] for row in report['capabilities']) else 'FAIL'
    report['form_trace_status'] = 'PASS' if report['execution_status']=='PASS' and all(not row['numeric_trace_not_in_form'] for row in report['capabilities']) else 'FAIL'
    report['summary_contract_status'] = 'PASS' if report['execution_status']=='PASS' and all(not row['summary_qoi_not_declared'] for row in report['capabilities']) else 'FAIL'
    report['status'] = 'PASS' if all(report[key]=='PASS' for key in ('execution_status','trace_contract_status','form_trace_status','summary_contract_status')) else 'FAIL'
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
