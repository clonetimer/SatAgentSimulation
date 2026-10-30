"""GPU LoRA SFT with source hashes, completion-only loss and held-out records."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import nullcontext
from importlib.metadata import version
import json
from pathlib import Path
import time

from .data import encode_record, load_training_data, sha256, verify_base_model


def completion_head_loss(hidden_states, labels, head, *, chunk_tokens=128):
    """Exact assistant-token mean CE with bounded vocabulary-logit storage.

    Each head chunk is checkpointed. Prefix tokens still receive attention
    gradients through the transformer; the supervised objective is unchanged.
    """
    import torch
    from torch.nn import functional as F
    from torch.utils.checkpoint import checkpoint
    target = labels[:,1:]
    mask = target != -100
    predictors = hidden_states[:,:-1,:][mask]
    target = target[mask]
    if not target.numel():
        raise ValueError('SFT_NO_SUPERVISED_ASSISTANT_TOKENS')
    def loss_chunk(states, ids):
        return F.cross_entropy(head(states).float(), ids, reduction='sum')
    total = hidden_states.new_zeros((), dtype=torch.float32)
    for start in range(0, target.numel(), chunk_tokens):
        states, ids = predictors[start:start+chunk_tokens], target[start:start+chunk_tokens]
        value = checkpoint(loss_chunk, states, ids, use_reentrant=False) if torch.is_grad_enabled() else loss_chunk(states, ids)
        total = total+value
    return total/target.numel()


def train(args):
    import torch
    from peft import LoraConfig, TaskType, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainerCallback, TrainingArguments, set_seed

    if not torch.cuda.is_available():
        raise RuntimeError('SFT_CUDA_REQUIRED: install the verified CUDA wheel in .venv-sft')
    if args.output_dir.exists() and any(args.output_dir.iterdir()) and not args.resume:
        raise ValueError('SFT_RUN_ALREADY_EXISTS_USE_NEW_DIRECTORY_OR_EXPLICIT_RESUME')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    records, dataset_manifest = load_training_data(args.dataset_dir)
    snapshot, base_manifest = verify_base_model(args.base_manifest)
    torch.set_num_threads(4)
    set_seed(args.seed)
    torch.cuda.reset_peak_memory_stats()
    tokenizer = AutoTokenizer.from_pretrained(snapshot, trust_remote_code=False, local_files_only=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    encoded = {split:[encode_record(tokenizer, row, args.max_length) for row in rows]
               for split, rows in records.items()}
    if args.smoke_longest:
        encoded['train'] = sorted(encoded['train'], key=lambda row:len(row['input_ids']), reverse=True)[:2]
        encoded['validation'] = sorted(encoded['validation'], key=lambda row:len(row['input_ids']), reverse=True)[:2]
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    model = AutoModelForCausalLM.from_pretrained(snapshot, torch_dtype=dtype,
        attn_implementation='sdpa', trust_remote_code=False, local_files_only=True,
        use_safetensors=True).to('cuda')
    model.config.use_cache = False
    model = get_peft_model(model, LoraConfig(task_type=TaskType.CAUSAL_LM,
        r=args.rank, lora_alpha=2*args.rank, lora_dropout=.05,
        target_modules=['q_proj','k_proj','v_proj','o_proj','gate_proj','up_proj','down_proj']))
    model.enable_input_require_grads()

    def collate(rows):
        length = max(len(row['input_ids']) for row in rows)
        return {key:torch.tensor([row[key]+[pad]*(length-len(row[key])) for row in rows], dtype=torch.long)
                for key,pad in (('input_ids',tokenizer.pad_token_id),('attention_mask',0),('labels',-100))}

    provenance = {'schema_version':'sat-lora-training.v1', 'state':'TRAINING',
        'started_at_utc':datetime.now(timezone.utc).isoformat(),
        'base_model':base_manifest['model_id'], 'base_content_sha256':base_manifest['snapshot_content_sha256'],
        'dataset_manifest_sha256':sha256(args.dataset_dir/'manifest.json'),
        'dataset_splits':dataset_manifest['splits'], 'seed':args.seed,
        'training':{'epochs':args.epochs,'max_length':args.max_length,'rank':args.rank,
                    'gradient_accumulation_steps':args.gradient_accumulation,'learning_rate':args.learning_rate},
        'hardware':{'gpu':torch.cuda.get_device_name(0), 'cuda':torch.version.cuda, 'torch':torch.__version__},
        'software':{name:version(name) for name in ('transformers','peft','accelerate','safetensors')},
        'training_source_sha256':sha256(Path(__file__)),
        'trust_remote_code':False, 'loss_scope':'assistant_completion_only',
        'loss_head_chunk_tokens':args.loss_chunk_tokens,
        'attention_backend':args.attention_backend,
        'train_records_used':len(encoded['train']), 'validation_records_used':len(encoded['validation']),
        'label_source':'synthetic_registry_contract_not_flight_data',
        'task_spec_acceptance':'NOT_EVALUATED'}
    manifest_path = args.output_dir/'training_provenance.json'
    if args.resume:
        prior = json.loads(manifest_path.read_text(encoding='utf-8'))
        for field in ('base_content_sha256','dataset_manifest_sha256','training','seed'):
            if prior[field] != provenance[field]:
                raise ValueError(f'SFT_RESUME_PROVENANCE_MISMATCH: {field}')
    manifest_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')

    class Progress(TrainerCallback):
        def on_log(self, training_args, state, control, logs=None, **_):
            row = {'step':state.global_step, 'epoch':state.epoch, 'observed_at_utc':datetime.now(timezone.utc).isoformat(), **(logs or {})}
            with (args.output_dir/'metrics.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row)+'\n')
            print(json.dumps(row), flush=True)

    training_args = TrainingArguments(output_dir=str(args.output_dir/'checkpoints'),
        num_train_epochs=args.epochs, per_device_train_batch_size=1, per_device_eval_batch_size=1,
        gradient_accumulation_steps=args.gradient_accumulation,
        learning_rate=args.learning_rate, lr_scheduler_type='cosine', warmup_ratio=.05,
        bf16=dtype == torch.bfloat16, fp16=dtype == torch.float16,
        gradient_checkpointing=True, gradient_checkpointing_kwargs={'use_reentrant':False},
        logging_steps=1, eval_strategy='epoch', save_strategy='epoch',
        load_best_model_at_end=True, metric_for_best_model='eval_loss', greater_is_better=False,
        save_total_limit=2, report_to='none', dataloader_num_workers=0,
        seed=args.seed, data_seed=args.seed, optim='adamw_torch',
        max_steps=args.max_steps, remove_unused_columns=False, prediction_loss_only=True)

    class CompletionOnlyTrainer(Trainer):
        def compute_loss(self, wrapped, inputs, return_outputs=False, num_items_in_batch=None):
            peft = self.accelerator.unwrap_model(wrapped)
            base = peft.get_base_model()
            if base.config.model_type not in ('qwen2','qwen3'):
                raise ValueError('SFT_CHUNKED_LOSS_REQUIRES_SUPPORTED_QWEN_CAUSAL_DECODER')
            # Batch size is one. Omitting an all-valid mask preserves precisely
            # the same causal attention and permits cuDNN's bounded-memory path.
            mask = inputs['attention_mask']
            if bool((mask == 1).all()):
                mask = None
            hidden = base.model(input_ids=inputs['input_ids'], attention_mask=mask,
                                use_cache=False, return_dict=True).last_hidden_state
            loss = completion_head_loss(hidden, inputs['labels'], base.lm_head, chunk_tokens=args.loss_chunk_tokens)
            return (loss, {'loss':loss.detach()}) if return_outputs else loss

    trainer = CompletionOnlyTrainer(model=model, args=training_args, train_dataset=encoded['train'],
        eval_dataset=encoded['validation'], data_collator=collate, processing_class=tokenizer,
        callbacks=[Progress()])
    trainer.model_accepts_loss_kwargs = False
    started = time.perf_counter()
    try:
        from torch.nn.attention import sdpa_kernel, SDPBackend
        attention_context = sdpa_kernel(SDPBackend.CUDNN_ATTENTION) if args.attention_backend == 'cudnn' else nullcontext()
        # Keep the choice active through backward checkpoint recomputation too.
        with attention_context:
            result = trainer.train(resume_from_checkpoint=str(args.resume) if args.resume else None)
            validation = trainer.evaluate()
        adapter_dir = args.output_dir/'adapter'
        trainer.save_model(str(adapter_dir))
        tokenizer.save_pretrained(adapter_dir)
    except BaseException as exc:
        provenance.update(state='INTERRUPTED' if isinstance(exc, KeyboardInterrupt) else 'FAILED',
                          error={'type':type(exc).__name__, 'message':str(exc)},
                          stopped_at_utc=datetime.now(timezone.utc).isoformat())
        manifest_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
        raise
    provenance.update(state='SMOKE_TRAINING_ONLY' if args.max_steps > 0 else 'TRAINED_NOT_TASKSPEC_ACCEPTED',
        completed_at_utc=datetime.now(timezone.utc).isoformat(), elapsed_s=time.perf_counter()-started,
        global_step=trainer.state.global_step, train_metrics=result.metrics, validation_loss_metrics=validation,
        cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
        cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved(), adapter_dir=str(adapter_dir.resolve()))
    provenance['adapter_files'] = [{'path':path.relative_to(adapter_dir).as_posix(), 'sha256':sha256(path)}
                                  for path in sorted(adapter_dir.rglob('*')) if path.is_file()]
    manifest_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(provenance, ensure_ascii=False), flush=True)
    return provenance
