"""Test Connection must not stage its inputs in the live config (#76).

``_save_temp`` pushed five Behavior-tab widget values into the ``get_config()``
singleton so ``_TestConnectionThread.run()`` could read two of them back off
it. Cancel is a bare ``reject()`` with no snapshot, so the writes outlived the
dialog, and the two unrelated ``save_current_config()`` calls in
``chat_widget.py`` (dock-layout change, Plan/Act toggle) then flushed them to
disk — settings the user explicitly cancelled, persisted.

The fix hands the probe its inputs directly instead of staging them in global
state, so there is nothing left to roll back. The other three values
(``context_window``, ``max_tool_turns``, ``system_prompt_override``) were
never read by the probe at all; they simply stop being written.

The fake self carries only what ``_test_connection`` touches, so no Qt dialog
has to be constructed. Since #101, ``_test_connection`` lives on
``ProviderPage`` and reads ``max_tokens``/``thinking``/``temperature`` and
``provider_keys`` off ``get_config()`` rather than a load-time snapshot,
because the Behavior-tab widgets that used to carry the first two now live
on a different page (a cross-page link Decision 3 rejects).
"""

import dataclasses
from unittest.mock import MagicMock

import pytest

# settings_dialog imports through ui/compat.py, which needs Qt.
try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.config import AppConfig, ProviderConfig  # noqa: E402
from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402
from freecad_ai.ui.settings_pages.provider_page import ProviderPage  # noqa: E402


def _cfg():
    cfg = AppConfig()
    cfg.profiles = {
        "cloud": ProviderConfig(name="anthropic", model="claude-sonnet-4-6"),
    }
    cfg.active_profile = "cloud"
    cfg.provider_keys = {"anthropic": "vendor-default"}
    return cfg


def _fake(cfg):
    fake = MagicMock()
    fake._cfg = cfg
    # The section's own copy of the shown profile, not cfg's: committing
    # into it is the only write Test Connection may make (#76, #99).
    fake.section.current_label.return_value = "cloud"
    fake.section.current_profile.return_value = ProviderConfig(
        name="anthropic", base_url="https://api.anthropic.com",
        api_key="typed-key", model="claude-sonnet-4-6")
    return fake


def _run(monkeypatch, cfg, fake=None):
    """Drive _test_connection, returning the kwargs the probe thread got."""
    captured = {}

    def fake_thread(*args, **kwargs):
        captured.update(kwargs)
        captured["positional"] = args
        return MagicMock()

    monkeypatch.setattr(
        "freecad_ai.ui.settings_pages.provider_page._TestConnectionThread",
        fake_thread)
    # get_config() is what _test_connection reads for max_tokens/thinking/
    # temperature/provider_keys since #101 — point it at the same object so
    # a leak is visible on cfg either way.
    monkeypatch.setattr(
        "freecad_ai.ui.settings_pages.provider_page.get_config", lambda: cfg)

    ProviderPage._test_connection(fake or _fake(cfg))
    return captured


class TestTheLiveConfigIsLeftAlone:
    """The bug: five Behavior-tab values written into the singleton, with no
    rollback on Cancel."""

    def test_max_tokens_is_not_written(self, monkeypatch):
        cfg = _cfg()
        _run(monkeypatch, cfg)
        assert cfg.max_tokens == AppConfig().max_tokens

    def test_context_window_is_not_written(self, monkeypatch):
        cfg = _cfg()
        _run(monkeypatch, cfg)
        assert cfg.context_window == AppConfig().context_window

    def test_max_tool_turns_is_not_written(self, monkeypatch):
        cfg = _cfg()
        _run(monkeypatch, cfg)
        assert cfg.max_tool_turns == AppConfig().max_tool_turns

    def test_thinking_is_not_written(self, monkeypatch):
        cfg = _cfg()
        _run(monkeypatch, cfg)
        assert cfg.thinking == AppConfig().thinking

    def test_system_prompt_override_is_not_written(self, monkeypatch):
        cfg = _cfg()
        _run(monkeypatch, cfg)
        assert cfg.system_prompt_override == AppConfig().system_prompt_override

    def test_the_whole_config_is_untouched(self, monkeypatch):
        """Catches any field the five assertions above don't enumerate."""
        cfg = _cfg()
        before = dataclasses.asdict(cfg)

        _run(monkeypatch, cfg)

        assert dataclasses.asdict(cfg) == before


class TestTheProbeStillUsesTheEditedValues:
    """max_tokens and thinking now read straight off ``get_config()`` (#101's
    Ruling 1) — the Behavior-tab widgets that used to carry them live on a
    different page now, so reading them from ProviderPage would be a
    cross-page link. temperature is unaffected: it has always come from the
    saved config, the same way, below."""

    def test_max_tokens_comes_from_the_saved_config(self, monkeypatch):
        cfg = _cfg()
        cfg.max_tokens = 1234
        captured = _run(monkeypatch, cfg)
        assert captured["max_tokens"] == 1234

    def test_thinking_comes_from_the_saved_config(self, monkeypatch):
        cfg = _cfg()
        cfg.thinking = "extended"
        captured = _run(monkeypatch, cfg)
        assert captured["thinking"] == "extended"

    def test_temperature_comes_from_the_config(self, monkeypatch):
        """_save_temp never wrote temperature, so the saved value is what the
        probe has always used. The model-params table still outranks it inside
        LLMClient."""
        cfg = _cfg()
        cfg.temperature = 0.42
        captured = _run(monkeypatch, cfg)
        assert captured["temperature"] == 0.42


class TestTheProbeCommitsTheSectionFirst:
    """#99: the probe reads the committed profile, so the visible fields
    have to be committed into it before it is read."""

    def test_commit_comes_before_the_profile_is_read(self, monkeypatch):
        cfg = _cfg()
        fake = _fake(cfg)
        order = []
        prof = fake.section.current_profile.return_value
        fake.section.commit.side_effect = (
            lambda: order.append("commit"))
        fake.section.current_profile.side_effect = (
            lambda: order.append("read") or prof)
        monkeypatch.setattr(
            "freecad_ai.ui.settings_pages.provider_page._TestConnectionThread",
            lambda *a, **k: MagicMock())
        monkeypatch.setattr(
            "freecad_ai.ui.settings_pages.provider_page.get_config",
            lambda: cfg)
        ProviderPage._test_connection(fake)
        assert order[:2] == ["commit", "read"]


class TestTheStagingHelperIsGone:
    """A rollback-on-Cancel fix would have left _save_temp in place and the
    hazard one forgotten caller away. Pin that it has no way back."""

    def test_save_temp_no_longer_exists(self):
        assert not hasattr(SettingsDialog, "_save_temp")


class TestTheProbeSendsTheProfilesThinking:
    """#108: a level the model refuses fails here, not mid-chat."""

    def test_profile_value_wins(self, monkeypatch):
        cfg = _cfg()
        cfg.thinking = "extended"
        fake = _fake(cfg)
        fake.section.current_profile.return_value.thinking = "xhigh"
        assert _run(monkeypatch, cfg, fake)["thinking"] == "xhigh"
