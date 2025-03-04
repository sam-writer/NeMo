# Copyright (c) 2022, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Transformer based language model."""
from nemo.collections.nlp.modules.common.megatron.megatron_transformer_encoder import MegatronTransformerEncoderModule
from nemo.collections.nlp.modules.common.megatron.retrieval_transformer import (
    MegatronRetrievalTransformerEncoderModule,
)
from nemo.collections.nlp.modules.common.megatron.utils import (
    ApexGuardDefaults,
    init_method_normal,
    scaled_init_method_normal,
)

try:
    from apex.transformer.enums import AttnMaskType, ModelType

    HAVE_APEX = True
except (ImportError, ModuleNotFoundError):
    HAVE_APEX = False
    # fake missing classes with None attributes
    AttnMaskType = ApexGuardDefaults()
    ModelType = ApexGuardDefaults()

__all__ = []

AVAILABLE_ENCODERS = ["transformer"]


def get_encoder_model(
    arch,
    hidden_size,
    ffn_hidden_size,
    num_layers,
    num_attention_heads,
    apply_query_key_layer_scaling=True,
    kv_channels=None,
    init_method=None,
    scaled_init_method=None,
    encoder_attn_mask_type=AttnMaskType.padding,
    pre_process=True,
    post_process=True,
    init_method_std=0.02,
    use_cpu_initialization=False,
    hidden_dropout=0.1,
    attention_dropout=0.1,
    precision=16,
    fp32_residual_connection=False,
    activations_checkpoint_method=None,
    activations_checkpoint_num_layers=1,
    layernorm_epsilon=1e-5,
    bias_gelu_fusion=True,
    bias_dropout_add_fusion=True,
    masked_softmax_fusion=True,
    persist_layer_norm=False,
    openai_gelu=False,
    activation="gelu",
    onnx_safe=False,
    bias=True,
    normalization="layernorm",
    headscale=False,
    transformer_block_type="pre_ln",
    hidden_steps=-1,
    hidden_blocks=1,
    parent_model_type=ModelType.encoder_or_decoder,
    layer_type=None,
    chunk_size=64,
):
    """Build language model and return along with the key to save."""

    if kv_channels is None:
        assert (
            hidden_size % num_attention_heads == 0
        ), 'hidden_size must be divisible by num_attention_heads if kv_channels is None'
        kv_channels = hidden_size // num_attention_heads

    if init_method is None:
        init_method = init_method_normal(init_method_std)

    if scaled_init_method is None:
        scaled_init_method = scaled_init_method_normal(init_method_std, num_layers)

    if arch == "transformer":
        # Language encoder.
        encoder = MegatronTransformerEncoderModule(
            init_method=init_method,
            output_layer_init_method=scaled_init_method,
            hidden_size=hidden_size,
            num_layers=num_layers,
            num_attention_heads=num_attention_heads,
            apply_query_key_layer_scaling=apply_query_key_layer_scaling,
            kv_channels=kv_channels,
            ffn_hidden_size=ffn_hidden_size,
            encoder_attn_mask_type=encoder_attn_mask_type,
            pre_process=pre_process,
            post_process=post_process,
            use_cpu_initialization=use_cpu_initialization,
            hidden_dropout=hidden_dropout,
            attention_dropout=attention_dropout,
            precision=precision,
            fp32_residual_connection=fp32_residual_connection,
            activations_checkpoint_method=activations_checkpoint_method,
            activations_checkpoint_num_layers=activations_checkpoint_num_layers,
            layernorm_epsilon=layernorm_epsilon,
            bias_gelu_fusion=bias_gelu_fusion,
            bias_dropout_add_fusion=bias_dropout_add_fusion,
            masked_softmax_fusion=masked_softmax_fusion,
            persist_layer_norm=persist_layer_norm,
            openai_gelu=openai_gelu,
            onnx_safe=onnx_safe,
            activation=activation,
            bias=bias,
            normalization=normalization,
            transformer_block_type=transformer_block_type,
            headscale=headscale,
            parent_model_type=parent_model_type,
        )
    elif arch == "retro":
        encoder = MegatronRetrievalTransformerEncoderModule(
            init_method=init_method,
            output_layer_init_method=scaled_init_method,
            hidden_size=hidden_size,
            num_layers=num_layers,
            num_attention_heads=num_attention_heads,
            apply_query_key_layer_scaling=apply_query_key_layer_scaling,
            kv_channels=kv_channels,
            layer_type=layer_type,
            ffn_hidden_size=ffn_hidden_size,
            pre_process=pre_process,
            post_process=post_process,
            use_cpu_initialization=use_cpu_initialization,
            hidden_dropout=hidden_dropout,
            attention_dropout=attention_dropout,
            precision=precision,
            fp32_residual_connection=fp32_residual_connection,
            activations_checkpoint_method=activations_checkpoint_method,
            activations_checkpoint_num_layers=activations_checkpoint_num_layers,
            layernorm_epsilon=layernorm_epsilon,
            bias_gelu_fusion=bias_gelu_fusion,
            bias_dropout_add_fusion=bias_dropout_add_fusion,
            masked_softmax_fusion=masked_softmax_fusion,
            persist_layer_norm=persist_layer_norm,
            openai_gelu=openai_gelu,
            onnx_safe=onnx_safe,
            activation=activation,
            bias=bias,
            parent_model_type=parent_model_type,
            chunk_size=chunk_size,
        )
    else:
        raise ValueError(f"Unknown encoder arch = {arch}. Available encoder arch = {AVAILABLE_ENCODERS}")

    return encoder
