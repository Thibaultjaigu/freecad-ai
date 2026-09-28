"""Current Claude models reject what the Anthropic body used to send (#107).

Probed against the live API on 2026-09-28: Opus 4.7+, Sonnet 5, Opus 5,
Opus 5.5 and Fable 5.1 answer every request with a 400 --
``temperature`` is deprecated for them, and ``thinking.type: enabled``
is not supported ("Use thinking.type.adaptive and output_config.effort").
Haiku 4.5 is the mirror image: it rejects adaptive thinking and effort.
No one body serves both, so the old format is kept for a *closed* list
of legacy models -- Anthropic ships no new ones -- and everything else
named ``claude`` gets the current format.
"""

import pytest

from freecad_ai.llm.client import LLMClient


def _client(model, thinking="off", model_params=None):
    return LLMClient(
        provider_name="anthropic", base_url="https://example.invalid",
        api_key="k", model=model, max_tokens=8000, temperature=0.3,
        thinking=thinking, model_params=model_params)


def _body(model, **kw):
    return _client(model, **kw)._anthropic_body([], "sys", stream=True)


def _headers(model, **kw):
    return _client(model, **kw)._anthropic_headers()


CURRENT = ["claude-sonnet-5", "claude-opus-5-5", "claude-opus-5",
           "claude-opus-4-7", "claude-fable-5-1",
           "claude-opus-4-7-20260415"]
LEGACY = ["claude-sonnet-4-6", "claude-haiku-4-5", "claude-opus-4-1",
          "claude-sonnet-4-0", "claude-sonnet-4-20250514",
          "claude-sonnet-4-5-20250929", "claude-3-7-sonnet-20250219",
          "claude-3-5-haiku-latest", "claude-3-opus-20240229",
          "anthropic.claude-sonnet-4-5-20250929-v1:0"]


class TestCurrentModels:

    @pytest.mark.parametrize("model", CURRENT)
    def test_thinking_off_sends_neither_temperature_nor_thinking(self, model):
        body = _body(model)
        assert "temperature" not in body
        assert "thinking" not in body
        assert "output_config" not in body

    @pytest.mark.parametrize("thinking,effort",
                             [("on", "medium"), ("extended", "high")])
    @pytest.mark.parametrize("model", CURRENT)
    def test_thinking_is_adaptive_with_an_effort(self, model, thinking,
                                                 effort):
        body = _body(model, thinking=thinking)
        assert body["thinking"] == {"type": "adaptive"}
        assert body["output_config"] == {"effort": effort}
        assert "temperature" not in body

    @pytest.mark.parametrize("thinking", ["off", "on", "extended"])
    def test_no_interleaved_beta_header(self, thinking):
        assert "anthropic-beta" not in _headers("claude-sonnet-5",
                                                thinking=thinking)

    def test_an_explicit_temperature_row_is_still_sent(self):
        """The user set it on this profile; if the vendor refuses it,
        its error message says so -- dropping it silently would not."""
        body = _body("claude-sonnet-5", model_params={"temperature": 0.7})
        assert body["temperature"] == 0.7


class TestLegacyModelsAreUnchanged:

    @pytest.mark.parametrize("model", LEGACY)
    def test_thinking_off(self, model):
        body = _body(model)
        assert body["temperature"] == 0.3
        assert "thinking" not in body
        assert "output_config" not in body
        assert "anthropic-beta" not in _headers(model)

    @pytest.mark.parametrize("thinking,budget",
                             [("on", 4096), ("extended", 16384)])
    @pytest.mark.parametrize("model", LEGACY)
    def test_thinking_on(self, model, thinking, budget):
        body = _body(model, thinking=thinking)
        assert body["thinking"] == {"type": "enabled",
                                    "budget_tokens": budget}
        assert body["temperature"] == 1
        assert "output_config" not in body
        assert (_headers(model, thinking=thinking)["anthropic-beta"]
                == "interleaved-thinking-2025-05-14")


class TestNonClaudeModelsAreUnchanged:
    """An Anthropic-compatible gateway serving another vendor's model
    was never probed; it keeps exactly what it was sent before."""

    def test_thinking_off(self):
        assert _body("MiniMax-M2")["temperature"] == 0.3

    def test_thinking_on(self):
        body = _body("MiniMax-M2", thinking="on")
        assert body["thinking"]["type"] == "enabled"
        assert body["temperature"] == 1
