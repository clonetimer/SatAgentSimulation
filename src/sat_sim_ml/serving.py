"""Local, non-streaming OpenAI-compatible inference over real model weights."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
import time
from uuid import uuid4

from .data import sha256, verify_base_model


class LocalModelEngine:
    def __init__(self, base_manifest: Path, adapter: Path | None = None, *, model_name=None, max_context=16384, attention_backend='cudnn'):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        snapshot, source = verify_base_model(base_manifest)
        if attention_backend not in ('auto','cudnn'):
            raise ValueError('UNSUPPORTED_ATTENTION_BACKEND')
        if attention_backend == 'cudnn':
            from .attention import register_cudnn_prefill_attention
            implementation = register_cudnn_prefill_attention()
        else:
            implementation = 'sdpa'
        if not torch.cuda.is_available():
            raise RuntimeError('LOCAL_MODEL_CUDA_REQUIRED')
        torch.set_num_threads(4)
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(snapshot, trust_remote_code=False, local_files_only=True)
        self.model = AutoModelForCausalLM.from_pretrained(snapshot, use_safetensors=True,
            local_files_only=True, trust_remote_code=False, attn_implementation=implementation,
            torch_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16).to('cuda')
        self.provenance = {'engine':'transformers', 'base_model':source['model_id'], 'attention_backend':implementation,
            'base_content_sha256':source['snapshot_content_sha256'], 'adapter_loaded':False}
        if adapter is not None:
            from peft import PeftModel
            manifest = adapter.parent/'training_provenance.json'
            training = json.loads(manifest.read_text(encoding='utf-8'))
            if training['state'] != 'TRAINED_NOT_TASKSPEC_ACCEPTED':
                raise ValueError('PRODUCTION_SERVING_REQUIRES_COMPLETED_NON_SMOKE_TRAINING')
            if training['base_content_sha256'] != source['snapshot_content_sha256']:
                raise ValueError('LORA_BASE_MODEL_MISMATCH')
            if not training.get('adapter_files'):
                raise ValueError('LORA_ADAPTER_HASH_EVIDENCE_MISSING')
            for item in training['adapter_files']:
                path = (adapter/item['path']).resolve()
                if not path.is_relative_to(adapter.resolve()) or sha256(path) != item['sha256']:
                    raise ValueError('LORA_ADAPTER_HASH_MISMATCH')
            self.model = PeftModel.from_pretrained(self.model, adapter, local_files_only=True)
            # Frozen inference can fuse the verified LoRA delta once, avoiding
            # hundreds of tiny adapter kernels for each decoded token on Windows.
            self.model = self.model.merge_and_unload(safe_merge=True)
            self.provenance.update(adapter_loaded=True, adapter_merged=True, training_provenance_sha256=sha256(manifest),
                                   dataset_manifest_sha256=training['dataset_manifest_sha256'])
        self.model.eval()
        self.model.config.use_cache = True
        self.model_name = model_name or (source['model_id']+'-sat-lora' if adapter else source['model_id'])
        self.max_context = max_context
        self.attention_backend = attention_backend
        self._lock = threading.Lock()

    def generate(self, messages, *, max_tokens=2048, response_schema=None):
        with self._lock, self.torch.inference_mode():
            encoded = self.tokenizer.apply_chat_template(messages, tokenize=True,
                add_generation_prompt=True, enable_thinking=False, return_tensors='pt').to('cuda')
            if encoded.shape[-1]+max_tokens > self.max_context:
                raise ValueError('MODEL_CONTEXT_BUDGET_EXCEEDED')
            started = time.perf_counter()
            options = {}
            if response_schema is not None:
                from .guided_decoding import prefix_constraint
                options['prefix_allowed_tokens_fn'] = prefix_constraint(self.tokenizer,response_schema,max_tokens)
            generated = self.model.generate(encoded, attention_mask=self.torch.ones_like(encoded),
                max_new_tokens=max_tokens, do_sample=False,
                pad_token_id=self.tokenizer.eos_token_id, eos_token_id=self.tokenizer.eos_token_id, **options)
            completion = generated[0,encoded.shape[-1]:]
            text = self.tokenizer.decode(completion, skip_special_tokens=True).strip()
            from .guided_decoding import decoding_evidence
            return {'text':text, 'structured_generation':decoding_evidence(response_schema),
                'prompt_tokens':int(encoded.shape[-1]),
                'completion_tokens':int(completion.shape[-1]), 'elapsed_s':time.perf_counter()-started,
                'finish_reason':'stop' if int(completion[-1]) == self.tokenizer.eos_token_id else 'length'}


def create_model_app(engine):
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel, ConfigDict, Field
    from typing import Literal
    from jsonschema.exceptions import SchemaError, ValidationError as JsonSchemaError

    class Message(BaseModel):
        model_config = ConfigDict(extra='forbid')
        role: Literal['system','user','assistant']
        content: str = Field(max_length=200000)

    class Completion(BaseModel):
        model_config = ConfigDict(extra='forbid')
        model: str
        messages: list[Message] = Field(min_length=1, max_length=50)
        max_tokens: int = Field(default=2048, ge=1, le=4096)
        temperature: float | None = 0.
        seed: int | None = None
        stream: Literal[False] = False
        response_format: dict | None = None

    # FastAPI resolves annotations in module globals, including nested schemas.
    globals()['Completion'] = Completion
    app = FastAPI(title='Local ModelScope TaskSpec model', docs_url=None, redoc_url=None)

    @app.get('/health')
    def health():
        return {'ok':True, 'model':engine.model_name, 'source':engine.provenance}

    @app.get('/v1/models')
    def models():
        return {'object':'list', 'data':[{'id':engine.model_name,'object':'model','owned_by':'local-modelscope',
                                        'sat_sim_source':engine.provenance}]}

    @app.post('/v1/chat/completions')
    def complete(body: Completion):
        if body.model != engine.model_name:
            raise HTTPException(404, detail='MODEL_NOT_LOADED')
        if body.temperature not in (None,0.):
            raise HTTPException(422, detail='THIS_ENDPOINT_USES_DETERMINISTIC_GREEDY_DECODING')
        fmt = body.response_format or {}
        if fmt and fmt.get('type') not in ('text','json_object','json_schema'):
            raise HTTPException(422, detail='UNSUPPORTED_RESPONSE_FORMAT')
        messages = [item.model_dump() for item in body.messages]
        result = None
        try:
            from .guided_decoding import response_schema
            schema = response_schema(fmt)
            options = {'response_schema':schema} if schema is not None else {}
            result = engine.generate(messages, max_tokens=body.max_tokens, **options)
            if fmt.get('type') in ('json_object','json_schema'):
                parsed = json.loads(result['text'])
                if not isinstance(parsed, dict):
                    raise ValueError('MODEL_OUTPUT_IS_NOT_JSON_OBJECT')
                if fmt.get('type') == 'json_schema':
                    import jsonschema
                    from referencing import Registry
                    schema = fmt['json_schema']['schema']
                    def check_local_refs(item):
                        if isinstance(item, dict):
                            if '$ref' in item and not str(item['$ref']).startswith('#'):
                                raise ValueError('REMOTE_SCHEMA_REFERENCES_FORBIDDEN')
                            for value in item.values():
                                check_local_refs(value)
                        elif isinstance(item, list):
                            for value in item:
                                check_local_refs(value)
                    check_local_refs(schema)
                    validator = jsonschema.Draft202012Validator
                    validator.check_schema(schema)
                    validator(schema, registry=Registry()).validate(parsed)
        except (ValueError, KeyError, SchemaError, JsonSchemaError) as exc:
            raise HTTPException(422, detail={'reason_code':'MODEL_OUTPUT_OR_CONTEXT_REJECTED',
                'message':str(exc),'model_output':result}) from exc
        except Exception as exc:
            raise HTTPException(503, detail={'reason_code':'MODEL_GENERATION_FAILED','error_type':type(exc).__name__}) from exc
        return {'id':'chatcmpl-'+uuid4().hex, 'object':'chat.completion','created':int(time.time()),
            'model':engine.model_name, 'choices':[{'index':0,'message':{'role':'assistant','content':result['text']},
                                                'finish_reason':result['finish_reason']}],
            'usage':{key:result[key] for key in ('prompt_tokens','completion_tokens')},
            'sat_sim':{**engine.provenance, 'elapsed_s':result['elapsed_s'],
                       'structured_generation':result.get('structured_generation'),
                       'output_sha256':hashlib.sha256(result['text'].encode()).hexdigest(),
                       'template_fallback_used':False}}
    return app
