"""CPU-only decoder checks on protocol fixtures; no model answers executed."""
from dataclasses import replace
import copy
import json
from pathlib import Path
import sys
import time

import psutil

ROOT=Path(__file__).resolve().parents[2]
CANDIDATE=ROOT/'reports/local_requirements_completion/event-intent-r18-candidate'
sys.path.insert(0,str(CANDIDATE/'src'))
from sat_sim.llm_backends import taskspec_draft_response_schema
from sat_sim_ml.guided_decoding import constrained_json_parser
from lmformatenforcer.characterlevelparser import UnionParser


def leaves(parser):
    if hasattr(parser,'delegate'):
        return leaves(parser.delegate)
    if isinstance(parser,UnionParser):
        return sum(leaves(child) for child in parser.parsers)
    return 1


def accepts(schema,text):
    parser=constrained_json_parser(schema,4096)
    # The real token enforcer supplies the tokenizer's Unicode alphabet.
    # This CPU character probe has no tokenizer; include its fixture alphabet.
    parser.config=replace(parser.config,alphabet=parser.config.alphabet+''.join(sorted(set(text))))
    peak=leaves(parser)
    for index,character in enumerate(text):
        if character not in parser.get_allowed_characters():
            return {'accepted':False,'rejected_at':index,'peak_branches':peak}
        parser=parser.add_character(character)
        peak=max(peak,leaves(parser))
        if peak>512:
            raise RuntimeError(f'EVENT_SCHEMA_BRANCH_BUDGET_EXCEEDED:{peak}')
    return {'accepted':parser.can_end(),'rejected_at':None,'peak_branches':peak}


def main():
    proc=psutil.Process();proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    allowed=[number for number in proc.cpu_affinity() if number>=12]
    if allowed:proc.cpu_affinity(allowed)
    schema=taskspec_draft_response_schema('whole_spacecraft.unified_native.v1',constrain_events=True,single_fault=True,single_fault_carrier='faults')
    source=ROOT/'reports/local_requirements_completion/r17-normal-form-variations/create17/model_call.json'
    archived=json.loads(json.loads(source.read_text(encoding='utf-8'))['raw_text'])
    valid=copy.deepcopy(archived);valid['faults'][0]['target']='whole_spacecraft'
    extra=copy.deepcopy(valid);extra['faults'].append({**extra['faults'][0],'fault_id':'extra'})
    split=copy.deepcopy(valid);split['modifiers']['faults']=[copy.deepcopy(split['faults'][0])]
    empty=copy.deepcopy(valid);empty['faults']=[]
    varied=copy.deepcopy(valid);varied['faults'][0].update(magnitude=.11,onset_time_s=2.)
    alias=copy.deepcopy(valid);alias['faults'][0]['severity']=.61
    from sat_sim.task_models import canonicalize_task_spec
    canonical=canonicalize_task_spec(valid)
    nonpreferred=copy.deepcopy(valid);nonpreferred['modifiers']['faults']=nonpreferred['faults'];nonpreferred['faults']=[]
    fixtures=[('archived_actual_r17_wrong_target',archived,False),
              ('protocol_fixture_public_target',valid,True),
              ('protocol_fixture_extra_fault',extra,False),
              ('protocol_fixture_split_carriers',split,False),
              ('protocol_fixture_missing_fault',empty,False),
              ('protocol_fixture_different_values',varied,True),
              ('protocol_fixture_canonical_wrapped',{'task_spec':canonical},True),
              ('protocol_fixture_conflicting_aliases',alias,False),
              ('protocol_fixture_nonpreferred_carrier',nonpreferred,False)]
    cases=[];start=time.perf_counter()
    for name,payload,expected in fixtures:
        result=accepts(schema,json.dumps(payload,ensure_ascii=False,separators=(',',':')))
        assert result['accepted']==expected,(name,result,expected)
        cases.append({'case':name,'expected':expected,**result})
    report={'status':'PASS','scope':'CPU decoder protocol fixtures only; the archived model answer is never replaced or executed. No semantic model accuracy or production qualification.',
            'cases':cases,'elapsed_s':time.perf_counter()-start,'schema_bytes':len(json.dumps(schema)),
            'process':{'pid':proc.pid,'creation':proc.create_time(),'affinity':proc.cpu_affinity()}}
    (CANDIDATE/'preferred-carrier-prefix-probe.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    main()
