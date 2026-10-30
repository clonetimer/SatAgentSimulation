"""Use bounded cuDNN prefill and avoid per-length cuDNN planning during decode."""


def register_cudnn_prefill_attention():
    from torch.nn.attention import sdpa_kernel, SDPBackend
    from transformers.modeling_utils import AttentionInterface
    from transformers.masking_utils import AttentionMaskInterface, sdpa_mask
    from transformers.integrations.sdpa_attention import sdpa_attention_forward

    name = 'sat_cudnn_prefill'

    def attention(module, query, key, value, attention_mask, **kwargs):
        # For one-token KV-cache decode the attention matrix is linear in
        # context length. Math SDPA avoids cuDNN rebuilding a plan for each
        # successive KV length; full-sequence prefill retains cuDNN's low memory.
        backend = SDPBackend.CUDNN_ATTENTION if query.shape[-2] > 1 else SDPBackend.MATH
        with sdpa_kernel(backend):
            return sdpa_attention_forward(module, query, key, value, attention_mask, **kwargs)

    AttentionInterface.register(name, attention)
    AttentionMaskInterface.register(name, sdpa_mask)
    return name
