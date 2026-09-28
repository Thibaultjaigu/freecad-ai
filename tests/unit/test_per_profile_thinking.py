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


TOOLS = [{"type": "function", "function": {"name": "t", "parameters": {}}}]


def _oai(provider, thinking, tools=None, model_params=None):
    c = _llm(provider, "some-model", thinking, model_params=model_params)
    return c._openai_body([], "sys", stream=False, tools=tools)


def _system(body):
    return body["messages"][0]["content"]


class TestOpenAIStyle:
    @pytest.mark.parametrize("provider", ["openai", "ollama"])
    @pytest.mark.parametrize("tools", [None, TOOLS])
    def test_a_level_is_sent_verbatim_even_with_tools(self, provider, tools):
        assert _oai(provider, "xhigh", tools)["reasoning_effort"] == "xhigh"

    def test_none_is_a_level_like_any_other(self):
        assert _oai("openai", "none")["reasoning_effort"] == "none"

    def test_a_number_is_sent_as_typed(self):
        assert _oai("openai", "8000")["reasoning_effort"] == "8000"

    @pytest.mark.parametrize("value", ["xhigh", "8000", "default"])
    def test_verbatim_values_add_no_ollama_tag(self, value):
        system = _system(_oai("ollama", value))
        assert "/think" not in system and "/no_think" not in system

    @pytest.mark.parametrize("provider", ["openai", "ollama"])
    def test_default_sends_no_reasoning_effort(self, provider):
        assert "reasoning_effort" not in _oai(provider, "default")

    def test_global_on_with_tools_still_sends_no_effort(self):
        assert "reasoning_effort" not in _oai("openai", "on", TOOLS)

    @pytest.mark.parametrize("thinking,effort",
                             [("on", "medium"), ("extended", "high")])
    def test_global_on_without_tools_is_unchanged(self, thinking, effort):
        assert _oai("openai", thinking)["reasoning_effort"] == effort

    def test_ollama_tags_for_global_values_are_unchanged(self):
        assert _system(_oai("ollama", "off")).endswith("\n/no_think")
        assert _system(_oai("ollama", "on")).endswith("\n/think")
        assert "reasoning_effort" not in _oai("ollama", "off")


from freecad_ai.llm.client import create_client  # noqa: E402


def _cfg_two():
    cfg = AppConfig()
    cfg.thinking = "on"
    cfg.profiles = {
        "chat": ProviderConfig(name="anthropic", model=CURRENT_ID),
        "local": ProviderConfig(name="ollama", model="qwen3:8b",
                                base_url="http://localhost:11434/v1",
                                thinking="none"),
    }
    cfg.active_profile = "chat"
    return cfg


class TestResolution:
    def test_unset_profile_uses_global(self):
        assert create_client(_cfg_two()).thinking == "on"

    def test_profile_value_beats_global(self):
        cfg = _cfg_two()
        cfg.active_profile = "local"
        assert create_client(cfg).thinking == "none"

    def test_call_site_beats_profile(self):
        cfg = _cfg_two()
        cfg.active_profile = "local"
        assert create_client(cfg, thinking="off").thinking == "off"

    def test_a_utility_profile_uses_its_own_value(self):
        cfg = _cfg_two()
        cfg.utility_profiles["compaction"] = "local"
        assert create_client(cfg, "compaction").thinking == "none"

    def test_a_fallback_candidate_uses_its_own_value(self):
        assert create_client(_cfg_two(), profile="local").thinking == "none"

    def test_the_reranker_still_forces_off(self):
        cfg = _cfg_two()
        cfg.utility_profiles["rerank"] = "local"
        cfg.profiles["local"].thinking = "high"
        client = create_client(cfg, "rerank", max_tokens=1024,
                               thinking="off")
        assert client.thinking == "off"
