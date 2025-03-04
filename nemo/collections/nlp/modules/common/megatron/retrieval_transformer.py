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

"""Retrival Transformer."""

from einops import rearrange, repeat

from nemo.collections.nlp.modules.common.megatron.module import MegatronModule
from nemo.collections.nlp.modules.common.megatron.rotary_pos_embedding import RotaryEmbedding
from nemo.collections.nlp.modules.common.megatron.transformer import ParallelTransformer
from nemo.collections.nlp.modules.common.megatron.utils import ApexGuardDefaults, build_attention_mask_3d

try:
    from apex.transformer.enums import AttnMaskType, ModelType

    HAVE_APEX = True
except (ImportError, ModuleNotFoundError):
    # fake missing classes with None attributes
    AttnMaskType = ApexGuardDefaults()
    ModelType = ApexGuardDefaults()
    HAVE_APEX = False


class MegatronRetrievalTransformerEncoderModule(MegatronModule):
    """Transformer encoder model.
    """

    def __init__(
        self,
        init_method,
        output_layer_init_method,
        hidden_size,
        ffn_hidden_size,
        num_layers,
        num_attention_heads,
        apply_query_key_layer_scaling=True,
        kv_channels=None,
        layer_type=[],
        pre_process=True,
        post_process=True,
        use_cpu_initialization=False,
        attn_mask_type=AttnMaskType.padding,
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
        onnx_safe=False,
        activation='gelu',
        bias=True,
        parent_model_type=ModelType.encoder_or_decoder,
        chunk_size=64,
    ):
        super(MegatronRetrievalTransformerEncoderModule, self).__init__()

        self.pre_process = pre_process
        self.post_process = post_process
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.init_method = init_method
        self.model_attn_mask_type = attn_mask_type
        self.hidden_dropout = hidden_dropout
        self.output_layer_init_method = output_layer_init_method
        self.parent_model_type = parent_model_type

        if kv_channels is None:

            assert (
                hidden_size % num_attention_heads == 0
            ), 'hidden_size must be divisible by num_attention_heads if kv_channels is None'
            kv_channels = hidden_size // num_attention_heads

        # Transformer.
        self.model = ParallelTransformer(
            init_method=self.init_method,
            output_layer_init_method=self.output_layer_init_method,
            num_layers=self.num_layers,
            hidden_size=self.hidden_size,
            num_attention_heads=num_attention_heads,
            apply_query_key_layer_scaling=apply_query_key_layer_scaling,
            kv_channels=kv_channels,
            layer_type=layer_type,
            ffn_hidden_size=ffn_hidden_size,
            self_attn_mask_type=self.model_attn_mask_type,
            pre_process=self.pre_process,
            post_process=self.post_process,
            precision=precision,
            fp32_residual_connection=fp32_residual_connection,
            activations_checkpoint_method=activations_checkpoint_method,
            activations_checkpoint_num_layers=activations_checkpoint_num_layers,
            layernorm_epsilon=layernorm_epsilon,
            hidden_dropout=hidden_dropout,
            attention_dropout=attention_dropout,
            use_cpu_initialization=use_cpu_initialization,
            bias_gelu_fusion=bias_gelu_fusion,
            bias_dropout_fusion=bias_dropout_add_fusion,
            masked_softmax_fusion=masked_softmax_fusion,
            persist_layer_norm=persist_layer_norm,
            openai_gelu=openai_gelu,
            onnx_safe=onnx_safe,
            activation=activation,
            bias=bias,
            model_type=parent_model_type,
            chunk_size=chunk_size,
        )
        rot_dim = hidden_size // num_attention_heads if kv_channels is None else kv_channels
        self.rotary_pos_emb = RotaryEmbedding(rot_dim)
        self.chunk_size = chunk_size
        self._model_key = 'model'

    def set_input_tensor(self, input_tensor):
        """ See megatron.model.transformer.set_input_tensor()"""
        self.model.set_input_tensor(input_tensor)

    def forward(
        self,
        enc_input,
        enc_attn_mask,
        context_attn_mask=None,
        encoder_output=None,
        layer_past=None,
        get_key_value=False,
    ):
        # expected enc_input shape [batch, num_chunks, num_neighbors, retrival_seq_len, dim]
        # expected enc_attn_mask shape [batch, num_chunks, num_neighbors, retrival_seq_len]
        # expected encoder_output shape [batch, seq_len, dim]
        b, k, r, rn, dim = enc_input.shape

        # batch, seq_len, dim
        _, n, _ = encoder_output.shape

        num_seq_chunks = n // self.chunk_size
        assert k == num_seq_chunks, f'sequence requires {num_seq_chunks} retrieved chunks, but only {k} passed in'

        seq_index = num_seq_chunks * self.chunk_size

        retrieved = rearrange(enc_input, 'b k r n d -> (b k r) n d')
        enc_attn_mask = rearrange(enc_attn_mask, 'b k r n -> (b k r) n')
        embed_as_context = repeat(encoder_output[:, :seq_index], 'b (k n) d -> (b k r) n d', n=self.chunk_size, r=r)
        context_attn_mask = repeat(context_attn_mask[:, :seq_index], 'b (k n) -> (b k r) n', n=self.chunk_size, r=r)

        # need to add extra chunk size, since it will be shifted
        cross_attn_q_pos_emb = self.rotary_pos_emb(rn, offset=0)
        cross_attn_k_pos_emb = self.rotary_pos_emb(self.chunk_size)
        attn_pos_emb = (cross_attn_q_pos_emb, cross_attn_q_pos_emb, cross_attn_k_pos_emb)

        # # convert to Megatron mask
        enc_attn_mask_3d = build_attention_mask_3d(
            source_mask=enc_attn_mask, target_mask=enc_attn_mask, attn_mask_type=self.model_attn_mask_type,
        )
        enc_attn_mask_3d = enc_attn_mask_3d[:, None, :, :]

        enc_dec_attn_mask_3d = build_attention_mask_3d(
            source_mask=enc_attn_mask, target_mask=context_attn_mask, attn_mask_type=AttnMaskType.padding,
        )
        enc_dec_attn_mask_3d = enc_dec_attn_mask_3d[:, None, :, :]

        # transformer encoder
        enc_output = self.model(
            retrieved,
            enc_attn_mask_3d,
            layer_past=layer_past,
            get_key_value=get_key_value,
            encoder_output=embed_as_context,
            enc_dec_attn_mask=enc_dec_attn_mask_3d,
            rotary_pos_emb=attn_pos_emb,
        )
        # revert back to original retrieved shape
        enc_output = rearrange(enc_output, '(b k r) n d -> b k r n d', b=b, k=k)
        return enc_output

    def state_dict_for_save_checkpoint(self, destination=None, prefix='', keep_vars=False):
        """For easy load."""

        state_dict_ = {}

        state_dict_[self._model_key] = self.model.state_dict_for_save_checkpoint(destination, prefix, keep_vars)

        return state_dict_

    def load_state_dict(self, state_dict, strict=True):
        """Customized load."""

        # Encoder.
        if self._model_key in state_dict:
            state_dict_ = state_dict[self._model_key]
        self.model.load_state_dict(state_dict_, strict=strict)


class MegatronRetrievalTransformerDecoderModule(MegatronModule):
    """Transformer decoder model.
    """

    def __init__(
        self,
        init_method,
        output_layer_init_method,
        hidden_size,
        ffn_hidden_size,
        num_layers,
        num_attention_heads,
        apply_query_key_layer_scaling=True,
        kv_channels=None,
        layer_type=[],
        pre_process=True,
        post_process=True,
        use_cpu_initialization=False,
        attn_mask_type=AttnMaskType.causal,
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
        onnx_safe=False,
        activation='gelu',
        bias=True,
        parent_model_type=ModelType.encoder_or_decoder,
        chunk_size=64,
    ):
        super(MegatronRetrievalTransformerDecoderModule, self).__init__()

        self.pre_process = pre_process
        self.post_process = post_process
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.init_method = init_method
        self.model_attn_mask_type = attn_mask_type
        self.hidden_dropout = hidden_dropout
        self.output_layer_init_method = output_layer_init_method
        self.parent_model_type = parent_model_type

        if kv_channels is None:

            assert (
                hidden_size % num_attention_heads == 0
            ), 'hidden_size must be divisible by num_attention_heads if kv_channels is None'
            kv_channels = hidden_size // num_attention_heads

        # Transformer.
        self.model = ParallelTransformer(
            init_method=self.init_method,
            output_layer_init_method=self.output_layer_init_method,
            num_layers=self.num_layers,
            hidden_size=self.hidden_size,
            num_attention_heads=num_attention_heads,
            apply_query_key_layer_scaling=apply_query_key_layer_scaling,
            kv_channels=kv_channels,
            layer_type=layer_type,
            ffn_hidden_size=ffn_hidden_size,
            self_attn_mask_type=self.model_attn_mask_type,
            pre_process=self.pre_process,
            post_process=self.post_process,
            precision=precision,
            fp32_residual_connection=fp32_residual_connection,
            activations_checkpoint_method=activations_checkpoint_method,
            activations_checkpoint_num_layers=activations_checkpoint_num_layers,
            layernorm_epsilon=layernorm_epsilon,
            hidden_dropout=hidden_dropout,
            attention_dropout=attention_dropout,
            use_cpu_initialization=use_cpu_initialization,
            bias_gelu_fusion=bias_gelu_fusion,
            bias_dropout_fusion=bias_dropout_add_fusion,
            masked_softmax_fusion=masked_softmax_fusion,
            persist_layer_norm=persist_layer_norm,
            openai_gelu=openai_gelu,
            onnx_safe=onnx_safe,
            activation=activation,
            bias=bias,
            model_type=parent_model_type,
            chunk_size=chunk_size,
        )
        rot_dim = hidden_size // num_attention_heads if kv_channels is None else kv_channels
        self.rotary_pos_emb = RotaryEmbedding(rot_dim)
        self.chunk_size = chunk_size
        self._model_key = 'model'

    def set_input_tensor(self, input_tensor):
        """ See megatron.model.transformer.set_input_tensor()"""
        self.model.set_input_tensor(input_tensor)

    def forward(
        self,
        dec_input,
        dec_attn_mask,
        retrieved_attn_mask=None,
        retrieved_emb=None,
        layer_past=None,
        get_key_value=False,
    ):
        # expected dec_input shape [batch, seq_len, dim]
        # expected dec_attn_mask shape [batch, seq_len]
        # expected retrieved_input shape [batch, num_chunks, num_neighbors, retrival_seq_len, dim]
        # expected retrieved_attn_mask shape [batch, num_chunks, num_neighbors, retrival_seq_len]

        # batch, seq_len, dim
        _, n, _ = dec_input.shape

        num_seq_chunks = n // self.chunk_size

        if retrieved_emb is not None:
            b, k, r, rn, dim = retrieved_emb.shape
            assert (
                k == num_seq_chunks
            ), f'sequence requires {num_seq_chunks} retrieved chunks, but only {k} passed in'  # need to add extra chunk size, since it will be shifted
        self_attn_emb = self.rotary_pos_emb(n)
        cross_attn_q_pos_emb = self.rotary_pos_emb(self.chunk_size * 2 - 1)

        if retrieved_emb is not None:
            cross_attn_k_pos_emb = self.rotary_pos_emb(rn, offset=0)
            attn_pos_emb = (self_attn_emb, cross_attn_q_pos_emb, cross_attn_k_pos_emb)
        else:
            attn_pos_emb = (self_attn_emb, cross_attn_q_pos_emb, None)

        # # convert to Megatron mask
        dec_attn_mask_3d = build_attention_mask_3d(
            source_mask=dec_attn_mask, target_mask=dec_attn_mask, attn_mask_type=self.model_attn_mask_type,
        )
        dec_attn_mask_3d = dec_attn_mask_3d[:, None, :, :]

        if retrieved_emb is not None:
            dec_attn_mask = rearrange(dec_attn_mask, 'b (k n) -> (b k) n', k=k)
            retrieved_attn_mask = rearrange(retrieved_attn_mask, 'b k r n -> (b k) (r n)')

            enc_dec_attn_mask_3d = build_attention_mask_3d(
                source_mask=dec_attn_mask, target_mask=retrieved_attn_mask, attn_mask_type=AttnMaskType.padding,
            )
            enc_dec_attn_mask_3d = enc_dec_attn_mask_3d[:, None, :, :]
        else:
            enc_dec_attn_mask_3d = None

        # transformer encoder
        enc_output = self.model(
            dec_input,
            dec_attn_mask_3d,
            layer_past=layer_past,
            get_key_value=get_key_value,
            encoder_output=None,
            retrieved_emb=retrieved_emb,
            enc_dec_attn_mask=enc_dec_attn_mask_3d,
            rotary_pos_emb=attn_pos_emb,
        )

        return enc_output

    def state_dict_for_save_checkpoint(self, destination=None, prefix='', keep_vars=False):
        """For easy load."""

        state_dict_ = {}

        state_dict_[self._model_key] = self.model.state_dict_for_save_checkpoint(destination, prefix, keep_vars)

        return state_dict_

    def load_state_dict(self, state_dict, strict=True):
        """Customized load."""

        # Encoder.
        if self._model_key in state_dict:
            state_dict_ = state_dict[self._model_key]
        self.model.load_state_dict(state_dict_, strict=strict)
