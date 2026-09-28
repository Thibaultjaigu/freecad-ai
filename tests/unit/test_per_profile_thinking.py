"""Per-profile thinking (#108): stored on the profile, resolved call site >
profile > global, sent to the vendor verbatim."""

import logging

import pytest

from freecad_ai.config import AppConfig, ProviderConfig, _profile_from_dict


class TestStorage:
    def test_defaults_to_use_global(self):
        assert ProviderConfig().thinking is None

    def test_a_value_round_trips(self):
        assert _profile_from_dict({"thinking": "xhigh"}).thinking == "xhigh"

    def test_old_configs_without_the_key_use_global(self):
        assert _profile_from_dict({"model": "m"}).thinking is None

    def test_surrounding_whitespace_is_stripped(self):
        assert _profile_from_dict({"thinking": " low "}).thinking == "low"

    def test_case_is_kept(self):
        assert _profile_from_dict({"thinking": "Low"}).thinking == "Low"

    def test_blank_means_use_global(self):
        assert _profile_from_dict({"thinking": "   "}).thinking is None

    def test_a_non_string_means_use_global_with_a_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            prof = _profile_from_dict({"thinking": 5})
        assert prof.thinking is None
        assert "thinking" in caplog.text


from freecad_ai.llm.client import LLMClient, _thinking_kind  # noqa: E402

LEGACY_ID = "claude-sonnet-4-6"
CURRENT_ID = "claude-sonnet-5"


def _llm(provider, model, thinking, model_params=None):
    return LLMClient(
        provider_name=provider, base_url="https://example.invalid",
        api_key="k", model=model, max_tokens=8000, temperature=0.3,
        thinking=thinking, model_params=model_params)


class TestKind:
    @pytest.mark.parametrize("value,kind", [
        ("off", "off"), ("on", "preset"), ("extended", "preset"),
        ("default", "default"), ("8000", "budget"), ("low", "level"),
        ("none", "level"), ("Low", "level"), ("Off", "level"),
        ("²", "level")])
    def test_kinds(self, value, kind):
        assert _thinking_kind(value) == kind


def _ant(model, thinking, **kw):
    c = _llm("anthropic", model, thinking, **kw)
    return c._anthropic_body([], "sys", stream=True), c._anthropic_headers()


class TestAnthropic:
    @pytest.mark.parametrize("model", [LEGACY_ID, CURRENT_ID])
    def test_a_level_is_adaptive_effort_verbatim(self, model):
        body, headers = _ant(model, "xhigh")
        assert body["thinking"] == {"type": "adaptive"}
        assert body["output_config"] == {"effort": "xhigh"}
        assert "temperature" not in body
        assert "anthropic-beta" not in headers

    def test_a_level_keeps_a_temperature_row(self):
        body, _ = _ant(CURRENT_ID, "low", model_params={"temperature": 0.7})
        assert body["temperature"] == 0.7

    @pytest.mark.parametrize("model", [LEGACY_ID, CURRENT_ID])
    def test_a_number_is_an_enabled_budget(self, model):
        body, _ = _ant(model, "12000")
        assert body["thinking"] == {"type": "enabled",
                                    "budget_tokens": 12000}
        assert body["temperature"] == 1
        assert "output_config" not in body

    def test_a_number_sends_the_beta_header_on_legacy_ids_only(self):
        assert "anthropic-beta" in _ant(LEGACY_ID, "12000")[1]
        assert "anthropic-beta" not in _ant(CURRENT_ID, "12000")[1]

    @pytest.mark.parametrize("model", [LEGACY_ID, CURRENT_ID])
    def test_default_sends_what_off_sends(self, model):
        assert _ant(model, "default") == _ant(model, "off")

    @pytest.mark.parametrize("model", [LEGACY_ID, CURRENT_ID])
    @pytest.mark.parametrize("thinking", ["off", "on", "extended"])
    def test_global_values_are_unchanged(self, model, thinking):
        """Pinned to #107's shapes: these three must not move."""
        body, headers = _ant(model, thinking)
        if thinking == "off":
            assert "thinking" not in body
        elif model == LEGACY_ID:
            assert body["thinking"]["type"] == "enabled"
            assert body["temperature"] == 1
            assert "anthropic-beta" in headers
        else:
            assert body["thinking"] == {"type": "adaptive"}
            assert body["output_config"]["effort"] == (
                "medium" if thinking == "on" else "high")
