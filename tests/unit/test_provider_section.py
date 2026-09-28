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

    def test_mutating_the_working_copy_leaves_cfg_alone(self, section):
        """A shallow dict(cfg.profiles) would share the ProviderConfig
        objects, editing cfg through the back door. Pins the deepcopy."""
        cfg = _cfg()
        section.load(cfg)
        section.profiles()["local"].base_url = "http://mutated:9999/v1"
        assert cfg.profiles["local"].base_url != "http://mutated:9999/v1"

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

    def test_the_stored_name_is_shown_not_the_first_provider(self, section):
        section.load(self._cfg())
        combo = section.provider_combo
        assert combo.currentIndex() == combo.count() - 1
        assert combo.currentText() == "futurevendor (unknown provider)"
        assert combo.currentData() == "futurevendor"
        assert combo.count() == len(get_provider_names()) + 1

    def test_a_known_profile_removes_the_temporary_item(self, section):
        section.load(self._cfg())
        section.profile_combo.setCurrentIndex(
            section.profile_combo.findData("local"))
        assert section.provider_combo.count() == len(get_provider_names())
        assert section.provider_combo.currentText() != ""
        assert section.current_provider_name() == "ollama"

    def test_switching_back_and_forth_never_duplicates_it(self, section):
        section.load(self._cfg())
        for label in ("local", "cloud", "local", "cloud"):
            section.profile_combo.setCurrentIndex(
                section.profile_combo.findData(label))
        assert section.provider_combo.count() == len(get_provider_names()) + 1
        texts = [section.provider_combo.itemText(i)
                 for i in range(section.provider_combo.count())]
        assert texts.count("futurevendor (unknown provider)") == 1

    def test_picking_the_first_provider_really_switches(self, section):
        """The old stand-in *was* index 0, so picking Anthropic was a no-op."""
        section.load(self._cfg())
        first = get_provider_names()[0]
        section.provider_combo.setCurrentIndex(0)
        out = AppConfig()
        section.apply_to(out)
        assert out.profiles["cloud"].name == first
        assert section.provider_combo.count() == len(get_provider_names())

    def test_the_real_pick_applies_the_preset(self, section):
        section.load(self._cfg())
        section.provider_combo.setCurrentIndex(
            get_provider_names().index("ollama"))
        assert section.base_url_edit.text() == \
            PROVIDER_PRESETS["ollama"]["base_url"]

    def test_current_provider_name_is_empty_while_unknown(self, section):
        section.load(self._cfg())
        assert section.current_provider_name() == ""


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

    def test_it_stays_out_of_the_config_until_apply(self, section):
        """A probe result lands in the section's working copy, like every
        other profile field, and reaches the real config on OK."""
        cfg = _cfg()
        section.load(cfg)
        section.set_probe_result("local", vision=True)
        assert section.profiles()["local"].vision_detected is True
        assert cfg.profiles["local"].vision_detected is None

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


class TestRerankDefaults:
    """#10 at save time: a switch to a preset with default_rerank sets the
    reranker only while the live config is still at factory defaults."""

    GH = PROVIDER_PRESETS["github"]["default_rerank"]

    def _switch_to_github(self, section):
        section.load(_cfg())
        section.provider_combo.setCurrentIndex(
            get_provider_names().index("github"))

    def test_untouched_reranker_takes_the_preset(self, section):
        self._switch_to_github(section)
        out = AppConfig()
        section.apply_to(out)
        assert out.rerank_method == self.GH["method"]
        assert out.rerank_top_n == self.GH["top_n"]

    def test_an_explicit_choice_survives(self, section):
        self._switch_to_github(section)
        out = AppConfig()
        out.rerank_method = "llm"
        section.apply_to(out)
        assert out.rerank_method == "llm"
        assert out.rerank_top_n == 15

    def test_a_changed_top_n_alone_counts_as_explicit(self, section):
        self._switch_to_github(section)
        out = AppConfig()
        out.rerank_top_n = 20
        section.apply_to(out)
        assert (out.rerank_method, out.rerank_top_n) == ("off", 20)

    def test_no_switch_no_change(self, section):
        section.load(_cfg())
        out = AppConfig()
        section.apply_to(out)
        assert (out.rerank_method, out.rerank_top_n) == ("off", 15)

    def test_last_switch_wins(self, section):
        self._switch_to_github(section)
        section.provider_combo.setCurrentIndex(
            get_provider_names().index("ollama"))
        out = AppConfig()
        section.apply_to(out)
        assert (out.rerank_method, out.rerank_top_n) == ("off", 15)

    def test_load_clears_the_record(self, section):
        self._switch_to_github(section)
        section.load(_cfg())
        out = AppConfig()
        section.apply_to(out)
        assert (out.rerank_method, out.rerank_top_n) == ("off", 15)

    def _switch_local_to_github(self, section):
        section.load(_cfg(), label="local")        # a non-active profile
        section.provider_combo.setCurrentIndex(
            get_provider_names().index("github"))
        assert section._pending_rerank == self.GH

    def test_deleting_the_switched_profile_drops_the_record(
            self, section, monkeypatch):
        self._switch_local_to_github(section)
        monkeypatch.setattr(ps_mod.QMessageBox, "question",
                            lambda *a, **k: ps_mod.QMessageBox.Yes)
        section._on_profile_delete()
        assert "local" not in section.profiles()
        out = AppConfig()
        section.apply_to(out)
        assert (out.rerank_method, out.rerank_top_n) == ("off", 15)

    def test_renaming_the_switched_profile_keeps_the_record(
            self, section, monkeypatch):
        self._switch_local_to_github(section)
        monkeypatch.setattr(ps_mod.QInputDialog, "getText",
                            lambda *a, **k: ("gh", True))
        section._on_profile_rename()
        assert "gh" in section.profiles()
        out = AppConfig()
        section.apply_to(out)
        assert (out.rerank_method, out.rerank_top_n) == (
            self.GH["method"], self.GH["top_n"])

    def test_apply_consumes_the_record(self, section):
        """A second Apply after the user set 'off' again must not re-apply."""
        self._switch_to_github(section)
        section.apply_to(AppConfig())
        out = AppConfig()
        section.apply_to(out)
        assert (out.rerank_method, out.rerank_top_n) == ("off", 15)


def _cw_cfg():
    cfg = _cfg()
    cfg.profiles["cloud"].context_window = 3000     # a hand edit below 4000
    return cfg


class TestCompactAbove:
    """#103: the per-profile compaction threshold."""

    def _label(self, section):
        form = section.compact_above_spin.parentWidget().layout()
        return form.labelForField(section.compact_above_spin).text()

    def test_label_special_text_and_range(self, section):
        spin = section.compact_above_spin
        assert self._label(section) == "Compact above:"
        assert spin.specialValueText() == "Use global"
        assert (spin.minimum(), spin.maximum()) == (0, 1000000)
        assert spin.singleStep() == 10000
        assert "Behavior" in spin.toolTip()

    def test_each_profile_shows_its_own_value(self, section):
        section.load(_cw_cfg())
        assert section.compact_above_spin.value() == 3000
        section.profile_combo.setCurrentIndex(
            section.profile_combo.findData("local"))
        assert section.compact_above_spin.value() == 0

    def test_untouched_load_is_clean(self, section):
        section.load(_cw_cfg())
        assert section.is_dirty() is False

    def test_a_hand_edit_survives_an_unrelated_edit(self, section):
        section.load(_cw_cfg())
        section.base_url_edit.setText("http://elsewhere/v1")
        out = AppConfig()
        section.apply_to(out)
        assert out.profiles["cloud"].context_window == 3000

    def test_zero_saves_none(self, section):
        section.load(_cw_cfg())
        section.compact_above_spin.setValue(0)
        out = AppConfig()
        section.apply_to(out)
        assert out.profiles["cloud"].context_window is None

    def test_a_typed_small_value_is_raised_to_4000(self, section):
        section.load(_cw_cfg())
        section.compact_above_spin.setValue(1500)
        out = AppConfig()
        section.apply_to(out)
        assert out.profiles["cloud"].context_window == 4000

    def test_a_typed_value_is_saved(self, section):
        section.load(_cw_cfg())
        section.compact_above_spin.setValue(150000)
        out = AppConfig()
        section.apply_to(out)
        assert out.profiles["cloud"].context_window == 150000

    def test_a_reverted_edit_is_clean(self, section):
        section.load(_cw_cfg())
        section.compact_above_spin.setValue(80000)
        assert section.is_dirty() is True
        section.compact_above_spin.setValue(3000)
        assert section.is_dirty() is False

    def test_an_edit_follows_its_profile_across_a_switch(self, section):
        section.load(_cw_cfg())
        section.compact_above_spin.setValue(80000)
        section.profile_combo.setCurrentIndex(
            section.profile_combo.findData("local"))
        assert section.compact_above_spin.value() == 0
        out = AppConfig()
        section.apply_to(out)
        assert out.profiles["cloud"].context_window == 80000
        assert out.profiles["local"].context_window is None


class TestFallbackList:
    """#104: the ordered fallback list, edited in the shared section."""

    def _loaded(self, section, fallback=()):
        c = _cfg()
        c.profiles["spare"] = ProviderConfig(name="openai", model="m3")
        c.fallback_profiles = list(fallback)
        section.load(c)
        return c

    def _pick(self, section, label):
        section.fallback_picker.setCurrentIndex(
            section.fallback_picker.findData(label))

    def _listed(self, section):
        return [section.fallback_list.item(i).text()
                for i in range(section.fallback_list.count())]

    def test_the_group_title_and_tooltip(self, section):
        assert section.fallback_group.title() == \
            "Fallback when the chat model can't be reached"
        assert section.fallback_group.toolTip() == (
            "Tried once, in this order, after the chat profile fails to "
            "answer. A paid profile in this list is used without asking.")

    def test_load_shows_the_list(self, section):
        self._loaded(section, ["spare", "local"])
        assert self._listed(section) == ["spare", "local"]

    def test_the_picker_offers_only_unlisted_profiles(self, section):
        self._loaded(section, ["local"])
        offered = [section.fallback_picker.itemData(i)
                   for i in range(section.fallback_picker.count())]
        assert "local" not in offered
        assert "spare" in offered

    def test_add_appends_and_dirties(self, section):
        self._loaded(section)
        self._pick(section, "spare")
        section.fallback_add_btn.click()
        assert self._listed(section) == ["spare"]
        assert section.is_dirty() is True

    def test_remove_drops_the_selected_row(self, section):
        self._loaded(section, ["spare", "local"])
        section.fallback_list.setCurrentRow(0)
        section.fallback_remove_btn.click()
        assert self._listed(section) == ["local"]

    def test_up_and_down_reorder(self, section):
        self._loaded(section, ["spare", "local"])
        section.fallback_list.setCurrentRow(1)
        section.fallback_up_btn.click()
        assert self._listed(section) == ["local", "spare"]
        assert section.fallback_list.currentRow() == 0
        section.fallback_down_btn.click()
        assert self._listed(section) == ["spare", "local"]

    def test_up_on_the_first_row_does_nothing(self, section):
        self._loaded(section, ["spare", "local"])
        section.fallback_list.setCurrentRow(0)
        section.fallback_up_btn.click()
        assert self._listed(section) == ["spare", "local"]

    def test_apply_writes_the_list(self, section):
        self._loaded(section, ["local"])
        self._pick(section, "spare")
        section.fallback_add_btn.click()
        target = _cfg()
        section.apply_to(target)
        assert target.fallback_profiles == ["local", "spare"]

    def test_an_untouched_load_is_clean(self, section):
        self._loaded(section, ["local"])
        assert section.is_dirty() is False

    def test_rename_carries_the_fallback_entry(self, section):
        self._loaded(section, ["local"])
        section._rename_profile("local", "box")
        section._refresh_profile_combo()
        assert self._listed(section) == ["box"]

    def test_delete_removes_the_fallback_entry(self, section):
        self._loaded(section, ["local", "spare"])
        section._delete_profile("local")
        section._refresh_profile_combo()
        assert self._listed(section) == ["spare"]


def _th_cfg():
    cfg = _cfg()
    cfg.thinking = "extended"
    cfg.profiles["cloud"].thinking = "xhigh"
    return cfg


class TestThinking:
    """#108: the per-profile thinking value."""

    def _items(self, section):
        c = section.thinking_combo
        return [c.itemText(i) for i in range(c.count())]

    def test_label_and_editable(self, section):
        form = section.thinking_combo.parentWidget().layout()
        assert form.labelForField(section.thinking_combo).text() == "Thinking:"
        assert section.thinking_combo.isEditable()

    def test_anthropic_suggestions(self, section):
        section.load(_th_cfg())
        assert self._items(section) == [
            "Use global", "Model default", "off",
            "low", "medium", "high", "xhigh", "max"]

    def test_openai_style_suggestions(self, section):
        section.load(_th_cfg())
        section.profile_combo.setCurrentIndex(
            section.profile_combo.findData("local"))
        assert self._items(section)[3:] == [
            "none", "minimal", "low", "medium", "high", "xhigh", "max"]

    def test_each_profile_shows_its_own_value(self, section):
        section.load(_th_cfg())
        assert section.thinking_combo.currentText() == "xhigh"
        section.profile_combo.setCurrentIndex(
            section.profile_combo.findData("local"))
        assert section.thinking_combo.currentText() == "Use global"

    def test_use_global_tooltip_names_the_global_value(self, section):
        section.load(_th_cfg())
        tip = section.thinking_combo.itemData(0, ps_mod.QtCore.Qt.ToolTipRole)
        assert "extended" in tip

    def test_untouched_load_is_clean(self, section):
        section.load(_th_cfg())
        assert not section.is_dirty()

    @pytest.mark.parametrize("typed,stored", [
        ("Use global", None), ("Model default", "default"), ("off", "off"),
        ("Low", "Low"), ("  high ", "high"), ("", None), ("9000", "9000")])
    def test_typed_value_is_stored(self, section, typed, stored):
        section.load(_th_cfg())
        section.thinking_combo.setEditText(typed)
        section.commit()
        assert section.profiles()["cloud"].thinking == stored

    def test_a_stored_unknown_value_round_trips(self, section):
        cfg = _th_cfg()
        cfg.profiles["cloud"].thinking = "ultra"
        section.load(cfg)
        assert section.thinking_combo.currentText() == "ultra"
        assert not section.is_dirty()

    def test_vendor_switch_keeps_the_value_and_swaps_suggestions(
            self, section):
        section.load(_th_cfg())
        section.provider_combo.setCurrentIndex(
            get_provider_names().index("ollama"))
        assert section.thinking_combo.currentText() == "xhigh"
        assert "none" in self._items(section)
        assert section.profiles()["cloud"].thinking == "xhigh"
