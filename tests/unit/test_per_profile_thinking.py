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
