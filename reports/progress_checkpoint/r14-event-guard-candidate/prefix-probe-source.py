"""CPU-only decoder checks on protocol fixtures; no model answers executed."""
from dataclasses import replace
import copy
import json
from pathlib import Path
import sys
import time

import psutil

ROOT=Path(__file__).resolve().parents[2]
CANDIDATE=ROOT/'reports/local_requirements_completion/event-structured-r13-candidate'
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
    schema=taskspec_draft_response_schema('whole_spacecraft.unified_native.v1',constrain_events=True)
    source=ROOT/'reports/local_requirements_completion/r11-natural-loss-variations-v2/create17/model_call.json'
    archived=json.loads(json.loads(source.read_text(encoding='utf-8'))['raw_text'])
    malformed=archived['modifiers']['faults'][0]
    corrected=copy.deepcopy(archived)
    corrected['modifiers']['faults'][0]['fault_type']='eps_battery_capacity_loss'
    fixtures=[('archived_missing_discriminator',archived,False),
              ('protocol_fixture_explicit_discriminator',corrected,True)]
    wrong=copy.deepcopy(corrected);wrong['modifiers']['faults'][0]['fault_type']='invented_effect'
    fixtures.append(('unsupported_effect',wrong,False))
    cases=[];start=time.perf_counter()
    for name,payload,expected in fixtures:
        result=accepts(schema,json.dumps(payload,ensure_ascii=False,separators=(',',':')))
        assert result['accepted']==expected,(name,result,expected)
        cases.append({'case':name,'expected':expected,**result})
    report={'status':'PASS','scope':'CPU decoder protocol fixtures only; the archived model answer is never replaced or executed. No semantic model accuracy or production qualification.',
            'cases':cases,'elapsed_s':time.perf_counter()-start,'schema_bytes':len(json.dumps(schema)),
            'process':{'pid':proc.pid,'creation':proc.create_time(),'affinity':proc.cpu_affinity()}}
    (CANDIDATE/'event-prefix-probe.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    main()
