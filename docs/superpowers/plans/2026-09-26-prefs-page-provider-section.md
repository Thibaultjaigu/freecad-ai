# Preferences Page Shares the Provider Sections — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Edit → Preferences → FreeCAD AI shows the Settings dialog's *LLM Provider* and *Utility models* sections. Both windows use one shared widget. `config.json` becomes the only store.

**Architecture:** A new `ProviderSection(QWidget)` takes over the two group boxes, the profile scratch copy, and the methods that work on it from `SettingsDialog`. The dialog embeds the section and keeps its extra features (params table, reranker defaults) through four signals. A new Python preferences page, `FreeCADAIPrefsPage`, embeds the same section, saves only when something changed, and notifies config listeners. The FreeCAD parameter-store bridge is retired behind a one-time migration.

**Tech Stack:** Python 3.11, PySide6/PySide2 via `freecad_ai/ui/compat.py`, FreeCAD 1.1.1 preference-page API, pytest with offscreen Qt.

**Spec:** `docs/superpowers/specs/2026-09-26-prefs-page-provider-section-design.md`. Read it before starting any task.

## Global Constraints

- Branch: `feat/99-prefs-provider-section`. Every commit is conventional, `type(scope): text (#99)`, and ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Import Qt only through `freecad_ai/ui/compat.py`, never PySide2 or PySide6 directly. Use flat enums (`QLineEdit.Password`, `QMessageBox.Yes`), because PySide2 accepts only the flat form.
- No new third-party dependencies.
- Moved user-visible strings keep their `translate("SettingsDialog", ...)` / `QT_TRANSLATE_NOOP("SettingsDialog", ...)` context, so existing translations still apply.
- Full unit suite before every commit: `env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py`. Baseline on this branch: **1838 passed**. A bare `pytest` crashes here because the shell's `PYTHONPATH` shadows the venv's pluggy, and `test_document_attach.py` Qt-segfaults on clean master too.
- Qt widget tests put `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` before any Qt import, as `tests/unit/test_settings_dialog_geometry.py` does.
- Live FreeCAD probes run with `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `FREECAD_AI_CONFIG_DIR` all under the session scratchpad. Never read or write the maintainer's real FreeCAD or FreeCAD AI configuration.
- Moved methods keep their behavior. The only deliberate changes are these four; each gets a test:
  1. A profile's unknown provider name survives (Review Focus 1).
  2. Test Connection commits the visible fields before probing.
  3. The config-changed notification replaces the chat panel's post-dialog refresh.
  4. The Settings dialog keeps a utility mapping that points at a missing profile instead of dropping it on OK. The resolver already tolerates such mappings.
- Remove only code that your own changes leave unused (imports, Qt aliases, helpers). Don't remove other dead code.

## Review Focus

These five situations follow from the spec but no spec test covers them. Each has a test in the task named in brackets.

1. **A profile whose provider isn't in `get_provider_names()`** (a hand edit, or a config from a newer version). The combo shows it as its first entry. An untouched page must stay clean, and saving must keep the original name, or every OK in Preferences rewrites it to `anthropic`: #97 again. [Task 2]
2. **`max_tokens` above the old page's 32768 cap** (e.g. 65536). It must survive when the user saves an unrelated change on the page. The old `.ui` spin box clamped at 32768. [Task 5]
3. **Edit on the page, then Cancel.** FreeCAD calls no `saveSettings()`. The live config must be untouched, and the next `loadSettings()` shows the saved values. [Task 5]
4. **Apply while editing a non-active profile.** Re-baselining after the save must leave the page on that profile, not jump to the active one. [Task 5]
5. **Migration save fails** (`OSError`, e.g. disk full or read-only). `load_config()` must still return a config with the migrated values applied, and must keep the parameter group so the migration runs again next start. FreeCAD startup must not fail. [Task 6]

---

### Task 1: Config-changed listeners

**Files:**
- Modify: `freecad_ai/config.py` (new block after `reload_config`)
- Modify: `tests/conftest.py` (`reset_config_singleton` fixture)
- Test: `tests/unit/test_config.py` (new class `TestConfigListeners` at the end)

**Interfaces:**
- Produces: `add_config_listener(fn) -> None`, `remove_config_listener(fn) -> None`, `notify_config_changed() -> None`, module list `_config_listeners`. Listeners take no arguments.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_config.py`:

```python
class TestConfigListeners:
    """#99: one refresh path after the dialog or the preferences page saves."""

    def test_a_listener_is_called(self):
        from freecad_ai.config import add_config_listener, notify_config_changed
        calls = []
        add_config_listener(lambda: calls.append(1))
        notify_config_changed()
        assert calls == [1]

    def test_a_removed_listener_is_not_called(self):
        from freecad_ai.config import (
            add_config_listener, notify_config_changed, remove_config_listener)
        calls = []
        fn = lambda: calls.append(1)  # noqa: E731
        add_config_listener(fn)
        remove_config_listener(fn)
        notify_config_changed()
        assert calls == []

    def test_removing_an_unknown_listener_is_harmless(self):
        from freecad_ai.config import remove_config_listener
        remove_config_listener(lambda: None)

    def test_adding_twice_calls_once(self):
        from freecad_ai.config import add_config_listener, notify_config_changed
        calls = []
        fn = lambda: calls.append(1)  # noqa: E731
        add_config_listener(fn)
        add_config_listener(fn)
        notify_config_changed()
        assert calls == [1]

    def test_a_raising_listener_does_not_block_the_others(self, caplog):
        from freecad_ai.config import add_config_listener, notify_config_changed
        calls = []

        def boom():
            raise RuntimeError("deleted Qt object")

        add_config_listener(boom)
        add_config_listener(lambda: calls.append(1))
        notify_config_changed()
        assert calls == [1]
        assert "deleted Qt object" in caplog.text

    def test_a_listener_may_remove_itself_while_being_notified(self):
        from freecad_ai.config import (
            add_config_listener, notify_config_changed, remove_config_listener)
        calls = []

        def once():
            calls.append("once")
            remove_config_listener(once)

        add_config_listener(once)
        add_config_listener(lambda: calls.append("other"))
        notify_config_changed()
        notify_config_changed()
        assert calls == ["once", "other", "other"]
```

In `tests/conftest.py`, extend `reset_config_singleton` so no listener leaks between tests:

```python
@pytest.fixture(autouse=True)
def reset_config_singleton():
    """Reset the config singleton and its listeners after each test."""
    yield
    import freecad_ai.config as config_mod
    config_mod._config = None
    config_mod._config_listeners.clear()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_config.py::TestConfigListeners -q`
Expected: the tests FAIL with `ImportError: cannot import name 'add_config_listener'`. The conftest teardown also errors on `_config_listeners`.

- [ ] **Step 3: Implement**

Append to `freecad_ai/config.py` after `reload_config()`:

```python
# ── Config-changed notification (#99) ───────────────────────────────────
#
# The Settings dialog and the Edit → Preferences page both save the live
# config; whoever shows state derived from it (the chat panel) registers
# here instead of each window knowing about every consumer.

_config_listeners: list = []


def add_config_listener(fn) -> None:
    """Call ``fn()`` after every notify_config_changed(). Idempotent."""
    if fn not in _config_listeners:
        _config_listeners.append(fn)


def remove_config_listener(fn) -> None:
    """Stop calling ``fn``. Unknown listeners are ignored."""
    try:
        _config_listeners.remove(fn)
    except ValueError:
        pass


def notify_config_changed() -> None:
    """Tell every listener the config was saved.

    Iterates over a copy, so a listener may remove itself. One that raises
    is logged and skipped: a closed panel's stale listener must not stop
    the others from refreshing.
    """
    for fn in list(_config_listeners):
        try:
            fn()
        except Exception:
            logger.exception("FreeCAD AI: config listener %r failed", fn)
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_config.py::TestConfigListeners -q`
Expected: 6 passed. Then run the full suite. Expected: 1844 passed.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/config.py tests/conftest.py tests/unit/test_config.py
git commit -m "feat(config): config-changed listeners (#99)"
```

---

### Task 2: `ProviderSection` widget

**Files:**
- Create: `freecad_ai/ui/provider_section.py`
- Test: `tests/unit/test_provider_section.py` (new)

`SettingsDialog` stays untouched in this task, so the two sections exist twice until Task 3.

**Interfaces:**
- Produces, on class `ProviderSection(QWidget)`, in `freecad_ai.ui.provider_section`:
  - Signals:
    - `profileShown(object)`: a `ProviderConfig`, emitted after a profile is shown.
    - `aboutToCommit(object)`: a `ProviderConfig`, emitted inside `_commit_profile_fields` before it returns.
    - `presetApplied(object)`: the preset `dict`, possibly `{}`, emitted after a user provider switch applied it.
    - `modelChanged(str)`: the stripped model text, emitted on `editingFinished`.
  - Public methods:
    - `load(cfg, label=None)`
    - `apply_to(cfg)`
    - `commit()`
    - `profiles() -> dict`: the live scratch dict; callers only read it.
    - `current_label() -> str | None`
    - `current_profile() -> ProviderConfig | None`
    - `active_label() -> str`
    - `current_provider_name() -> str`: the name at the provider combo's index, or `""`.
    - `utility_selection(name) -> str`: the combo's `currentData()`, or `""`.
    - `set_probe_result(label, *, vision=None, tools=None, thinking=None)`: `None` means not reported.
    - `is_dirty() -> bool`
  - Class attribute `UTILITIES`. Classmethod `_collect_utility_profiles(selections)`. Staticmethod `_profiles_with_url_placeholder(profiles)`.
  - Widget attributes, same names as in the dialog today:
    - profile row: `profile_combo`, `profile_add_btn`, `profile_rename_btn`, `profile_delete_btn`, `profile_active_check`;
    - connection fields: `provider_combo`, `api_key_edit`, `base_url_edit`, `model_edit`;
    - vision row: `vision_check`, `_vision_status_label`, `_vision_reset_btn`;
    - utilities: `utility_group`, `utility_combos`.
  - Scratch state, same names as in the dialog, so the fake-self tests move over by class name only: `_profiles`, `_active_profile`, `_utility_profiles`, `_current_profile_label`, `_vision_override_value`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_provider_section.py`:

```python
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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_provider_section.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'freecad_ai.ui.provider_section'`.

- [ ] **Step 3: Create `freecad_ai/ui/provider_section.py`**

Start with this module frame. Then copy each member named under "Moved verbatim" out of `freecad_ai/ui/settings_dialog.py` with its body and docstring unchanged; find each by its `def` name.

```python
"""The LLM Provider and Utility models sections, shared by two windows.

The workbench's Settings dialog and Edit → Preferences → FreeCAD AI both
embed this widget, so the two can never offer different providers or
write different things (#12, #97, #99). It edits a private copy of the
profiles; nothing reaches the config until apply_to().

Strings keep the "SettingsDialog" translation context they had before
the move, so existing translations still apply.
"""

import copy
import dataclasses
import re

from .compat import QtWidgets, QtCore
from ..i18n import translate, QT_TRANSLATE_NOOP
from ..config import PROVIDER_PRESETS, ProviderConfig
from ..llm.providers import get_provider_names

QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QFormLayout = QtWidgets.QFormLayout
QGroupBox = QtWidgets.QGroupBox
QComboBox = QtWidgets.QComboBox
QLineEdit = QtWidgets.QLineEdit
QCheckBox = QtWidgets.QCheckBox
QPushButton = QtWidgets.QPushButton
QLabel = QtWidgets.QLabel
QMessageBox = QtWidgets.QMessageBox
QInputDialog = QtWidgets.QInputDialog
Signal = QtCore.Signal

_URL_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z0-9_]+\}")


class ProviderSection(QWidget):
    """Profiles, their connection fields, and utility routing."""

    # object, not ProviderConfig/dict: PySide2 and PySide6 both carry any
    # Python object through Signal(object) by reference, which is what lets
    # an aboutToCommit slot write into the profile before emit() returns.
    profileShown = Signal(object)
    aboutToCommit = Signal(object)
    presetApplied = Signal(object)
    modelChanged = Signal(str)

    # UTILITIES — moved verbatim from SettingsDialog, comment included.

    # _collect_utility_profiles — moved verbatim (classmethod).

    def __init__(self, parent=None):
        super().__init__(parent)
        self._profiles = {}
        self._active_profile = ""
        self._utility_profiles = {}
        self._current_profile_label = None
        self._stand_in_index = None
        self._baseline = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # From SettingsDialog._build_ui: everything from
        #     provider_group = QGroupBox(translate("SettingsDialog", "LLM Provider"))
        # through
        #     layout.addWidget(self.utility_group)
        # moved verbatim, with exactly two edits:
        #   1. self.model_edit.editingFinished.connect(self._on_model_changed)
        #      becomes .connect(self._on_model_edited)
        #   2. the line  self._last_model_name = ""  is deleted (it stays
        #      in the dialog, which owns the params table)

    # ── Public interface ───────────────────────────────────────────

    def load(self, cfg, label=None):
        """Fill the widget from ``cfg`` and set the is_dirty() baseline.

        Deep-copies the profiles: the config is the live singleton, and an
        unrelated save_current_config() elsewhere must not flush an edit
        the user later cancels. Shows ``label`` when it names a profile,
        else the active one.
        """
        self._profiles = copy.deepcopy(cfg.profiles)
        self._active_profile = cfg.active_profile
        self._utility_profiles = dict(cfg.utility_profiles)
        shown = label if label in self._profiles else self._active_profile
        # Set before the refresh, which selects the profile being edited.
        self._current_profile_label = shown
        self._refresh_profile_combo()
        self._show_profile(shown)
        self._baseline = self._state()

    def apply_to(self, cfg):
        """Commit the visible fields, then write the scratch copy into cfg."""
        self._commit_profile_fields()
        cfg.profiles = copy.deepcopy(self._profiles)
        cfg.active_profile = self._active_profile
        cfg.utility_profiles = self._collect_utility_profiles(
            self._utility_profiles)

    def commit(self):
        """Write the visible fields into the profile being edited."""
        self._commit_profile_fields()

    def profiles(self) -> dict:
        return self._profiles

    def current_label(self):
        return self._current_profile_label

    def current_profile(self):
        return self._profiles.get(self._current_profile_label)

    def active_label(self) -> str:
        return self._active_profile

    def current_provider_name(self) -> str:
        names = get_provider_names()
        idx = self.provider_combo.currentIndex()
        return names[idx] if 0 <= idx < len(names) else ""

    def utility_selection(self, name: str) -> str:
        combo = self.utility_combos.get(name)
        return (combo.currentData() or "") if combo is not None else ""

    def set_probe_result(self, label, *, vision=None, tools=None,
                         thinking=None):
        """Record a probe's findings on the profile it probed.

        ``label`` is captured when the probe starts, so a profile switch
        mid-probe cannot land the answer on the wrong profile; a profile
        deleted meanwhile is ignored. None means "not reported" — False is
        an answer and is recorded.
        """
        prof = self._profiles.get(label)
        if prof is None:
            return
        if vision is not None:
            prof.vision_detected = vision
        if tools is not None:
            prof.tools_detected = tools
        if thinking is not None:
            prof.thinking_detected = thinking
        if vision is not None and label == self._current_profile_label:
            self._update_vision_ui(prof)

    def is_dirty(self) -> bool:
        """Whether anything differs from the load() baseline.

        Compares state, not edit counts, so a value typed and then
        restored is clean.
        """
        if self._baseline is None:
            return False
        self._commit_profile_fields()
        return self._state() != self._baseline

    def _state(self):
        return (
            {label: dataclasses.asdict(prof)
             for label, prof in self._profiles.items()},
            self._active_profile,
            self._collect_utility_profiles(self._utility_profiles),
        )

    # ── Moved verbatim from SettingsDialog ─────────────────────────
    # _rename_profile, _delete_profile, _refresh_profile_combo,
    # _refresh_utility_combos, _on_utility_combo_changed,
    # _on_profile_active_toggled, _on_profile_changed, _on_profile_add,
    # _on_profile_rename, _on_profile_delete,
    # _profiles_with_url_placeholder (staticmethod),
    # _update_vision_ui, _on_vision_override_changed, _reset_vision_override

    # ── Moved with changes ─────────────────────────────────────────

    def _commit_profile_fields(self) -> None:
        """Write the visible connection widgets back into their profile.

        Called before switching away from a profile so an in-progress edit
        is not lost — the #75 complaint, from the other direction.
        """
        label = getattr(self, "_current_profile_label", None)
        prof = self._profiles.get(label)
        if prof is None:
            return
        names = get_provider_names()
        idx = self.provider_combo.currentIndex()
        new_name = names[idx] if 0 <= idx < len(names) else prof.name
        # A provider the combo cannot show (a hand edit, or a config from
        # a newer version) is displayed as a stand-in entry by
        # _show_profile. Until the user picks something, that entry is not
        # a choice, and writing it back is #97 in another shape.
        if (prof.name not in names
                and idx == getattr(self, "_stand_in_index", None)):
            new_name = prof.name
        new_model = self.model_edit.text()
        # A probe result describes one provider+model pair. Retype either
        # and the stored answer is about something else, so drop it —
        # per profile, since another profile's probe is still valid.
        if new_name != prof.name or new_model != prof.model:
            prof.vision_detected = None
            prof.tools_detected = None
            prof.thinking_detected = None
        prof.name = new_name
        prof.base_url = self.base_url_edit.text()
        prof.api_key = self.api_key_edit.text()
        prof.model = new_model
        # The vision checkbox is a profile widget like the four above; the
        # section holds its pending value so a tri-state (None) survives.
        if hasattr(self, "_vision_override_value"):
            prof.vision_override = self._vision_override_value
        # The Settings dialog writes its params table into prof.params
        # here; the preferences page has no table and leaves them alone.
        self.aboutToCommit.emit(prof)

    def _show_profile(self, label: str) -> None:
        """Populate the connection widgets from a profile."""
        prof = self._profiles[label]
        self._current_profile_label = label
        names = get_provider_names()
        try:
            idx = names.index(prof.name)
            self._stand_in_index = None
        except ValueError:
            idx = 0
            self._stand_in_index = idx
        # Programmatic index moves must not run _on_provider_changed —
        # that handler exists to apply a preset on a *user* switch, and
        # firing it here would overwrite the profile's saved URL (#75).
        self.provider_combo.blockSignals(True)
        try:
            self.provider_combo.setCurrentIndex(idx)
        finally:
            self.provider_combo.blockSignals(False)
        self.api_key_edit.setText(prof.api_key)
        self.base_url_edit.setText(prof.base_url)
        self.model_edit.setText(prof.model)
        self._update_vision_ui(prof)

        is_active = label == self._active_profile
        # blockSignals, or populating the widgets would itself re-point
        # chat through _on_profile_active_toggled.
        self.profile_active_check.blockSignals(True)
        try:
            self.profile_active_check.setChecked(is_active)
        finally:
            self.profile_active_check.blockSignals(False)
        # Disabled while ticked: there is always exactly one active
        # profile, so the way to move it is to tick a different one, not
        # to untick this one.
        self.profile_active_check.setEnabled(not is_active)
        self.profileShown.emit(prof)

    def _on_provider_changed(self, index):
        """Apply the new provider's preset URL and model on a user switch."""
        names = get_provider_names()
        if not 0 <= index < len(names):
            return
        preset = PROVIDER_PRESETS.get(names[index], {})
        # Only overwrite when the preset has a concrete value. The
        # "custom" preset ships empty strings — wiping the user's
        # gateway/model on every switch-to-custom is the second half
        # of #12. Real providers always have non-empty presets, so
        # behavior is unchanged there.
        new_base_url = preset.get("base_url", "")
        if new_base_url:
            self.base_url_edit.setText(new_base_url)
        new_model = preset.get("default_model", "")
        if new_model:
            self.model_edit.setText(new_model)
        # The dialog reloads its params table and applies default_rerank
        # (#10) here, before the commit below writes the table back.
        self.presetApplied.emit(preset)
        # A vendor switch is an explicit "point this profile
        # elsewhere", so record it. Only a user-driven change reaches
        # here: programmatic index moves are wrapped in blockSignals.
        self._commit_profile_fields()

    def _on_model_edited(self):
        self.modelChanged.emit(self.model_edit.text().strip())
```

When copying the verbatim members, fix these three leftovers from the dialog:
- `_on_utility_combo_changed`'s docstring mentions "the rerank probe". Keep it.
- `_refresh_profile_combo` calls `self._refresh_utility_combos()`. Both now live on the section.
- In `_update_vision_ui`, change the words "the dialog holds" in any comment to "the section holds". Change no code.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_provider_section.py -q`
Expected: every test passes. Then run the full suite. Expected: 1844 plus the new count, all green.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/ui/provider_section.py tests/unit/test_provider_section.py
git commit -m "feat(ui): ProviderSection, the provider sections as one widget (#99)"
```

---

### Task 3: `SettingsDialog` embeds the section

**Files:**
- Modify: `freecad_ai/ui/settings_dialog.py`
- Modify tests:
  - `tests/unit/test_profile_selector.py`
  - `tests/unit/test_per_profile_capabilities.py`
  - `tests/unit/test_settings_dialog_provider_change.py`
  - `tests/unit/test_settings_dialog_base_url_guard.py`
  - `tests/unit/test_probe_status_names_profile.py`
  - `tests/unit/test_reranker_namespace.py`
  - `tests/unit/test_test_connection_config_scope.py`
  - `tests/unit/test_utility_dropdowns.py`
  - `tests/unit/test_settings_dialog_geometry.py`, plus any other test that `grep` shows reading a moved attribute
- Create: `tests/unit/test_settings_dialog_section_wiring.py`

**Interfaces:**
- Consumes: the whole `ProviderSection` interface from Task 2.
- Produces:
  - `SettingsDialog.provider_section`.
  - Dialog slots: `_on_profile_shown(prof)`, `_on_about_to_commit(prof)`, `_on_preset_applied(preset)`, `_on_model_changed(new_model: str)`. The last one now takes the model text.
  - `_confirm_incomplete_profiles(self, profiles) -> bool`, which now takes the profiles dict.
  - `SettingsDialog._probed_profile` is **deleted**.

- [ ] **Step 1: Write the failing wiring tests**

Create `tests/unit/test_settings_dialog_section_wiring.py`:

```python
"""The Settings dialog's params table and reranker defaults ride on the
ProviderSection's signals (#99). A real dialog under offscreen Qt: a
fake self cannot show that a signal is actually connected."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

try:
    from PySide6 import QtWidgets
except ImportError:
    try:
        from PySide2 import QtWidgets
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.config import PROVIDER_PRESETS, ProviderConfig  # noqa: E402
from freecad_ai.llm.providers import get_provider_names  # noqa: E402
from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def cfg(tmp_config_dir):
    import freecad_ai.config as config_mod
    c = config_mod.get_config()
    c.profiles = {
        "a": ProviderConfig(name="anthropic", model="m-a",
                            params={"temperature": 0.1}),
        "b": ProviderConfig(name="ollama", model="m-b",
                            base_url="http://localhost:11434/v1",
                            params={"top_k": 40}),
    }
    c.active_profile = "a"
    c.rerank_method = "off"
    c.rerank_top_n = 15
    return c


@pytest.fixture
def dialog(qapp, cfg):
    dlg = SettingsDialog()
    yield dlg
    dlg.deleteLater()


def _select(dlg, label):
    combo = dlg.provider_section.profile_combo
    combo.setCurrentIndex(combo.findData(label))


def test_showing_a_profile_loads_its_params(dialog):
    assert dialog._read_model_params_table() == {"temperature": 0.1}
    _select(dialog, "b")
    assert dialog._read_model_params_table() == {"top_k": 40}


def test_switching_away_commits_the_table_into_the_profile(dialog):
    dialog._populate_model_params_table({"temperature": 0.7})
    _select(dialog, "b")
    assert dialog.provider_section.profiles()["a"].params == {
        "temperature": 0.7}


def test_a_b_a_keeps_each_profiles_own_params(dialog):
    dialog._populate_model_params_table({"temperature": 0.7})
    _select(dialog, "b")
    _select(dialog, "a")
    assert dialog._read_model_params_table() == {"temperature": 0.7}


def test_a_provider_switch_applies_default_rerank_when_untouched(dialog):
    defaults = PROVIDER_PRESETS["github"]["default_rerank"]
    dialog.provider_section.provider_combo.setCurrentIndex(
        get_provider_names().index("github"))
    assert dialog.rerank_method_combo.currentIndex() == \
        SettingsDialog._RERANK_METHOD_INDEX[defaults["method"]]
    assert dialog.rerank_top_n_spin.value() == int(defaults["top_n"])


def test_a_model_edit_swaps_the_table(dialog):
    s = dialog.provider_section
    s.model_edit.setText("other-model")
    s.model_edit.editingFinished.emit()
    assert dialog._last_model_name == "other-model"


def test_ok_writes_profiles_and_the_table(dialog, cfg):
    dialog._populate_model_params_table({"temperature": 0.2})
    dialog._save()
    assert cfg.profiles["a"].params == {"temperature": 0.2}
    assert cfg.active_profile == "a"


def test_test_connection_probes_the_shown_profile(dialog, monkeypatch):
    import freecad_ai.ui.settings_dialog as sd
    captured = {}

    class _Thread:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            self.finished = self.vision_result = self.capabilities_result = \
                type("S", (), {"connect": lambda *_: None})()

        def start(self):
            pass

    monkeypatch.setattr(sd, "_TestConnectionThread", _Thread)
    _select(dialog, "b")
    dialog.provider_section.model_edit.setText("typed-model")
    dialog._test_connection()
    provider_name, base_url, _key, model, params = captured["args"][:5]
    assert (provider_name, model) == ("ollama", "typed-model")
    assert base_url == "http://localhost:11434/v1"
    assert params == {"top_k": 40}
    assert dialog._test_profile_label == "b"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_settings_dialog_section_wiring.py -q`
Expected: FAIL with `AttributeError: 'SettingsDialog' object has no attribute 'provider_section'`.

- [ ] **Step 3: Rewire `settings_dialog.py`**

3a. Add `from .provider_section import ProviderSection` after the existing `..llm.providers` import. Delete `_URL_PLACEHOLDER_RE` (it moved) and the class attribute `UTILITIES` and classmethod `_collect_utility_profiles` (they moved).

3b. In `_build_ui`, replace the moved block (from `provider_group = QGroupBox(...)` through `layout.addWidget(self.utility_group)`) with:

```python
        # LLM Provider + Utility models: shared with Edit → Preferences
        # (#99). The dialog-only parts ride on its signals.
        self.provider_section = ProviderSection()
        self.provider_section.profileShown.connect(self._on_profile_shown)
        self.provider_section.aboutToCommit.connect(self._on_about_to_commit)
        self.provider_section.presetApplied.connect(self._on_preset_applied)
        self.provider_section.modelChanged.connect(self._on_model_changed)
        layout.addWidget(self.provider_section)
        self._last_model_name = ""  # track model name for param save/load
```

3c. In `_load_from_config`, replace the working-copy block (the comment starting "Profile edits stay dialog-local", the three assignments, `_refresh_profile_combo()` and `_show_profile(...)`) with:

```python
        # Profile edits stay in the section's own copy until OK (see
        # ProviderSection.load for why the live singleton must not see them).
        self.provider_section.load(cfg)
```

3d. Delete from `SettingsDialog` every member moved in Task 2:
- profile handling: `_rename_profile`, `_delete_profile`, `_refresh_profile_combo`, `_refresh_utility_combos`, `_on_utility_combo_changed`, `_commit_profile_fields`, `_show_profile`, `_on_profile_active_toggled`, `_on_profile_changed`, `_on_profile_add`, `_on_profile_rename`, `_on_profile_delete`;
- provider and placeholder: `_on_provider_changed`, `_profiles_with_url_placeholder`;
- vision row: `_update_vision_ui`, `_on_vision_override_changed`, `_reset_vision_override`;
- and `_probed_profile`.

Add these dialog slots where `_on_provider_changed` was:

```python
    def _on_profile_shown(self, prof):
        """The section showed a profile: load its params into the table."""
        self._load_model_params_table(prof.model, self._cfg, prof)

    def _on_about_to_commit(self, prof):
        """The section is committing a profile: the table is its params.

        A straight write-back, so a removed row is a removed parameter. Do
        not reintroduce a merge with cfg.model_params here: that shared
        layer is legacy and unread, and layering it back in would make
        Remove a no-op again.
        """
        prof.params = self._read_model_params_table()

    def _on_preset_applied(self, preset):
        """A user provider switch: reload the table, maybe apply #10.

        The working-copy profile, not the singleton: a vendor switch keeps
        the parameters this profile already states, and falls back to the
        new preset's default_params only when it states none.
        """
        section = self.provider_section
        self._load_model_params_table(
            section.model_edit.text(), self._cfg, section.current_profile())
        # Apply provider-recommended reranker settings only when the
        # reranker UI is still at its factory default (off + top_n 15), so
        # an explicit user choice — even "off" — survives a provider
        # switch. Used by the github preset (issue #10).
        rerank_defaults = preset.get("default_rerank", {})
        if rerank_defaults and self._rerank_at_factory_defaults():
            self._apply_rerank_defaults(rerank_defaults)
```

3e. Replace `_on_model_changed` with:

```python
    def _on_model_changed(self, new_model: str):
        """Stash the edited table on the working-copy profile, load the new model's."""
        if new_model == self._last_model_name or not new_model:
            return
        # Stash current table on the working-copy profile (never the live
        # singleton — cfg.model_params is read-only from this dialog).
        prof = self.provider_section.current_profile()
        if self._last_model_name and prof is not None:
            params = self._read_model_params_table()
            if params:
                prof.params = params
        self._load_model_params_table(new_model, self._cfg, prof)
```

3f. In `_load_model_params_table`:
- change the `if profile is None:` branch body to `profile = self.provider_section.current_profile()`;
- replace the three lines computing `provider_name` from `self.provider_combo` with `provider_name = self.provider_section.current_provider_name()`.

Do the same `provider_name` replacement in `_load_default_model_params`.

3g. Change `_confirm_incomplete_profiles` to take the profiles:

```python
    def _confirm_incomplete_profiles(self, profiles) -> bool:
```

In its body, use `profiles` in place of `self._profiles`, and call `ProviderSection._profiles_with_url_placeholder(profiles)`. Keep the rest, including `_profiles_missing_base_url`, unchanged.

3h. In `_save`, replace everything from `self._commit_profile_fields()` through the `cfg.utility_profiles = ...` statement with:

```python
        section = self.provider_section
        section.commit()
        if not self._confirm_incomplete_profiles(section.profiles()):
            return
        section.apply_to(cfg)
```

Replace `model_name = self.model_edit.text().strip()` with `model_name = section.model_edit.text().strip()`.

3i. In `_test_reranker`, replace the lines from `self._commit_profile_fields()` through `profile = self._profiles.get(label)` with:

```python
        section = self.provider_section
        section.commit()
        label = section.utility_selection("rerank")
        if label not in section.profiles():
            label = section.active_label()
        profile = section.profiles().get(label)
```

Keep the existing comments that still apply ("An in-progress edit…").

3j. In `_test_connection`, replace the resolution lines, from `names = get_provider_names()` through `model_params = self._read_model_params_table()`, with the block below. Keep the existing long comment above them, minus its sentence about reading "from the visible widgets directly".

```python
        # Commit first, then probe the committed profile: an edit in
        # progress is what gets tested, and nothing outside the section's
        # own copy is written (#76). Committing first also keeps the probe
        # result: a commit *after* it would see the retyped model and
        # drop the answer as stale.
        section = self.provider_section
        section.commit()
        profile = section.current_profile()
        provider_name = profile.name
        base_url = profile.base_url
        # Match create_client()'s fallback: an explicit key on the profile
        # wins, else the vendor-wide default in provider_keys.
        api_key = profile.api_key or \
            self._cfg.provider_keys.get(provider_name, "")
        model = profile.model
        model_params = dict(profile.params)
```

Change `self._test_profile_label = self._current_profile_label` to `self._test_profile_label = section.current_label()`.

3k. Replace the profile bookkeeping in `_on_vision_probed` (from `profile = self._probed_profile()` through the `_update_vision_ui` call) with:

```python
        self.provider_section.set_probe_result(
            getattr(self, "_test_profile_label", None),
            vision=supports_vision)
```

In `_on_capabilities_detected`, replace the `profile = self._probed_profile()` block with:

```python
        self.provider_section.set_probe_result(
            getattr(self, "_test_profile_label", None),
            **{k: bool(caps[k]) for k in ("tools", "thinking") if k in caps})
```

3l. Remove imports and Qt aliases that this task left unused. Check each candidate with `grep -n "QInputDialog\|copy\.\|ProviderConfig\|QT_TRANSLATE_NOOP\|\bre\." freecad_ai/ui/settings_dialog.py`.

- [ ] **Step 4: Run the wiring tests**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_settings_dialog_section_wiring.py -q`
Expected: all pass.

- [ ] **Step 5: Move the existing tests over**

First record the collected count: `env PYTHONPATH= .venv/bin/pytest tests/unit -q --co --ignore=tests/unit/test_document_attach.py | tail -1`.

Then apply these rules to each file listed under **Files**:

1. **A test that calls a moved method** (the list in 3d, plus `_on_provider_changed` and `_profiles_with_url_placeholder`): change `SettingsDialog.X` to `ProviderSection.X` and import `ProviderSection` from `freecad_ai.ui.provider_section`. A fake self then needs the signal stand-ins the method now emits. Add this helper to each file that needs it:

   ```python
   class _Sig:
       """Stand-in for a Qt signal on a fake self."""
       def __init__(self):
           self.calls = []

       def emit(self, *args):
           self.calls.append(args)
   ```

   Also give the fake `profileShown=_Sig()`, `aboutToCommit=_Sig()` and `presetApplied=_Sig()`, and delete its `_load_model_params_table` / `_read_model_params_table` stubs. Keep the assertions unchanged.
2. **A test whose assertion depends on the params table or the reranker defaults** while calling a moved method: that is dialog behavior. Delete the fake-self version. Point to its successor in `test_settings_dialog_section_wiring.py` or `test_provider_section.py`, or add one there. For example, the `_on_provider_changed` tests that assert on `_load_model_params_table` move into `test_a_provider_switch_…` and `test_preset_applied_fires_on_a_user_switch`. The a-b-a params test becomes `test_a_b_a_keeps_each_profiles_own_params`.
3. **A test that calls a method that stays in the dialog** (`_save`, `_load_from_config`, `_load_model_params_table`, `_on_model_changed`, `_test_connection`, `_test_reranker`, `_on_vision_probed`, `_on_capabilities_detected`, `_confirm_incomplete_profiles`): keep it on `SettingsDialog` and give its fake a `provider_section`.
   - MagicMock fakes already have one. Set only what the method reads:
     - `fake.provider_section.current_profile.return_value = ProviderConfig(...)`
     - `.current_label.return_value = "cloud"`
     - `.profiles.return_value = {...}`
     - `.utility_selection.return_value = "rr"`
     - `.active_label.return_value = "cloud"`
     - `.current_provider_name.return_value = "anthropic"`
   - SimpleNamespace fakes get `provider_section=MagicMock()` configured the same way.
   - `_on_model_changed` is now called with the model string.
   - `_confirm_incomplete_profiles` is now called with the profiles dict as its argument.
   - Probe-handler tests assert `fake.provider_section.set_probe_result.assert_called_once_with(...)`. What happens on the profile is covered in `TestProbeResult`.
4. **`_save` tests that asserted profiles written back** (`TestSaveWritesBackProfileState` and its neighbors): the write-back is now `apply_to`, covered by `TestRoundTrip`. Keep one fake-self assertion that `_save` calls `section.commit()`, then `_confirm_incomplete_profiles(section.profiles())`, then `section.apply_to(cfg)`, and nothing when the confirmation returns False:

   ```python
   def test_save_hands_the_profiles_to_the_section(monkeypatch):
       cfg = AppConfig()
       monkeypatch.setattr("freecad_ai.ui.settings_dialog.get_config", lambda: cfg)
       monkeypatch.setattr(
           "freecad_ai.ui.settings_dialog.save_current_config", lambda: None)
       fake = _fake_save_dialog()          # the file's existing MagicMock builder
       fake._confirm_incomplete_profiles.return_value = True
       SettingsDialog._save(fake)
       fake.provider_section.commit.assert_called_once_with()
       fake._confirm_incomplete_profiles.assert_called_once_with(
           fake.provider_section.profiles.return_value)
       fake.provider_section.apply_to.assert_called_once_with(cfg)


   def test_a_declined_confirmation_writes_nothing(monkeypatch):
       cfg = AppConfig()
       monkeypatch.setattr("freecad_ai.ui.settings_dialog.get_config", lambda: cfg)
       fake = _fake_save_dialog()
       fake._confirm_incomplete_profiles.return_value = False
       SettingsDialog._save(fake)
       fake.provider_section.apply_to.assert_not_called()
   ```

5. **`test_utility_dropdowns.py`**: point `UTILITIES`, `_collect_utility_profiles` and the source-extraction check at `ProviderSection` and `freecad_ai/ui/provider_section.py`.
6. **Attribute reads on a real dialog** (geometry, screenshots): anything like `dialog.profile_add_btn` becomes `dialog.provider_section.profile_add_btn`. Find them with `grep -rn "profile_add_btn\|profile_rename_btn\|profile_delete_btn\|provider_combo\|profile_combo\|utility_combos\|vision_check" tests/unit`.

Recount afterwards. Every test that disappeared must have a named successor. List the `old → new` pairs in the commit message body.

- [ ] **Step 6: Run the full suite**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py`
Expected: all green, including the offscreen geometry tests. They check that the profile buttons are still visible at the default width, because the section is now inside the dialog's width computation.

- [ ] **Step 7: Commit**

```bash
git add freecad_ai/ui/settings_dialog.py tests/unit
git commit -m "refactor(ui): Settings dialog embeds ProviderSection (#99)" \
  -m "<old test → new test pairs from Step 5>"
```

---

### Task 4: One refresh path. The dialog notifies, and the chat panel listens.

**Files:**
- Modify: `freecad_ai/ui/settings_dialog.py` (`_save`)
- Modify: `freecad_ai/ui/chat_widget.py`:
  - add `import copy`;
  - extend the `..config` import;
  - change `ChatDockWidget.__init__` and `_open_settings`;
  - add `_config_refresh_key`, `_register_config_listener` and `_on_config_changed`.
- Test: `tests/unit/test_config_changed_notification.py` (new)

**Interfaces:**
- Consumes: `add_config_listener`, `remove_config_listener`, `notify_config_changed` (Task 1).
- Produces:
  - `ChatDockWidget._config_refresh_key(cfg) -> tuple` (staticmethod);
  - `ChatDockWidget._register_config_listener(self)`;
  - `ChatDockWidget._on_config_changed(self)`;
  - attribute `self._config_seen`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_config_changed_notification.py`:

```python
"""#99: the chat panel refreshes from a config listener, not from code
that runs after SettingsDialog.exec() — so a save from Edit → Preferences
refreshes it too."""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

import freecad_ai.config as config_mod  # noqa: E402
from freecad_ai.config import AppConfig  # noqa: E402
from freecad_ai.ui import chat_widget as cw  # noqa: E402
from freecad_ai.ui.chat_widget import ChatDockWidget  # noqa: E402
from freecad_ai.ui.settings_dialog import SettingsDialog  # noqa: E402


@pytest.fixture
def cfg(monkeypatch):
    c = AppConfig()
    c.mcp_servers = [{"name": "a", "command": "x"}]
    monkeypatch.setattr(cw, "get_config", lambda: c)
    return c


@pytest.fixture
def manager(monkeypatch):
    m = MagicMock()
    monkeypatch.setattr("freecad_ai.mcp.manager.get_mcp_manager", lambda: m)
    return m


def _panel(cfg):
    return SimpleNamespace(
        _config_seen=ChatDockWidget._config_refresh_key(cfg),
        _config_refresh_key=ChatDockWidget._config_refresh_key,
        _vision_fallback_tool="tool",
        _mcp_connected=True,
        _ensure_vision_fallback=MagicMock(),
        _refresh_image_controls=MagicMock(),
    )


class TestTheListener:
    def test_a_model_change_resets_the_vision_fallback(self, cfg, manager):
        panel = _panel(cfg)
        cfg.provider.model = "another-model"
        ChatDockWidget._on_config_changed(panel)
        assert panel._vision_fallback_tool is None
        manager.disconnect_all.assert_not_called()

    def test_a_provider_change_resets_the_vision_fallback(self, cfg, manager):
        panel = _panel(cfg)
        cfg.provider.name = "ollama"
        ChatDockWidget._on_config_changed(panel)
        assert panel._vision_fallback_tool is None

    def test_an_mcp_change_disconnects(self, cfg, manager):
        panel = _panel(cfg)
        cfg.mcp_servers = [{"name": "b", "command": "y"}]
        ChatDockWidget._on_config_changed(panel)
        assert panel._vision_fallback_tool is None
        assert panel._mcp_connected is False
        manager.disconnect_all.assert_called_once_with()

    def test_an_in_place_mcp_edit_disconnects(self, cfg, manager):
        """The snapshot is a deep copy, so editing an entry in place still
        counts as a change."""
        panel = _panel(cfg)
        cfg.mcp_servers[0]["command"] = "edited"
        ChatDockWidget._on_config_changed(panel)
        manager.disconnect_all.assert_called_once_with()

    def test_no_change_keeps_state_but_still_refreshes(self, cfg, manager):
        panel = _panel(cfg)
        ChatDockWidget._on_config_changed(panel)
        assert panel._vision_fallback_tool == "tool"
        assert panel._mcp_connected is True
        panel._ensure_vision_fallback.assert_called_once_with()
        panel._refresh_image_controls.assert_called_once_with()

    def test_the_snapshot_advances(self, cfg, manager):
        panel = _panel(cfg)
        cfg.provider.model = "another-model"
        ChatDockWidget._on_config_changed(panel)
        panel._vision_fallback_tool = "rebuilt"
        ChatDockWidget._on_config_changed(panel)
        assert panel._vision_fallback_tool == "rebuilt"


class TestRegistration:
    def test_register_adds_and_destroyed_removes(self, cfg):
        connected = []
        panel = _panel(cfg)
        panel.destroyed = SimpleNamespace(connect=connected.append)
        panel._on_config_changed = lambda: None
        ChatDockWidget._register_config_listener(panel)
        assert panel._config_listener in config_mod._config_listeners
        connected[0]()                # the panel is destroyed
        assert panel._config_listener not in config_mod._config_listeners

    def test_the_panel_registers_on_creation(self):
        assert "self._register_config_listener()" in inspect.getsource(
            ChatDockWidget.__init__)

    def test_open_settings_no_longer_refreshes_by_itself(self):
        src = inspect.getsource(ChatDockWidget._open_settings)
        assert "_ensure_vision_fallback" not in src
        assert "disconnect_all" not in src


class TestTheDialogNotifies:
    def _fake(self):
        fake = MagicMock()
        for combo in ("thinking_combo", "viewport_capture_combo",
                      "viewport_resolution_combo", "rerank_method_combo"):
            getattr(fake, combo).currentIndex.return_value = 0
        fake.rerank_pinned_edit.text.return_value = ""
        fake._parse_server_address.return_value = ("127.0.0.1", 8765)
        fake._confirm_incomplete_profiles.return_value = True
        return fake

    def test_ok_notifies_after_saving(self, monkeypatch):
        order = []
        c = AppConfig()
        monkeypatch.setattr("freecad_ai.ui.settings_dialog.get_config", lambda: c)
        monkeypatch.setattr("freecad_ai.ui.settings_dialog.save_current_config",
                            lambda: order.append("save"))
        monkeypatch.setattr("freecad_ai.ui.settings_dialog.notify_config_changed",
                            lambda: order.append("notify"))
        SettingsDialog._save(self._fake())
        assert order == ["save", "notify"]

    def test_a_declined_save_does_not_notify(self, monkeypatch):
        calls = []
        monkeypatch.setattr("freecad_ai.ui.settings_dialog.get_config",
                            lambda: AppConfig())
        monkeypatch.setattr("freecad_ai.ui.settings_dialog.notify_config_changed",
                            lambda: calls.append(1))
        fake = self._fake()
        fake._confirm_incomplete_profiles.return_value = False
        SettingsDialog._save(fake)
        assert calls == []
```

- [ ] **Step 2: Run them and watch them fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_config_changed_notification.py -q`
Expected: FAIL with `AttributeError: type object 'ChatDockWidget' has no attribute '_config_refresh_key'`. The dialog tests fail on the missing `notify_config_changed` attribute.

- [ ] **Step 3: Implement**

`settings_dialog.py`: extend the config import to `from ..config import get_config, notify_config_changed, save_current_config, PROVIDER_PRESETS, ProviderConfig` (drop any name Task 3 left unused). In `_save`, call `notify_config_changed()` right after `save_current_config()`:

```python
        save_current_config()
        # The chat panel (and anything else showing config-derived state)
        # refreshes from this, whichever window saved (#99).
        notify_config_changed()
```

`chat_widget.py`:
- Add `import copy` with the other stdlib imports.
- Extend the config import to `from ..config import LOGS_DIR, add_config_listener, get_config, prune_oldest_files, remove_config_listener, save_current_config`.
- At the end of `ChatDockWidget.__init__`, add `self._register_config_listener()`.
- Replace `_open_settings` with:

```python
    def _open_settings(self):
        """Open the settings dialog.

        Refreshing afterwards is _on_config_changed's job: the dialog
        notifies on OK, and so does Edit → Preferences (#99).
        """
        from .settings_dialog import SettingsDialog
        try:
            import FreeCADGui as Gui
            parent = Gui.getMainWindow()
        except ImportError:
            parent = self
        SettingsDialog(parent).exec()
```

Add next to it:

```python
    @staticmethod
    def _config_refresh_key(cfg):
        """What _on_config_changed compares: provider, model, MCP servers.

        A deep copy, so an MCP entry edited in place still reads as a
        change on the next comparison.
        """
        return (cfg.provider.name, cfg.provider.model,
                copy.deepcopy(cfg.mcp_servers))

    def _register_config_listener(self):
        """Listen for config saves until this panel is destroyed.

        The listener list holds a bound method, and with it the panel; without
        the removal a closed panel would stay alive there and raise on its
        deleted Qt object at the next save. The lambda, not the bound method,
        is what destroyed calls: by then the wrapper may be half gone.
        """
        self._config_seen = self._config_refresh_key(get_config())
        listener = self._config_listener = self._on_config_changed
        add_config_listener(listener)
        self.destroyed.connect(lambda *_: remove_config_listener(listener))

    def _on_config_changed(self):
        """Refresh what depends on the provider, model and MCP servers."""
        cfg = get_config()
        old_provider, old_model, old_mcp = self._config_seen
        self._config_seen = self._config_refresh_key(cfg)
        if cfg.provider.name != old_provider or cfg.provider.model != old_model:
            self._vision_fallback_tool = None
        if cfg.mcp_servers != old_mcp:
            self._vision_fallback_tool = None
            self._mcp_connected = False
            # Disconnect old MCP servers so stale connections don't linger
            from ..mcp.manager import get_mcp_manager
            get_mcp_manager().disconnect_all()
        self._ensure_vision_fallback()
        self._refresh_image_controls()
```

`__init__` must set `_vision_fallback_tool` and `_mcp_connected` before the new last line. Confirm with `grep -n "_vision_fallback_tool = \|_mcp_connected = " freecad_ai/ui/chat_widget.py`.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_config_changed_notification.py -q`
Expected: all pass. Then run the full suite. Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/ui/settings_dialog.py freecad_ai/ui/chat_widget.py tests/unit/test_config_changed_notification.py
git commit -m "refactor(ui): chat panel refreshes from a config listener (#99)"
```

---

### Task 5: The preferences page

**Files:**
- Create: `freecad_ai/ui/prefs_page.py`
- Modify: `InitGui.py` (the preferences-page registration block)
- Modify: `freecad_ai/paths.py` (delete `get_prefs_ui_path`)
- Delete: `resources/panels/FreeCADAIPrefs.ui`
- Test: `tests/unit/test_prefs_page.py` (new)

The bridge still runs in this task. It mirrors JSON into a parameter group that nothing reads any more, which is harmless. Task 6 removes it.

**Interfaces:**
- Consumes: `ProviderSection` (Task 2); `get_config`, `save_current_config`, `notify_config_changed` (Task 1).
- Produces:
  - class `FreeCADAIPrefsPage` with `form`, `section`, `mode_combo`, `thinking_combo`, `max_tokens_spin`, `enable_tools_check`, `loadSettings()` and `saveSettings()`;
  - module function `_warn(parent, title, text)`, which tests monkeypatch.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_prefs_page.py`:

```python
"""Edit → Preferences → FreeCAD AI (#99).

FreeCAD calls saveSettings() on every page for every OK and Apply, even
pages never opened, so an untouched page must not write anything."""

import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

try:
    from PySide6 import QtWidgets
except ImportError:
    try:
        from PySide2 import QtWidgets
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

import freecad_ai.config as config_mod  # noqa: E402
from freecad_ai.config import ProviderConfig  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def cfg(tmp_config_dir):
    c = config_mod.get_config()
    c.profiles = {
        "cloud": ProviderConfig(name="anthropic", model="m-cloud"),
        "local": ProviderConfig(name="ollama", model="m-local",
                                base_url="http://localhost:11434/v1"),
    }
    c.active_profile = "cloud"
    c.max_tokens = 65536
    c.mode = "act"
    config_mod.save_current_config()
    return c


@pytest.fixture
def notified(monkeypatch):
    calls = []
    monkeypatch.setattr("freecad_ai.ui.prefs_page.notify_config_changed",
                        lambda: calls.append(1))
    return calls


@pytest.fixture
def warnings(monkeypatch):
    calls = []
    monkeypatch.setattr("freecad_ai.ui.prefs_page._warn",
                        lambda parent, title, text: calls.append(text))
    return calls


@pytest.fixture
def page(qapp, cfg, notified, warnings):
    from freecad_ai.ui.prefs_page import FreeCADAIPrefsPage
    p = FreeCADAIPrefsPage()
    p.loadSettings()
    yield p
    p.form.deleteLater()


def _disk():
    with open(config_mod.CONFIG_FILE, "rb") as f:
        return f.read()


class TestUntouched:
    def test_ok_writes_nothing(self, page, notified):
        before = _disk()
        page.saveSettings()
        assert _disk() == before
        assert notified == []

    def test_it_shows_the_active_profile_and_behavior(self, page):
        assert page.section.current_label() == "cloud"
        assert page.mode_combo.currentIndex() == 1          # act
        assert page.max_tokens_spin.value() == 65536


class TestSaving:
    def test_an_edit_is_saved_and_notified(self, page, notified):
        page.section.model_edit.setText("m-edited")
        page.saveSettings()
        assert b"m-edited" in _disk()
        assert config_mod.get_config().provider.model == "m-edited"
        assert notified == [1]

    def test_a_second_save_writes_nothing(self, page, notified):
        page.section.model_edit.setText("m-edited")
        page.saveSettings()
        after_first = _disk()
        page.saveSettings()
        assert _disk() == after_first
        assert notified == [1]

    def test_a_behavior_edit_is_saved(self, page):
        page.thinking_combo.setCurrentIndex(1)
        page.saveSettings()
        assert config_mod.get_config().thinking == "on"

    def test_a_large_max_tokens_survives_an_unrelated_save(self, page):
        """Review Focus 2: the old page's spin box capped at 32768."""
        page.section.model_edit.setText("m-edited")
        page.saveSettings()
        assert config_mod.get_config().max_tokens == 65536

    def test_apply_stays_on_the_profile_being_edited(self, page):
        """Review Focus 4."""
        combo = page.section.profile_combo
        combo.setCurrentIndex(combo.findData("local"))
        page.section.model_edit.setText("m-local-edited")
        page.saveSettings()
        assert page.section.current_label() == "local"
        assert config_mod.get_config().profiles["local"].model == \
            "m-local-edited"

    def test_a_placeholder_url_saves_and_warns(self, page, warnings):
        url = "https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1"
        page.section.base_url_edit.setText(url)
        page.saveSettings()
        assert config_mod.get_config().provider.base_url == url
        assert len(warnings) == 1 and "cloud" in warnings[0]


class TestCancel:
    def test_edits_without_save_leave_the_config_alone(self, page, cfg):
        """Review Focus 3: Cancel means FreeCAD never calls saveSettings()."""
        page.section.model_edit.setText("m-cancelled")
        page.section.commit()
        page.thinking_combo.setCurrentIndex(2)
        assert cfg.provider.model == "m-cloud"
        assert cfg.thinking == "off"
        page.loadSettings()
        assert page.section.model_edit.text() == "m-cloud"
        assert page.thinking_combo.currentIndex() == 0


def test_importing_the_module_builds_no_qt_widgets():
    """InitGui imports the page class at startup; the Qt-heavy imports
    must wait until FreeCAD instantiates it."""
    code = ("import sys, freecad_ai.ui.prefs_page; "
            "print('freecad_ai.ui.provider_section' in sys.modules)")
    env = dict(os.environ, PYTHONPATH="")
    out = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT,
                         env=env, capture_output=True, text=True)
    assert out.stdout.strip() == "False", out.stderr
```

- [ ] **Step 2: Run them and watch them fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_prefs_page.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'freecad_ai.ui.prefs_page'`.

- [ ] **Step 3: Create `freecad_ai/ui/prefs_page.py`**

```python
"""Edit → Preferences → FreeCAD AI.

FreeCAD instantiates this class itself (InitGui.py registers it with
Gui.addPreferencePage) and calls loadSettings() each time Preferences
opens and saveSettings() on every OK and Apply — for every page, opened
or not. So the page saves only when something differs from what it
loaded; an OK elsewhere in Preferences leaves config.json byte-identical.

The provider sections are the Settings dialog's own widget (#99), so the
two windows cannot drift apart again (#12, #97).

Qt-heavy imports live in methods: InitGui imports this module at FreeCAD
startup, long before the page is first shown.
"""

from ..config import get_config, notify_config_changed, save_current_config

_MODES = ["plan", "act"]
_THINKING = ["off", "on", "extended"]


def _warn(parent, title, text):
    from .compat import QtWidgets
    QtWidgets.QMessageBox.warning(parent, title, text)


class FreeCADAIPrefsPage:
    def __init__(self, parent=None):
        from .compat import QtWidgets
        from ..i18n import translate
        from .provider_section import ProviderSection

        self.form = QtWidgets.QWidget(parent)
        self.form.setWindowTitle(translate("FreeCADAIPrefs", "FreeCAD AI"))
        layout = QtWidgets.QVBoxLayout(self.form)

        self.section = ProviderSection()
        layout.addWidget(self.section)

        behavior = QtWidgets.QGroupBox(translate("FreeCADAIPrefs", "Behavior"))
        form = QtWidgets.QFormLayout(behavior)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems([translate("FreeCADAIPrefs", "Plan"),
                                  translate("FreeCADAIPrefs", "Act")])
        form.addRow(translate("FreeCADAIPrefs", "Default mode:"),
                    self.mode_combo)
        self.thinking_combo = QtWidgets.QComboBox()
        self.thinking_combo.addItems([translate("FreeCADAIPrefs", "Off"),
                                      translate("FreeCADAIPrefs", "On"),
                                      translate("FreeCADAIPrefs", "Extended")])
        form.addRow(translate("FreeCADAIPrefs", "Thinking mode:"),
                    self.thinking_combo)
        self.max_tokens_spin = QtWidgets.QSpinBox()
        # The Settings dialog's range. The old page capped at 32768, which
        # clamped a larger saved value on display and wrote it back.
        self.max_tokens_spin.setRange(256, 262144)
        self.max_tokens_spin.setSingleStep(1024)
        form.addRow(translate("FreeCADAIPrefs", "Max tokens:"),
                    self.max_tokens_spin)
        self.enable_tools_check = QtWidgets.QCheckBox()
        form.addRow(translate("FreeCADAIPrefs", "Enable tool calling:"),
                    self.enable_tools_check)
        layout.addWidget(behavior)

        hint = QtWidgets.QLabel(translate(
            "FreeCADAIPrefs",
            "For MCP servers, tool reranking, viewport capture, model "
            "parameters, and more, open the workbench's full settings "
            "dialog (gear icon in the chat panel)."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")
        layout.addWidget(hint)
        layout.addStretch()

        self._behavior_baseline = {}

    def loadSettings(self):  # noqa: N802 — FreeCAD's name
        self._load(label=None)

    def _load(self, label):
        cfg = get_config()
        self.section.load(cfg, label=label)
        # A value these combos cannot show displays as the first entry and
        # is written back only if the user changes that field (see
        # saveSettings), never as a side effect of another edit.
        self.mode_combo.setCurrentIndex(
            _MODES.index(cfg.mode) if cfg.mode in _MODES else 0)
        self.thinking_combo.setCurrentIndex(
            _THINKING.index(cfg.thinking) if cfg.thinking in _THINKING else 0)
        self.max_tokens_spin.setValue(int(cfg.max_tokens))
        self.enable_tools_check.setChecked(bool(cfg.enable_tools))
        self._behavior_baseline = self._behavior_values()

    def _behavior_values(self):
        return {
            "mode": _MODES[self.mode_combo.currentIndex()],
            "thinking": _THINKING[self.thinking_combo.currentIndex()],
            "max_tokens": self.max_tokens_spin.value(),
            "enable_tools": self.enable_tools_check.isChecked(),
        }

    def saveSettings(self):  # noqa: N802 — FreeCAD's name
        changed = {key: value
                   for key, value in self._behavior_values().items()
                   if self._behavior_baseline.get(key) != value}
        if not changed and not self.section.is_dirty():
            return
        cfg = get_config()
        self.section.apply_to(cfg)
        for key, value in changed.items():
            setattr(cfg, key, value)
        save_current_config()
        notify_config_changed()
        # Re-baseline, so a second Apply writes nothing; stay on the
        # profile being edited rather than jumping back to the active one.
        self._load(label=self.section.current_label())
        self._warn_unfilled_placeholders(cfg)

    def _warn_unfilled_placeholders(self, cfg):
        """saveSettings() cannot veto FreeCAD's OK, so warn after saving."""
        from ..i18n import translate
        from .provider_section import ProviderSection
        unfilled = ProviderSection._profiles_with_url_placeholder(cfg.profiles)
        if unfilled:
            _warn(self.form,
                  translate("FreeCADAIPrefs", "Profile cannot be used as set up"),
                  translate(
                      "FreeCADAIPrefs",
                      "The Base URL for %s still contains a placeholder such "
                      "as {ACCOUNT_ID}. Replace it with the value from your "
                      "provider account.") % ", ".join(unfilled))
```

- [ ] **Step 4: Register the page, and delete the `.ui` and its path helper**

In `InitGui.py`, replace the block that starts `# Register the FreeCAD AI preferences page in Edit → Preferences.` and imports `get_prefs_ui_path` with:

```python
# Register the FreeCAD AI page in Edit → Preferences: a Python page class
# embedding the Settings dialog's own provider sections, writing
# config.json directly (#99).
try:
    from freecad_ai.ui.prefs_page import FreeCADAIPrefsPage as _PrefsPage
    Gui.addPreferencePage(_PrefsPage, "FreeCAD AI")
except Exception as _e:
    import FreeCAD as _App
    _App.Console.PrintWarning(
        f"FreeCAD AI: preferences page not registered: {_e}\n")
```

Leave the param-store seeding block below it for Task 6.

Delete `get_prefs_ui_path` from `freecad_ai/paths.py`. Delete the `.ui` with `git rm resources/panels/FreeCADAIPrefs.ui`. If `resources/panels/` is then empty, git drops it. Check `package.xml` for a reference to the file or the directory with `grep -n "panels\|FreeCADAIPrefs" package.xml`, and remove it if there is one.

`tests/unit/test_config.py`'s `test_prefs_combo_and_settings_dialog_list_the_same_providers` parses the deleted `.ui`. Delete it now; the rest of the bridge tests go in Task 6. Its successor is `test_the_provider_list_is_the_registry` in `test_provider_section.py`.

- [ ] **Step 5: Run the tests and watch them pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_prefs_page.py -q`
Expected: all pass. Then run the full suite. Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add freecad_ai/ui/prefs_page.py InitGui.py freecad_ai/paths.py tests/unit/test_prefs_page.py tests/unit/test_config.py
git rm resources/panels/FreeCADAIPrefs.ui
git commit -m "feat(prefs): preferences page shares the Settings dialog's provider sections (#99)"
```

---

### Task 6: Retire the parameter-store bridge, with a one-time migration

**Files:**
- Modify: `freecad_ai/config.py`:
  - `load_config` and `save_config`, including their docstrings;
  - the parameter-store block (`_PARAM_*`, `_apply_param_store_overrides`, `_write_to_param_store`).
- Modify: `InitGui.py` (delete the param-store seeding block)
- Modify: `freecad_ai/llm/providers.py` (the order comment above `PROVIDERS`)
- Test: `tests/unit/test_config.py`: replace class `TestParamStoreBridge` with `TestParamStoreMigration`.

**Interfaces:**
- Produces:
  - `_migrate_param_store(cfg) -> None`;
  - `_remove_param_group() -> None`;
  - frozen tuples `_LEGACY_PARAM_PROVIDERS`, `_LEGACY_PARAM_MODES`, `_LEGACY_PARAM_THINKING`.
  - `_get_param_group()` stays as it is.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_config.py`, replace the whole `TestParamStoreBridge` class. Before deleting it, check that every test in it exercises `_apply_param_store_overrides`, `_write_to_param_store` or `_PARAM_*`. A test that does not is not a bridge test: move it out and keep it. Keep the class's `_fake_param_group` helper unchanged, and add the class below.

```python
class TestParamStoreMigration:
    """#99: the preferences page writes config.json itself. A value changed
    there under an old version, and not yet mirrored, is applied once; the
    group is then removed, and its absence is the marker."""

    # _fake_param_group: copy the helper from the old TestParamStoreBridge
    # verbatim here.

    def _run(self, monkeypatch, group):
        removed = []

        def remove():
            removed.append(True)
            for store in self._stores:
                store.clear()

        monkeypatch.setattr(config_mod, "_get_param_group", lambda: group)
        monkeypatch.setattr(config_mod, "_remove_param_group", remove)
        return removed

    def _group(self, **kw):
        group, ints, strings, bools = self._fake_param_group(**kw)
        self._stores = (ints, strings, bools)
        return group

    def test_every_field_is_applied_saved_and_the_group_removed(
            self, tmp_config_dir, monkeypatch):
        group = self._group(
            ints={"ProviderIndex": 2, "ModeIndex": 0, "ThinkingIndex": 1,
                  "MaxTokens": 9000},
            strings={"Model": "qwen3:8b", "BaseUrl": "http://h:11434/v1",
                     "ApiKey": "sk-x"},
            bools={"EnableTools": False})
        removed = self._run(monkeypatch, group)
        cfg = config_mod.load_config()
        assert (cfg.provider.name, cfg.provider.model) == ("ollama", "qwen3:8b")
        assert cfg.provider.base_url == "http://h:11434/v1"
        assert cfg.provider.api_key == "sk-x"
        assert (cfg.mode, cfg.thinking) == ("plan", "on")
        assert (cfg.max_tokens, cfg.enable_tools) == (9000, False)
        with open(config_mod.CONFIG_FILE) as f:
            assert "qwen3:8b" in f.read()
        assert removed == [True]

    def test_an_empty_group_is_a_no_op(self, tmp_config_dir, monkeypatch):
        removed = self._run(monkeypatch, self._group())
        config_mod.load_config()
        assert not os.path.exists(config_mod.CONFIG_FILE)
        assert removed == []

    def test_the_second_load_is_a_no_op(self, tmp_config_dir, monkeypatch):
        removed = self._run(monkeypatch, self._group(strings={"Model": "m"}))
        config_mod.load_config()
        mtime = os.path.getmtime(config_mod.CONFIG_FILE)
        config_mod.load_config()
        assert removed == [True]
        assert os.path.getmtime(config_mod.CONFIG_FILE) == mtime

    def test_no_provider_index_leaves_the_provider_alone(
            self, tmp_config_dir, monkeypatch):
        """#12: a missing key is not index 0."""
        self._run(monkeypatch, self._group(strings={"Model": "m"}))
        assert config_mod.load_config().provider.name == "anthropic"

    def test_out_of_range_indices_are_ignored(self, tmp_config_dir, monkeypatch):
        self._run(monkeypatch, self._group(
            ints={"ProviderIndex": 99, "ModeIndex": 99, "ThinkingIndex": 99}))
        cfg = config_mod.load_config()
        assert cfg.provider.name == "anthropic"
        assert cfg.mode == config_mod.AppConfig().mode
        assert cfg.thinking == config_mod.AppConfig().thinking

    def test_the_legacy_order_is_frozen(self, tmp_config_dir, monkeypatch):
        """Stored indices are positional: 11 was cloudflare before #97, 21
        is custom since #97. Reordering the registry must not move them."""
        self._run(monkeypatch, self._group(ints={"ProviderIndex": 21}))
        assert config_mod.load_config().provider.name == "custom"
        assert config_mod._LEGACY_PARAM_PROVIDERS[11] == "cloudflare-workers-ai"
        assert len(config_mod._LEGACY_PARAM_PROVIDERS) == 22

    def test_a_failed_save_keeps_the_group_for_next_time(
            self, tmp_config_dir, monkeypatch, caplog):
        """Review Focus 5: startup must not fail, and nothing is lost."""
        removed = self._run(monkeypatch, self._group(strings={"Model": "m"}))

        def disk_full(_cfg):
            raise OSError("No space left on device")

        monkeypatch.setattr(config_mod, "save_config", disk_full)
        cfg = config_mod.load_config()
        assert cfg.provider.model == "m"
        assert removed == []
        assert "No space left" in caplog.text

    def test_outside_freecad_nothing_happens(self, tmp_config_dir):
        config_mod.load_config()          # _get_param_group() returns None
        assert not os.path.exists(config_mod.CONFIG_FILE)

    def test_saving_no_longer_touches_the_param_store(
            self, tmp_config_dir, monkeypatch):
        group = self._group()
        monkeypatch.setattr(config_mod, "_get_param_group", lambda: group)
        config_mod.save_config(config_mod.AppConfig())
        assert self._stores == ({}, {}, {})
```

Make sure `import os` and `import freecad_ai.config as config_mod` exist at the top of `test_config.py`, and add them if not.

- [ ] **Step 2: Run them and watch them fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_config.py::TestParamStoreMigration -q`
Expected: FAIL. `_remove_param_group` and `_LEGACY_PARAM_PROVIDERS` don't exist yet, and `test_saving_no_longer_touches_the_param_store` fails because `save_config` still mirrors.

- [ ] **Step 3: Implement**

In `config.py`, replace the whole parameter-store block, from the `# ── FreeCAD parameter-store bridge` header through the end of `_write_to_param_store`, with:

```python
# ── One-time migration off FreeCAD's parameter store (#99) ──────────────
#
# Until #99, Edit → Preferences was a .ui of Gui::Pref* widgets storing
# eight values under BaseApp/Preferences/Mod/FreeCADAI, mirrored into
# config.json on each load. The page now writes config.json itself. A
# value changed there under an old version, and not yet mirrored, exists
# only in the store — so apply it once, then remove the group. Its absence
# is the marker; no version flag is needed.
#
# Frozen copies of the old combos' item orders. The stored indices are
# positional, so these never change, whatever happens to PROVIDERS.
_LEGACY_PARAM_PROVIDERS = (
    "anthropic", "openai", "ollama", "gemini", "openrouter",
    "moonshot", "deepseek", "qwen", "groq", "mistral", "together",
    "cloudflare-workers-ai",
    # appended in #97
    "fireworks", "xai", "cohere", "sambanova", "minimax", "llama",
    "github", "huggingface", "zhipu", "custom",
)
_LEGACY_PARAM_MODES = ("plan", "act")
_LEGACY_PARAM_THINKING = ("off", "on", "extended")


def _get_param_group():
    """Return the FreeCAD ParamGet group, or None when running outside FreeCAD."""
    try:
        import FreeCAD
        return FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/FreeCADAI")
    except (ImportError, RuntimeError):
        return None


def _remove_param_group() -> None:
    """Delete Mod/FreeCADAI from FreeCAD's user.cfg — the API key with it."""
    try:
        import FreeCAD
        FreeCAD.ParamGet(
            "User parameter:BaseApp/Preferences/Mod").RemGroup("FreeCADAI")
    except (ImportError, RuntimeError, AttributeError):
        pass


def _migrate_param_store(cfg: AppConfig) -> None:
    """Apply what an old preferences page left in the store, once.

    Only keys present are applied: #12 — a missing ProviderIndex is not
    "index 0", it means the page never wrote one. Out-of-range indices are
    ignored. A failed save keeps the group, so the next start retries.
    """
    group = _get_param_group()
    if group is None:
        return
    keys = set(group.GetStrings()) | set(group.GetInts()) | set(group.GetBools())
    if not keys:
        return

    if "ProviderIndex" in keys:
        idx = group.GetInt("ProviderIndex", 0)
        if 0 <= idx < len(_LEGACY_PARAM_PROVIDERS):
            cfg.provider.name = _LEGACY_PARAM_PROVIDERS[idx]
    if "Model" in keys:
        cfg.provider.model = group.GetString("Model", cfg.provider.model)
    if "BaseUrl" in keys:
        cfg.provider.base_url = group.GetString("BaseUrl", cfg.provider.base_url)
    if "ApiKey" in keys:
        cfg.provider.api_key = group.GetString("ApiKey", cfg.provider.api_key)
    if "ModeIndex" in keys:
        idx = group.GetInt("ModeIndex", 0)
        if 0 <= idx < len(_LEGACY_PARAM_MODES):
            cfg.mode = _LEGACY_PARAM_MODES[idx]
    if "ThinkingIndex" in keys:
        idx = group.GetInt("ThinkingIndex", 0)
        if 0 <= idx < len(_LEGACY_PARAM_THINKING):
            cfg.thinking = _LEGACY_PARAM_THINKING[idx]
    if "MaxTokens" in keys:
        cfg.max_tokens = group.GetInt("MaxTokens", cfg.max_tokens)
    if "EnableTools" in keys:
        cfg.enable_tools = group.GetBool("EnableTools", cfg.enable_tools)

    try:
        save_config(cfg)
    except OSError as e:
        logger.warning(
            "FreeCAD AI: could not save preferences migrated from FreeCAD's "
            "parameter store (%s); will retry at next start", e)
        return
    _remove_param_group()
```

In `load_config`, replace the two calls `_apply_param_store_overrides(cfg)` and `_write_to_param_store(cfg)` with `_migrate_param_store(cfg)`. Replace the docstring's two paragraphs about the parameter store with:

```
    Then runs the one-time migration of values an old Edit → Preferences
    page left in FreeCAD's parameter store (#99).
```

In `save_config`, delete the final `_write_to_param_store(config)` line and change the docstring's first line to `"""Save configuration to disk.`.

In `InitGui.py`, delete the block that starts `# Seed the FreeCAD parameter store from JSON so the preferences page shows` and ends with `_gcfg()` / `except Exception: pass`.

In `freecad_ai/llm/providers.py`, replace the four-line order comment above `PROVIDERS` with:

```python
# Order is the Settings dialog's provider list and nothing else: since #99
# no position is stored anywhere. (The parameter-store positions from
# before #99 live on, frozen, in config._LEGACY_PARAM_PROVIDERS.)
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_config.py -q`
Expected: all pass. Then run `grep -rn "_PARAM_PROVIDERS\|_apply_param_store_overrides\|_write_to_param_store\|get_prefs_ui_path\|FreeCADAIPrefs.ui" --include=*.py .`. Expected: only `_LEGACY_PARAM_PROVIDERS` hits. Then run the full suite. Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/config.py freecad_ai/llm/providers.py InitGui.py tests/unit/test_config.py
git commit -m "refactor(config): retire the parameter-store bridge, migrate once (#99)"
```

---

### Task 7: CHANGELOG and wiki

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]`)
- Modify: pages in `/home/alf/Projects/programming/misc/freecad-ai-wiki` (a separate git repo)

- [ ] **Step 1: CHANGELOG**

Under `## [Unreleased]`, add a `### Changed` section after the existing `### Added` section, or reuse one if it exists:

```markdown
### Changed

- **Edit → Preferences → FreeCAD AI now shows profiles and utility models**,
  the same *LLM Provider* and *Utility models* sections as the Settings
  dialog, built from one shared widget so the two can no longer differ.
  An OK in Preferences that changed nothing on this page writes nothing
  (#99).
- **FreeCAD's `user.cfg` no longer holds FreeCAD AI settings.** They used
  to be mirrored under `BaseApp/Preferences/Mod/FreeCADAI`; `config.json`
  is now the only store. A value changed in Preferences under an older
  version is carried over once at the first start, and the group — API
  key included — is then removed. Scripts reading
  `ParamGet(".../Mod/FreeCADAI")` stop seeing values (#99).
```

Read the existing #97 entry under `### Fixed`. If it describes the preferences combo's item list as the fix, add one sentence at its end: "Since #99 the page shares the Settings dialog's provider list, so the lists cannot diverge again."

- [ ] **Step 2: Wiki**

Run `grep -n -i "preferences\|ParamGet\|user.cfg\|param store\|parameter store" /home/alf/Projects/programming/misc/freecad-ai-wiki/*.md`. For each hit that describes the old page (provider, model, base URL, API key, mode, thinking, max tokens, tools) or the mirror, rewrite it to match the new page:
- the page shows profiles with New/Rename/Delete, "Use this profile for chat", provider, API key, base URL, model, vision, the utility-model dropdowns, and the Behavior group;
- the page and the Settings dialog edit the same profiles;
- `config.json` is the only store.

Leave hits about FreeCAD's own Preferences unrelated to FreeCAD AI untouched. Commit in the wiki repo. **Do not push.** The maintainer decides at PR time.

```bash
git -C /home/alf/Projects/programming/misc/freecad-ai-wiki commit -am "Preferences page shows profiles and utility models (#99)"
```

(Skip the wiki commit if the grep finds nothing relevant, and say so in the PR body.)

- [ ] **Step 3: Commit the CHANGELOG**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): preferences page shares the provider sections (#99)"
```

---

### Task 8: Live verification on FreeCAD 1.1.1, then the PR

**Files:**
- Create (scratchpad only, not committed): `$SCRATCH/probe99/probe_seed.py`, `probe_check.py`, `run.sh`

- [ ] **Step 1: Write the probe harness**

`$SCRATCH` is the session scratchpad directory. In `run.sh`:

```bash
#!/bin/bash
# Isolated FreeCAD run: never the maintainer's real config.
set -e
ROOT="$SCRATCH/probe99"
export HOME="$ROOT/home" XDG_CONFIG_HOME="$ROOT/xdg-config" \
       XDG_DATA_HOME="$ROOT/xdg-data" FREECAD_AI_CONFIG_DIR="$ROOT/fcai"
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME/FreeCAD/v1-1/Mod" "$FREECAD_AI_CONFIG_DIR"
ln -sfn /home/alf/Projects/programming/misc/freecad-ai "$XDG_DATA_HOME/FreeCAD/v1-1/Mod/freecad-ai"
timeout 180 xvfb-run -a env QT_QPA_PLATFORM=xcb \
  FreeCAD_1.1.1-Linux-x86_64-py311.AppImage "$@"
```

Before the first run, seed `$ROOT/fcai/config.json` with two profiles, `cloud` (anthropic, active) and `local` (ollama). Take the AppImage path from `~/bin/freecad` if it isn't on `PATH`.

`probe_seed.py` (run 1) writes an old-style parameter store and exits:

```python
import os
import FreeCAD
g = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/FreeCADAI")
g.SetString("Model", "migrated-model")
g.SetInt("ProviderIndex", 0)
FreeCAD.saveParameter()
os._exit(0)
```

`probe_check.py` (runs 2–4) takes the scenario from an environment variable `PROBE` and writes every result line to `$ROOT/result-$PROBE.txt`:

```python
import hashlib, os
import FreeCAD, FreeCADGui as Gui
from PySide6 import QtCore, QtWidgets   # probe script only; product code uses compat
ROOT = os.path.join(os.environ["FREECAD_AI_CONFIG_DIR"], "..")
out = open(os.path.join(ROOT, f"result-{os.environ['PROBE']}.txt"), "w")
cfg_file = os.path.join(os.environ["FREECAD_AI_CONFIG_DIR"], "config.json")

def digest():
    return hashlib.sha256(open(cfg_file, "rb").read()).hexdigest()

import freecad_ai.config as config_mod
cfg = config_mod.get_config()               # runs the migration
g = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/FreeCADAI")
out.write(f"model={cfg.provider.model}\n")
out.write(f"group_keys={g.GetStrings() + g.GetInts() + g.GetBools()}\n")
before = digest()

if os.environ["PROBE"] == "edit":
    Gui.activateWorkbench("FreeCADAIWorkbench")
    Gui.runCommand("FreeCADAI_OpenChat")
    fired = []
    config_mod.add_config_listener(lambda: fired.append(1))

def act():
    dlg = next(w for w in QtWidgets.QApplication.topLevelWidgets()
               if w.metaObject().className() == "Gui::Dialog::DlgPreferencesImp")
    from freecad_ai.ui.provider_section import ProviderSection
    sections = dlg.findChildren(ProviderSection)
    out.write(f"sections={len(sections)}\n")
    if sections:
        s = sections[0]
        out.write("profiles=" + ",".join(
            s.profile_combo.itemData(i) for i in range(s.profile_combo.count())) + "\n")
        if os.environ["PROBE"] == "edit":
            s.model_edit.setText("edited-model")
    dlg.accept()

QtCore.QTimer.singleShot(1500, act)
page = "General" if os.environ["PROBE"] == "general" else "FreeCAD AI"
Gui.showPreferences(page, 0)                # modal until act() accepts
out.write(f"identical={digest() == before}\n")
if os.environ["PROBE"] == "edit":
    out.write(f"saved_edit={'edited-model' in open(cfg_file).read()}\n")
    out.write(f"listener_fired={bool(fired)}\n")
    from freecad_ai.ui.chat_widget import ChatDockWidget
    panels = Gui.getMainWindow().findChildren(ChatDockWidget)
    out.write(f"panel_saw_edit={bool(panels) and panels[0]._config_seen[1] == 'edited-model'}\n")
out.close()
os._exit(0)
```

If `Gui.showPreferences(name, 0)` doesn't select the FreeCAD AI group on 1.1.1, use `Gui.showPreferencesByName("FreeCAD AI", ...)`. Check the signature with `help(Gui.showPreferencesByName)` in a one-line probe first.

- [ ] **Step 2: Run the scenarios and read each result file**

```bash
bash run.sh probe_seed.py
PROBE=page    bash run.sh probe_check.py   # migration + untouched OK on our page
PROBE=general bash run.sh probe_check.py   # untouched OK opened on General
PROBE=edit    bash run.sh probe_check.py   # an edit saves and the chat panel hears it
```

Expected lines:
- `result-page.txt`: `model=migrated-model`, `group_keys=[]`, `sections=1`, `profiles=cloud,local`, `identical=True`.
- `result-general.txt`: `identical=True`.
- `result-edit.txt`: `sections=1`, `saved_edit=True`, `listener_fired=True`, `panel_saw_edit=True`.

Also run `grep -c FreeCADAI "$ROOT/xdg-config/FreeCAD/user.cfg"`. Expected: `0`.

Quote every line from the result files in the PR body. If a line differs from the expected value, stop and debug with superpowers:systematic-debugging. Don't adjust the expectation.

- [ ] **Step 3: Full suite one last time**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py`
Expected: all green. Record the exact count for the PR body.

- [ ] **Step 4: Push and open the PR**

```bash
git push -u origin feat/99-prefs-provider-section
gh pr create --base master --title "feat(prefs): preferences page shares the Settings dialog's provider sections (#99)" --body-file "$SCRATCH/probe99/pr-body.md"
```

`pr-body.md` must contain:
- the opening line "I'm an AI assistant handling this on Alfred's behalf.";
- `Closes #99`;
- a summary of the four tasks' user-visible effects;
- the four deliberate behavior changes from Global Constraints;
- the quoted live-probe lines;
- the test count;
- the note that the wiki commit is local and unpushed;
- the maintainer GUI check: open Preferences → FreeCAD AI and the Settings dialog side by side, then compare the two sections;
- the last line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

The maintainer merges.
