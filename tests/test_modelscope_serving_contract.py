"""HTTP/control-plane tests only; this protocol fixture is not model evidence."""
from fastapi.testclient import TestClient
import pytest

from sat_sim_ml.serving import create_model_app
from sat_sim.api import create_app


class ProtocolEngine:
    model_name = 'protocol-fixture-not-real-model'
    provenance = {'engine':'protocol_fixture', 'adapter_loaded':False}
    text = '{"ok":true}'
    def generate(self, messages, *, max_tokens, response_schema=None):
        self.last_schema = response_schema
        return {'text':self.text, 'prompt_tokens':4,'completion_tokens':4,'elapsed_s':.1,'finish_reason':'stop'}


def test_modelscope_service_can_be_saved_without_relabeling_as_vllm(tmp_path):
    with TestClient(create_app(runs_root=tmp_path/'runs',artifacts_root=tmp_path/'artifacts',embedded_worker=False,auth_mode='disabled')) as client:
        response = client.post('/models/local-services', json={'service_type':'modelscope','model':'sat-lora'})
        assert response.status_code == 200
        assert response.json()['provider_id'] == 'local-modelscope'
        provider = next(item for item in client.get('/models/providers').json()['catalog']['providers']
                        if item['provider_id'] == 'local-modelscope')
        assert provider['backend'] == 'openai_compatible'
        assert provider['base_url'] == 'http://127.0.0.1:8031/v1'
        # The Web save route must use the same transport envelope and budget
        # as the actual production-generation evaluation, not another profile.
        assert provider['structured_output'] == 'json_schema'
        assert provider['timeout_s'] == 300.
        assert provider['max_output_tokens'] == 4096


def test_local_inference_returns_actual_completion_and_rejects_wrong_model():
    with TestClient(create_model_app(ProtocolEngine())) as client:
        assert client.get('/v1/models').json()['data'][0]['owned_by'] == 'local-modelscope'
        body = {'model':ProtocolEngine.model_name,'messages':[{'role':'user','content':'JSON only'}],
                'response_format':{'type':'json_object'}}
        response = client.post('/v1/chat/completions', json=body)
        assert response.status_code == 200
        assert response.json()['choices'][0]['message']['content'] == '{"ok":true}'
        assert response.json()['sat_sim']['engine'] == 'protocol_fixture'
        assert client.post('/v1/chat/completions',json={**body,'model':'not-loaded'}).status_code == 404


@pytest.mark.parametrize('text', ['not JSON','[]','{"ok":false}'])
def test_invalid_structured_model_output_is_never_replaced_with_template(text):
    engine = ProtocolEngine()
    engine.text = text
    with TestClient(create_model_app(engine)) as client:
        response = client.post('/v1/chat/completions',json={
            'model':engine.model_name,'messages':[{'role':'user','content':'test'}],
            'response_format':{'type':'json_schema','json_schema':{'schema':{
                'type':'object','properties':{'ok':{'const':True}},'required':['ok']}}}})
        assert response.status_code == 422
        assert response.json()['detail']['reason_code'] == 'MODEL_OUTPUT_OR_CONTEXT_REJECTED'
