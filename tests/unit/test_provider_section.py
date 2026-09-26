"""ProviderSection: the provider sections both settings windows embed (#99).

A real widget under offscreen Qt. The section edits a private copy of the
profiles, so these tests never need a dialog or FreeCAD.
"""

import dataclasses
import os
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

try:
    from PySide6 import QtWidgets
except ImportError:
    try:
        from PySide2 import QtWidgets
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.config import AppConfig, PROVIDER_PRESETS, ProviderConfig  # noqa: E402
from freecad_ai.llm.providers import get_provider_names  # noqa: E402
import freecad_ai.ui.provider_section as ps_mod  # noqa: E402
from freecad_ai.ui.provider_section import ProviderSection  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def _cfg():
    cfg = AppConfig()
    cfg.profiles = {
        "cloud": ProviderConfig(
            name="anthropic", model="claude-sonnet-4-6", api_key="k1",
            params={"temperature": 0.2}, vision_detected=True,
            tools_detected=False, thinking_detected=True),
        "local": ProviderConfig(
            name="ollama", model="qwen3:8b",
            base_url="http://localhost:11434/v1", vision_override=False),
    }
    cfg.active_profile = "cloud"
    cfg.utility_profiles = {"rerank": "local"}
    return cfg


@pytest.fixture
def section(qapp):
    s = ProviderSection()
    yield s
    s.deleteLater()


def _as_dicts(profiles):
    return {k: dataclasses.asdict(v) for k, v in profiles.items()}


class TestRoundTrip:
    def test_load_then_apply_is_lossless(self, section):
        """Including fields the page never shows: params, the three
        *_detected probe results and vision_override."""
        cfg = _cfg()
        section.load(cfg)
        out = AppConfig()
        section.apply_to(out)
        assert _as_dicts(out.profiles) == _as_dicts(cfg.profiles)
        assert out.active_profile == "cloud"
        assert out.utility_profiles == {"rerank": "local"}

    def test_edits_stay_out_of_the_config_until_apply(self, section):
        cfg = _cfg()
        section.load(cfg)
        section.base_url_edit.setText("http://elsewhere/v1")
        section.commit()
        assert cfg.profiles["cloud"].base_url != "http://elsewhere/v1"

    def test_apply_does_not_alias_the_scratch_copy(self, section):
        section.load(_cfg())
        out = AppConfig()
        section.apply_to(out)
        out.profiles["cloud"].model = "mutated"
        assert section.profiles()["cloud"].model == "claude-sonnet-4-6"

    def test_load_shows_the_active_profile(self, section):
        section.load(_cfg())
        assert section.current_label() == "cloud"
        assert section.model_edit.text() == "claude-sonnet-4-6"

    def test_load_can_show_a_named_profile(self, section):
        section.load(_cfg(), label="local")
        assert section.current_label() == "local"
        assert section.profile_combo.currentData() == "local"

    def test_an_unknown_label_falls_back_to_active(self, section):
        section.load(_cfg(), label="gone")
        assert section.current_label() == "cloud"

    def test_the_provider_list_is_the_registry(self, section):
        """#97 cannot come back: the list is get_provider_names(), whole."""
        assert section.provider_combo.count() == len(get_provider_names())


class TestDirty:
    def test_clean_before_load(self, section):
        assert section.is_dirty() is False

    def test_clean_after_load(self, section):
        section.load(_cfg())
        assert section.is_dirty() is False

    def test_browsing_profiles_is_clean(self, section):
        section.load(_cfg())
        section.profile_combo.setCurrentIndex(
            section.profile_combo.findData("local"))
        assert section.is_dirty() is False

    def test_a_field_edit_is_dirty(self, section):
        section.load(_cfg())
        section.base_url_edit.setText("http://elsewhere/v1")
        assert section.is_dirty() is True

    def test_a_reverted_edit_is_clean(self, section):
        section.load(_cfg())
        original = section.base_url_edit.text()
        section.base_url_edit.setText("http://elsewhere/v1")
        assert section.is_dirty() is True
        section.base_url_edit.setText(original)
        assert section.is_dirty() is False

    def test_new_is_dirty(self, section):
        section.load(_cfg())
        section._on_profile_add()
        assert section.is_dirty() is True

    def test_rename_is_dirty(self, section, monkeypatch):
        section.load(_cfg())
        monkeypatch.setattr(ps_mod, "QInputDialog", types.SimpleNamespace(
            getText=lambda *a, **k: ("renamed", True)))
        section._on_profile_rename()
        assert "renamed" in section.profiles()
        assert section.is_dirty() is True

    def test_delete_is_dirty(self, section, monkeypatch):
        section.load(_cfg(), label="local")
        monkeypatch.setattr(ps_mod, "QMessageBox", types.SimpleNamespace(
            question=lambda *a, **k: "yes", Yes="yes",
            warning=lambda *a, **k: None))
        section._on_profile_delete()
        assert "local" not in section.profiles()
        assert section.is_dirty() is True

    def test_ticking_use_for_chat_is_dirty(self, section):
        section.load(_cfg(), label="local")
        section.profile_active_check.setChecked(True)
        assert section.active_label() == "local"
        assert section.is_dirty() is True

    def test_a_utility_combo_is_dirty(self, section):
        section.load(_cfg())
        combo = section.utility_combos["compaction"]
        combo.setCurrentIndex(combo.findData("local"))
        assert section.is_dirty() is True


class TestUnknownProvider:
    """Review Focus 1: a provider the combo cannot show must survive."""

    def _cfg(self):
        cfg = _cfg()
        cfg.profiles["cloud"].name = "futurevendor"
        return cfg

    def test_an_untouched_section_is_clean(self, section):
        section.load(self._cfg())
        assert section.is_dirty() is False

    def test_apply_keeps_the_name(self, section):
        section.load(self._cfg())
        section.base_url_edit.setText("http://edited/v1")
        out = AppConfig()
        section.apply_to(out)
        assert out.profiles["cloud"].name == "futurevendor"
        assert out.profiles["cloud"].base_url == "http://edited/v1"

    def test_the_probe_results_survive(self, section):
        section.load(self._cfg())
        out = AppConfig()
        section.apply_to(out)
        assert out.profiles["cloud"].vision_detected is True

    def test_a_real_choice_replaces_it(self, section):
        section.load(self._cfg())
        section.provider_combo.setCurrentIndex(
            get_provider_names().index("ollama"))
        out = AppConfig()
        section.apply_to(out)
        assert out.profiles["cloud"].name == "ollama"


class TestProbeResult:
    def test_it_lands_on_the_named_profile(self, section):
        section.load(_cfg())
        section.set_probe_result("local", vision=True, tools=True)
        prof = section.profiles()["local"]
        assert (prof.vision_detected, prof.tools_detected) == (True, True)
        assert prof.thinking_detected is None

    def test_false_is_recorded_not_skipped(self, section):
        section.load(_cfg())
        section.set_probe_result("cloud", vision=False)
        assert section.profiles()["cloud"].vision_detected is False

    def test_an_unknown_label_is_ignored(self, section):
        section.load(_cfg())
        section.set_probe_result("gone", vision=True)

    def test_the_vision_row_refreshes_for_the_shown_profile(self, section):
        cfg = _cfg()
        cfg.profiles["cloud"].vision_detected = None
        section.load(cfg)
        assert "not tested" in section._vision_status_label.text()
        section.set_probe_result("cloud", vision=True)
        assert "auto-detected" in section._vision_status_label.text()

    def test_another_profiles_result_leaves_the_row_alone(self, section):
        section.load(_cfg())
        before = section._vision_status_label.text()
        section.set_probe_result("local", vision=True)
        assert section._vision_status_label.text() == before


class TestSignals:
    def _record(self, signal):
        seen = []
        signal.connect(lambda *a: seen.append(a))
        return seen

    def test_profile_shown_fires_on_load(self, section):
        seen = self._record(section.profileShown)
        section.load(_cfg())
        assert seen[-1][0].model == "claude-sonnet-4-6"

    def test_about_to_commit_fires_on_apply_with_the_shown_profile(self, section):
        section.load(_cfg())
        seen = self._record(section.aboutToCommit)
        section.apply_to(AppConfig())
        assert seen and seen[-1][0] is not None

    def test_about_to_commit_can_write_into_the_profile(self, section):
        """The dialog writes its params table here; the write must land."""
        section.load(_cfg())
        section.aboutToCommit.connect(
            lambda prof: setattr(prof, "params", {"top_p": 0.9}))
        out = AppConfig()
        section.apply_to(out)
        assert out.profiles["cloud"].params == {"top_p": 0.9}

    def test_preset_applied_fires_on_a_user_switch(self, section):
        section.load(_cfg())
        seen = self._record(section.presetApplied)
        section.provider_combo.setCurrentIndex(
            get_provider_names().index("ollama"))
        assert seen == [(PROVIDER_PRESETS["ollama"],)]

    def test_preset_applied_does_not_fire_on_load(self, section):
        seen = self._record(section.presetApplied)
        section.load(_cfg())
        section.load(_cfg(), label="local")
        assert seen == []

    def test_model_changed_carries_the_stripped_text(self, section):
        section.load(_cfg())
        seen = self._record(section.modelChanged)
        section.model_edit.setText("  other-model ")
        section.model_edit.editingFinished.emit()
        assert seen == [("other-model",)]
