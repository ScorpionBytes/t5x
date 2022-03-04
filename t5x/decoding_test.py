# Copyright 2022 The T5X Authors.
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

"""Tests for t5x.decoding."""

import functools
from typing import Mapping, Tuple
from unittest import mock

from absl.testing import absltest
from absl.testing import parameterized
import jax
import jax.numpy as jnp
import numpy as np
from t5x import decoding

EOS_ID = 1
NEG_INF = decoding.NEG_INF


class DecodeTest(parameterized.TestCase):

  def test_temperature_sample_uneven_prefix(self):

    def token_to_logits(ids, cache):
      del ids
      del cache
      # Always sample id 2 for batch element 0 and id 3 for element 1.
      logits = np.array([[-1e7, -1e7, 0, -1e7], [-1e7, -1e7, -1e7, 0]],
                        dtype=np.float32)
      return logits, {}

    inputs = np.array([[0, 5, 7, 1, 0, 0], [0, 6, 1, 0, 0, 0]])
    sampled_sequences, _ = decoding._temperature_sample_single_trial(
        inputs, {},
        token_to_logits,
        EOS_ID,
        jax.random.PRNGKey(0),
        topk=0,
        initial_index=np.array([3, 2]))
    expected = np.array([[5, 7, 1, 2, 2, 2], [6, 1, 3, 3, 3, 3]])
    np.testing.assert_array_equal(expected, sampled_sequences)

  def test_temperature_sample_no_prefix(self):
    batch, max_decode_len = 2, 3

    def token_to_logits(ids, cache):  # pylint: disable=unused-argument
      # Always sample id 2 for batch element 0 and id 3 for element 1.
      logits = np.array([[-1e7, -1e7, 0, -1e7], [-1e7, -1e7, -1e7, 0]],
                        dtype=np.float32)
      return logits, {}

    inputs = np.zeros((batch, max_decode_len), dtype=np.int32)
    sampled_sequences, _ = decoding._temperature_sample_single_trial(
        inputs, {}, token_to_logits, EOS_ID, jax.random.PRNGKey(0), topk=0)

    expected = [[2, 2, 2], [3, 3, 3]]
    np.testing.assert_array_equal(expected, sampled_sequences)

  def test_temperature_sample_prefix(self):

    def token_to_logits(ids, cache):  # pylint: disable=unused-argument
      # Always sample id 2 for batch element 0 and id 3 for element 1.
      logits = np.array([[-1e7, -1e7, 0, -1e7], [-1e7, -1e7, -1e7, 0]],
                        dtype=np.float32)
      return logits, {}

    # batch element 0 has length 3 prefix and element 1 has length 2.
    inputs = np.array([[0, 5, 6, 7, 0], [0, 8, 9, 0, 0]], dtype=np.int32)
    sampled_sequences, _ = decoding._temperature_sample_single_trial(
        inputs, {}, token_to_logits, EOS_ID, jax.random.PRNGKey(0), topk=0)

    expected = [[5, 6, 7, 2, 2], [8, 9, 3, 3, 3]]
    np.testing.assert_array_equal(expected, sampled_sequences)

  def test_temperature_sample_prefix_ending_with_eos(self):

    def token_to_logits(ids, cache):  # pylint: disable=unused-argument
      # Always sample id 2 for batch element 0 and id 3 for element 1.
      logits = np.array([[-1e7, -1e7, 0, -1e7], [-1e7, -1e7, -1e7, 0]],
                        dtype=np.float32)
      return logits, {}

    # batch element 0 has length 4 prefix (including the initial dummy token and
    # the last eos) and element 1 has length 3.
    inputs = np.array([[0, 5, 6, 1, 0], [0, 8, 1, 0, 0]], dtype=np.int32)
    sampled_sequences, _ = decoding._temperature_sample_single_trial(
        inputs, {}, token_to_logits, EOS_ID, jax.random.PRNGKey(0), topk=1)

    expected = [[5, 6, 1, 2, 2], [8, 1, 3, 3, 3]]
    np.testing.assert_array_equal(expected, sampled_sequences)

  def test_temperature_sample_prefix_ending_with_eos_early_stop(self):
    batch, max_decode_len = 2, 7
    rng0 = jax.random.PRNGKey(0)

    ret = [np.array([2, 3]) for _ in range(max_decode_len)]
    # Sequence 1 outputs EOS=1 when i = 3 where `i` is the while loop counter of
    # `decoding._temperature_sample_single_trial`.
    ret[3] = np.array([2, 1])
    # Sequence 0 outputs EOS=1 when i = 4.
    ret[4] = np.array([1, 3])
    ret = jax.numpy.array(ret)

    def mocked_categorical(rng_input, logits):  # pylint: disable=unused-argument
      """Ignores logit and returns only based on the rng_input."""
      rng = rng0
      k = 0
      # Mimic the rng split done in `decoding.sample_loop_body_fn`.
      for j in range(max_decode_len):
        rng1, rng = jax.random.split(rng)
        # We want to sift out `j` for which rng1 == rng_input
        # rngs are a pair of ints. So sum the bool and divide by 2.
        k += j * (rng1 == rng_input).sum() // 2
      # `k` at this point is equal to the while loop variable `i` of the caller.
      return ret[k]

    def token_to_logits(ids, cache):  # pylint: disable=unused-argument
      # These values are not used in this test because random.categorical is
      # directly mocked.
      dummy_logits = np.zeros((batch, 4), dtype=np.float32)
      return dummy_logits, {}

    inputs = np.array([[0, 5, 1, 0, 0, 0, 0], [0, 8, 0, 0, 0, 0, 0]],
                      dtype=np.int32)
    with mock.patch.object(jax.random, 'categorical', new=mocked_categorical):
      sampled_sequences, _ = decoding._temperature_sample_single_trial(
          inputs, {}, token_to_logits, EOS_ID, rng0, topk=0)

    expected = [[5, 1, 2, 2, 1, 0, 0], [8, 3, 3, 1, 0, 0, 0]]
    np.testing.assert_array_equal(expected, sampled_sequences)

  def test_temperature_sample_log_prob(self):
    batch, max_decode_len = 2, 7
    rng0 = jax.random.PRNGKey(0)

    ret = [np.array([2, 3]) for _ in range(max_decode_len)]
    # Sequence 1 outputs EOS=1 when i = 3 where `i` is the while loop counter of
    # `decoding._temperature_sample_single_trial`.
    ret[3] = np.array([2, 1])
    # Sequence 0 outputs EOS=1 when i = 4.
    ret[4] = np.array([1, 3])
    ret = jax.numpy.array(ret)

    # TODO(hwchung): refactor this.
    def mocked_categorical(rng_input, logits):  # pylint: disable=unused-argument
      """Ignores logit and returns only based on the rng_input."""
      rng = rng0
      k = 0
      # Mimic the rng split done in `decoding.sample_loop_body_fn`.
      for j in range(max_decode_len):
        rng1, rng = jax.random.split(rng)
        # We want to sift out `j` for which rng1 == rng_input
        # rngs are a pair of ints. So sum the bool and divide by 2.
        k += j * (rng1 == rng_input).sum() // 2
      # `k` at this point is equal to the while loop variable `i` of the caller.
      return ret[k]

    logits = np.random.randn(batch, 4)
    token_to_logits = lambda ids, cache: (logits, {})
    inputs = np.array([[0, 5, 1, 0, 0, 0, 0], [0, 8, 0, 0, 0, 0, 0]],
                      dtype=np.int32)
    with mock.patch.object(jax.random, 'categorical', new=mocked_categorical):
      sampled_sequences, log_prob = decoding._temperature_sample_single_trial(
          inputs, {}, token_to_logits, EOS_ID, rng0, topk=0)

    log_probs = jax.nn.log_softmax(logits)
    expected = [[5, 1, 2, 2, 1, 0, 0], [8, 3, 3, 1, 0, 0, 0]]
    expected_log_prob = [
        log_probs[0, 2] + log_probs[0, 2] + log_probs[0, 1],
        log_probs[1, 3] + log_probs[1, 3] + log_probs[1, 1]
    ]
    expected_log_prob = np.array(expected_log_prob)
    np.testing.assert_array_equal(expected, sampled_sequences)
    np.testing.assert_allclose(expected_log_prob, log_prob, atol=1e-5)

  def test_temperature_sample_num_decodes(self):
    num_decodes = 3
    rng0 = jax.random.PRNGKey(0)
    inputs = np.array([[0, 5, 1, 0], [0, 8, 7, 0]], dtype=np.int32)

    with mock.patch.object(decoding,
                           '_temperature_sample_single_trial') as mocked:
      # expanded_decodes: [batch * num_decodes, max_decode_len]
      expanded_decodes = np.array([[5, 1, 4, 4], [5, 1, 5, 5], [5, 1, 3, 3],
                                   [8, 7, 5, 5], [8, 7, 3, 3], [8, 7, 4, 4]])
      # expanded_log_prob: [batch * num_decodes]
      expanded_log_prob = np.array([-2.3, -1.3, -3.6, -0.5, -2.5, -1.9])
      mocked.return_value = expanded_decodes, expanded_log_prob

      decodes, scores = decoding.temperature_sample(
          inputs, {}, mock.Mock(), EOS_ID, rng0, num_decodes=num_decodes)

      expanded_inputs = jnp.array([[0, 5, 1, 0], [0, 5, 1, 0], [0, 5, 1, 0],
                                   [0, 8, 7, 0], [0, 8, 7, 0], [0, 8, 7, 0]])
      # Test that the actual decode function is called with the expanded values.
      np.testing.assert_array_equal(mocked.call_args[0][0], expanded_inputs)

    np.testing.assert_array_equal(decodes,
                                  [[[5, 1, 3, 3], [5, 1, 4, 4], [5, 1, 5, 5]],
                                   [[8, 7, 3, 3], [8, 7, 4, 4], [8, 7, 5, 5]]])
    np.testing.assert_allclose(scores, [[-3.6, -2.3, -1.3], [-2.5, -1.9, -0.5]])

  def test_temperature_sample_num_decodes_with_initial_index(self):
    num_decodes = 3
    rng0 = jax.random.PRNGKey(0)
    inputs = np.array([[0, 5, 1, 0], [0, 8, 7, 0]], dtype=np.int32)
    initial_index = np.array([1, 2], dtype=np.int32)

    with mock.patch.object(decoding,
                           '_temperature_sample_single_trial') as mocked:
      with mock.patch.object(decoding, 'cache_map') as mocked_cache_map:
        # expanded_decodes: [batch * num_decodes, max_decode_len]
        expanded_decodes = np.array([[5, 1, 4, 4], [5, 1, 5, 5], [5, 1, 3, 3],
                                     [8, 7, 5, 5], [8, 7, 3, 3], [8, 7, 4, 4]])
        # expanded_log_prob: [batch * num_decodes]
        expanded_log_prob = np.array([-2.3, -1.3, -3.6, -0.5, -2.5, -1.9])
        mocked.return_value = expanded_decodes, expanded_log_prob

        decodes, scores = decoding.temperature_sample(
            inputs, {},
            mock.Mock(),
            EOS_ID,
            rng0,
            num_decodes=num_decodes,
            initial_index=initial_index)

        expanded_inputs = jnp.array([[0, 5, 1, 0], [0, 5, 1, 0], [0, 5, 1, 0],
                                     [0, 8, 7, 0], [0, 8, 7, 0], [0, 8, 7, 0]])
        expanded_initial_index = np.array([1, 1, 1, 2, 2, 2], dtype=np.int32)
        # Test that the actual decode function is called with the expanded
        # values.
        np.testing.assert_array_equal(mocked.call_args[0][0], expanded_inputs)
        np.testing.assert_array_equal(mocked.call_args[1]['initial_index'],
                                      expanded_initial_index)
        # Test that the function was applied to the index in the cache map
        self.assertTrue(mocked_cache_map.call_args[1]['apply_to_index'])

    np.testing.assert_array_equal(decodes,
                                  [[[5, 1, 3, 3], [5, 1, 4, 4], [5, 1, 5, 5]],
                                   [[8, 7, 3, 3], [8, 7, 4, 4], [8, 7, 5, 5]]])
    np.testing.assert_allclose(scores, [[-3.6, -2.3, -1.3], [-2.5, -1.9, -0.5]])

  @parameterized.named_parameters(
      dict(
          testcase_name='no_initial_index',
          initial_index=None,
          expected_calls=6,
      ),
      dict(
          testcase_name='initial_index',
          initial_index=np.array([1, 2], dtype=np.int32),
          expected_calls=4,
      ),
      dict(
          testcase_name='lower_initial_index',
          initial_index=np.array([1, 1], dtype=np.int32),
          expected_calls=5,  # we decode 4 tokens out of the prompt
      ),
  )
  def test_temperature_sample_max_decode_steps_with_initial_index(
      self, initial_index, expected_calls):
    max_decode_steps = 4
    rng0 = jax.random.PRNGKey(0)
    inputs = np.array([[0, 2, 0, 0, 0, 0, 0, 0], [0, 2, 2, 0, 0, 0, 0, 0]],
                      dtype=np.int32)

    token_to_logits = mock.Mock()
    token_to_logits.return_value = (np.array(
        [[-1e7, -1e7, -1e7, 0], [-1e7, -1e7, -1e7, 0]], dtype=np.float32), {})

    # to unroll while loop
    with jax.disable_jit():
      decodes, scores = decoding.temperature_sample(
          inputs, {},
          token_to_logits,
          EOS_ID,
          rng0,
          initial_index=initial_index,
          topk=4,
          max_decode_steps=max_decode_steps)

    self.assertLen(token_to_logits.call_args_list, expected_calls)

    expected_output = np.array([[2, 3, 3, 3, 3, 0, 0, 0],
                                [2, 2, 3, 3, 3, 3, 0, 0]])
    expected_output = jnp.expand_dims(expected_output, 1)

    np.testing.assert_array_equal(decodes, expected_output)
    np.testing.assert_array_equal(scores, [[0.], [0.]])

  def test_temperature_sample_topp(self):
    rng0 = jax.random.PRNGKey(0)
    inputs = np.zeros((1, 20), dtype=np.int32)

    token_to_logits = mock.Mock()

    # logits correspond to (0.3, 0, 0.1, 0.6)
    token_to_logits.return_value = (np.array([[-1.2, -1e7, -2.3, -0.51]],
                                             dtype=np.float32), {})

    decodes, scores = decoding.temperature_sample(
        inputs, {}, token_to_logits, EOS_ID, rng0, topp=0.55,
        topk=0)  # anything under 0.6 will trigger deterministic decoding.

    expected_output = np.array([[3] * 20])
    expected_output = jnp.expand_dims(expected_output, 1)

    np.testing.assert_array_equal(decodes, expected_output)
    np.testing.assert_array_equal(scores, [[0.]])

    # temperature is applied first, so the distribution becomes
    # (0.27, 0, 0.069, 0.65), so if topp is 0.63, it should become greedy.
    decodes, scores = decoding.temperature_sample(
        inputs, {},
        token_to_logits,
        EOS_ID,
        rng0,
        temperature=0.8,
        topp=0.63,
        topk=0)

    expected_output = np.array([[3] * 20])
    expected_output = jnp.expand_dims(expected_output, 1)

    np.testing.assert_array_equal(decodes, expected_output)
    np.testing.assert_array_equal(scores, [[0.]])

  def test_topp_log_probs(self):
    rng0 = jax.random.PRNGKey(0)
    inputs = np.zeros((1, 1), dtype=np.int32)

    token_to_logits = mock.Mock()

    # logits correspond to (0.3, 0, 0.1, 0.6)
    token_to_logits.return_value = (np.array([[-1.2, NEG_INF, -2.3, -0.51]],
                                             dtype=np.float32), {})

    with jax.disable_jit():
      # this lets us see logits after topp and topk are applied
      with mock.patch.object(jax.random, 'categorical') as mocked:
        mocked.return_value = jnp.array([0], dtype=jnp.int32)
        decodes, _ = decoding.temperature_sample(
            inputs, {},
            token_to_logits,
            EOS_ID,
            rng0,
            temperature=1.4,
            topp=0.7,
            topk=0)

    self.assertLen(token_to_logits.call_args_list, 1)
    np.testing.assert_array_equal(decodes, jnp.asarray([[[0]]]))

    np.testing.assert_array_almost_equal(
        mocked.call_args_list[0][0][1],
        jnp.asarray([[-0.85714293, NEG_INF, NEG_INF, -0.36428571]]))

  def test_add_beam_dim(self):
    x = np.array([[0, 5, 1, 0], [0, 8, 6, 9]], dtype=np.int32)
    y = decoding.add_beam_dim(x, beam_size=3)
    self.assertEqual(y.shape, (2, 3, 4))
    np.testing.assert_array_equal([[[0, 5, 1, 0], [0, 5, 1, 0], [0, 5, 1, 0]],
                                   [[0, 8, 6, 9], [0, 8, 6, 9], [0, 8, 6, 9]]],
                                  y)

  def test_flat_batch_beam_expand(self):
    x = np.array([[0, 5, 1, 0], [0, 8, 6, 9]], dtype=np.int32)
    np.testing.assert_array_equal(
        [[0, 5, 1, 0], [0, 5, 1, 0], [0, 8, 6, 9], [0, 8, 6, 9]],
        decoding.flat_batch_beam_expand(x, beam_size=2))

  def test_top_k_two_stage(self):

    def _test_top_k(batch_size, k):
      # Pick sufficiently large seq_len.
      seq_len = 2047 * k * batch_size
      seq = np.arange(seq_len)
      np.random.shuffle(seq)
      x = jnp.reshape(seq, (batch_size, int(seq_len / batch_size))).astype(
          jnp.float32)
      np.testing.assert_almost_equal(
          decoding.top_k_two_stage(x, k), jax.lax.top_k(x, k), decimal=5)

    # Test small batch cases (batch={1,8}, k=16).
    _test_top_k(1, 16)
    _test_top_k(8, 16)
    # Test large batch cases (batch={9,32}, k=11).
    _test_top_k(9, 11)
    _test_top_k(32, 11)

  def test_cache_map(self):
    cache = {
        'layers_0': {
            'cached_key': jnp.ones([3, 6]),
            'cached_values': jnp.ones([3, 6]),
            'cache_index': jnp.ones([
                3,
            ]),
        },
        'layers_1': {
            'self_attention': {
                'cached_key': jnp.ones([2, 7]),
                'cached_values': jnp.ones([5, 8]),
                'cache_index': jnp.array(1),
            },
            'encoder_decoder_attention': {
                'cached_key': jnp.ones([10, 12, 2]),
                'cached_values': jnp.ones([4, 7, 2]),
                'cache_index': jnp.ones([4, 5, 6]),
            }
        },
    }

    fn = functools.partial(jnp.add, 4)

    gold_cache = {
        'layers_0': {
            'cached_key': fn(jnp.ones([3, 6])),
            'cached_values': fn(jnp.ones([3, 6])),
            'cache_index': jnp.ones([
                3,
            ]),
        },
        'layers_1': {
            'self_attention': {
                'cached_key': fn(jnp.ones([2, 7])),
                'cached_values': fn(jnp.ones([5, 8])),
                'cache_index': jnp.array(1),
            },
            'encoder_decoder_attention': {
                'cached_key': fn(jnp.ones([10, 12, 2])),
                'cached_values': fn(jnp.ones([4, 7, 2])),
                'cache_index': jnp.ones([4, 5, 6]),
            }
        }
    }

    jax.tree_multimap(np.testing.assert_array_equal,
                      decoding.cache_map(fn, cache), gold_cache)

  def test_cache_map_with_index(self):
    cache = {
        'layers_0': {
            'cached_key': jnp.ones([3, 6]),
            'cached_values': jnp.ones([3, 6]),
            'cache_index': jnp.ones([
                3,
            ]),
        },
        'layers_1': {
            'relpos_bias': {
                'cached_bias': jnp.ones([1, 5, 3]),
            },
            'self_attention': {
                'cached_key': jnp.ones([2, 7]),
                'cached_values': jnp.ones([5, 8]),
                'cache_index': jnp.array(1),
            },
            'encoder_decoder_attention': {
                'cached_key': jnp.ones([10, 12, 2]),
                'cached_values': jnp.ones([4, 7, 2]),
                'cache_index': jnp.ones([4, 5, 6]),
            }
        },
        'position_embedder': {
            'position_embedder_index': jnp.array(-1),
        },
    }

    fn = functools.partial(jnp.add, 8)

    gold_cache = {
        'layers_0': {
            'cached_key': fn(jnp.ones([3, 6])),
            'cached_values': fn(jnp.ones([3, 6])),
            'cache_index': fn(jnp.ones([
                3,
            ])),
        },
        'layers_1': {
            'relpos_bias': {
                'cached_bias': jnp.ones([1, 5, 3]),
            },
            'self_attention': {
                'cached_key': fn(jnp.ones([2, 7])),
                'cached_values': fn(jnp.ones([5, 8])),
                'cache_index': fn(jnp.array(1)),
            },
            'encoder_decoder_attention': {
                'cached_key': fn(jnp.ones([10, 12, 2])),
                'cached_values': fn(jnp.ones([4, 7, 2])),
                'cache_index': fn(jnp.ones([4, 5, 6])),
            }
        },
        'position_embedder': {
            'position_embedder_index': jnp.array(-1),
        },
    }

    jax.tree_multimap(np.testing.assert_array_equal,
                      decoding.cache_map(fn, cache, apply_to_index=True),
                      gold_cache)

  def test_beam_search(self):
    # Toy problem, we have 4 states, A, B, START, END, (plus PAD).
    # Scores are given by a first-order Markov model.
    batch_size = 2
    beam_size = 2
    # PAD doesn't matter for this test, but part of the contract for beam_search
    # is giving the PAD token id 0.
    states = ['PAD', 'A', 'B', 'START-', '-END']
    num_states = len(states)
    decode_length = 7

    # Edge potentials (written inside edges for diagonals):
    #            1      -1     1      -1
    #         A ---- A ---- A ---- A ---- A
    #       0   \  -1  \  1   \  -1  \  1   0
    # START      X      X      X      X       END
    #       0   /  -1  /  1   /  -1  /  1   0
    #         B ---- B ---- B ---- B ---- B
    #            1      -1     1      -1

    # put the above edge potentials in a 3-tensor
    ab_edge_potentials = np.asarray([[[1, -1], [-1, 1]], [[-1, 1], [1, -1]],
                                     [[1, -1], [-1, 1]], [[-1, 1], [1, -1]]])
    # now we have to add on the START, END states
    # and PAD at 0
    edge_potentials = np.ones([6, 5, 5]) * NEG_INF
    edge_potentials[1:5, 1:3, 1:3] = ab_edge_potentials
    # START can go to either A or B for free at t0
    edge_potentials[0, 3, 1] = 0
    edge_potentials[0, 3, 2] = 0
    # either A or B can go to END for free at t5
    edge_potentials[5, 1, 4] = 0
    edge_potentials[5, 2, 4] = 0
    # PAD can go to anything for free (doesn't matter for this test)
    edge_potentials[:, 0, :] = 0

    edge_potentials = jnp.asarray(edge_potentials)

    # at time 0, we start with state=START=3
    logits0 = jnp.asarray([NEG_INF, NEG_INF, NEG_INF, 0, NEG_INF])

    # add dummy flattened batch x beam dim for broadcasting
    logits0 = jnp.expand_dims(logits0, axis=0)
    edge_potentials = jnp.expand_dims(edge_potentials, axis=0)

    def tokens_to_logits(
        token_indices: jnp.ndarray, state_cache: Mapping[str, jnp.ndarray]
    ) -> Tuple[jnp.ndarray, Mapping[str, jnp.ndarray]]:
      cur_iter = state_cache['cur_iter']
      # grab edge potentials for the current timestep
      cur_edge_potentials = jnp.take_along_axis(
          edge_potentials,
          jnp.reshape(
              jnp.maximum(0, cur_iter[:, 0] - 1),
              (batch_size * beam_size, 1, 1, 1)),
          axis=1)
      cur_edge_potentials = jnp.squeeze(cur_edge_potentials, axis=1)
      # get "logits" from edge potentials for requested tokens (except at t0)
      cur_logits = jnp.matmul(
          jnp.reshape(
              jax.nn.one_hot(token_indices, num_states, axis=1),
              (batch_size * beam_size, 1, num_states)), cur_edge_potentials)
      cur_logits = jnp.squeeze(cur_logits, axis=1)
      # use our START-only logits for t0, otherwise use the edge potentials
      logits_for_tokens = jnp.where(cur_iter == 0, logits0, cur_logits)
      # update state in the cache
      new_cache = state_cache.copy()
      new_cache['cur_iter'] = cur_iter + 1
      return logits_for_tokens, new_cache

    init_cache = {}
    init_cache['cur_iter'] = jnp.zeros((batch_size, 1))

    top_scoring, _ = decoding.beam_search(
        inputs=np.zeros([batch_size, decode_length]),
        cache=init_cache,
        tokens_to_logits=tokens_to_logits,
        eos_id=4,
        num_decodes=beam_size,
        alpha=0.0,
        max_decode_len=decode_length)

    # The two top scoring sequences should be a tie between
    # START-AABBA-END
    # and
    # START-BBAAB-END
    # (and greedy beam search will find both these with just two beams)

    top_scoring_strings = [
        ''.join(states[tok]
                for tok in top_scoring[0, i, :])
        for i in range(beam_size)
    ]

    expected = ['START-AABBA-END', 'START-BBAAB-END']
    np.testing.assert_array_equal(expected, top_scoring_strings)

  def test_beam_search_force_decode_prefix(self):
    beam_size = 2
    batch_logits = np.array([[-1e7, -1e10, -0.1, -0.9, -1e1, -2e1, -3e1, -4e1],
                             [-1e7, -1e10, -0.9, -0.1, -1e1, -2e1, -3e1, -4e1]],
                            dtype=np.float32)

    def token_to_logits(ids, cache):  # pylint: disable=unused-argument
      # Use id 2 then 3 for batch element 0 and id 3 then 2 for element 1.
      logits = np.repeat(
          np.expand_dims(batch_logits, axis=1), [beam_size], axis=1)
      logits = decoding.flatten_beam_dim(logits)
      return logits, {}

    # batch element 0 has length 1 and element 1 has length 2.
    inputs = np.array([[0, 7, 0, 0, 0], [0, 4, 5, 0, 0]], dtype=np.int32)
    beam_search_sequences, decoding_scores = decoding.beam_search(
        inputs, {}, token_to_logits, EOS_ID, num_decodes=beam_size, alpha=0)

    # Prefixes are forced depending on inputs.
    # Beam search sequences and corresponding scores are in reverse order.
    self.assertTrue(np.all(np.diff(decoding_scores) >= 0))
    expected = np.array([[[7, 3, 2, 2, 2], [7, 2, 2, 2, 2]],
                         [[4, 5, 2, 3, 3], [4, 5, 3, 3, 3]]])
    np.testing.assert_array_equal(expected, beam_search_sequences)

    expected_scores = []
    for batch, logits in zip(expected, batch_logits):
      beam_expected_scores = []
      for beam in batch:
        log_probs = jax.nn.log_softmax(logits)
        beam_scores = [log_probs[token] for token in beam]
        beam_expected_scores.append(sum(beam_scores))
      expected_scores.append(beam_expected_scores)
    np.testing.assert_allclose(expected_scores, decoding_scores, atol=1e-5)

  def test_beam_search_force_decode_unlikely_prompt(self):
    """When the forced prompts are close to NEG_INF, prob is kept non-zero."""
    beam_size = 2

    # Id 2 and 3 are non-zero probability, with 3 always chosen.
    batch_logits = np.array([[-1e7, -1e10, -0.9, -0.1, -1e10, -1e10, -1e10]],
                            dtype=np.float32)

    def token_to_logits(ids, cache):  # pylint: disable=unused-argument
      logits = np.repeat(
          np.expand_dims(batch_logits, axis=1), [beam_size], axis=1)
      logits = decoding.flatten_beam_dim(logits)
      return logits, {}

    # batch element 0 has length 3.
    inputs = np.array([[0, 4, 5, 6, 0]], dtype=np.int32)
    beam_search_sequences, decoding_scores = decoding.beam_search(
        inputs, {}, token_to_logits, EOS_ID, num_decodes=beam_size)

    # Prefixes are forced depending on inputs.
    # Beam search sequences and corresponding scores are in reverse order.
    self.assertTrue(np.all(np.diff(decoding_scores) >= 0))
    expected = np.array([[[4, 5, 6, 2, 3], [4, 5, 6, 3, 3]]])
    np.testing.assert_array_equal(expected, beam_search_sequences)

    batch, logits = expected[0], batch_logits[0]
    beam_expected_scores = []
    for beam in batch:
      log_probs = jax.nn.log_softmax(logits)
      beam_scores = [log_probs[token] for token in beam]
      final_score = 0

      # Verify that the beam scores would have become NEG_INF.
      self.assertLess(sum(beam_scores), -1e7)

      # Verify that the beam score is capped only while forcing the prompt.
      for force_prompt, score in zip(
          np.concatenate([inputs[0][1:], [0]]), beam_scores):
        final_score += score
        if force_prompt != 0:
          final_score = max(-1e6, final_score)

      beam_expected_scores.append(final_score)
    np.testing.assert_allclose([beam_expected_scores],
                               decoding_scores,
                               atol=1e-5)

  def test_beam_search_force_decode_no_prefix(self):
    beam_size = 2

    def token_to_logits(ids, cache):  # pylint: disable=unused-argument
      # Use id 2 then 3 for batch element 0 and id 3 then 2 for element 1.
      logits = np.repeat(
          np.expand_dims(
              np.array([[-1e7, -1e10, -0.1, -0.9], [-1e7, -1e10, -0.9, -0.1]],
                       dtype=np.float32),
              axis=1), [beam_size],
          axis=1)
      logits = decoding.flatten_beam_dim(logits)
      return logits, {}

    # No prefix is passed.
    inputs = np.array([[0, 0, 0, 0, 0], [0, 0, 0, 0, 0]], dtype=np.int32)
    beam_search_sequences, decoding_scores = decoding.beam_search(
        inputs, {}, token_to_logits, EOS_ID, num_decodes=beam_size)

    # Prefixes are forced depending on inputs.
    # Beam search sequences and corresponding scores are in reverse order.
    self.assertTrue(np.all(np.diff(decoding_scores) >= 0))
    expected = np.array([[[3, 2, 2, 2, 2], [2, 2, 2, 2, 2]],
                         [[2, 3, 3, 3, 3], [3, 3, 3, 3, 3]]])
    np.testing.assert_array_equal(expected, beam_search_sequences)


if __name__ == '__main__':
  absltest.main()
