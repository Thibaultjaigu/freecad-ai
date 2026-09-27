# Settings Frame Implementation Plan (#101)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Edit → Preferences → FreeCAD AI and the Settings dialog show exactly the same settings from the same widgets: four shared pages. The dialog becomes a thin frame around them, and a profile whose provider the dropdown doesn't know is no longer shown as "Anthropic".

**Architecture:** A new package `freecad_ai/ui/settings_pages/` has a `SettingsPage` base class and four page widgets (`ProviderPage`, `BehaviorPage`, `ToolsPage`, `McpPage`), built from code moved out of `settings_dialog.py`.

- Each page records a baseline on `load()`.
- On save, each page writes only its changed fields onto the live `get_config()` singleton.
- `SettingsDialog` hosts the four pages in one scroll area. `prefs_page.py` wraps each page in its own FreeCAD preference-page class.

**Tech Stack:** Python 3.11, PySide6/PySide2 through `freecad_ai/ui/compat.py`, pytest with offscreen Qt, FreeCAD 1.1.1 AppImage under Xvfb for the live probe.

**Spec:** `docs/superpowers/specs/2026-09-27-settings-frame-design.md`. Read it before starting any task: it is the authority, and this plan argues from it.

## Global Constraints

- **Qt imports:** always through `freecad_ai/ui/compat.py` (`from ..compat import QtWidgets, QtCore, QtGui`). Never import PySide2 or PySide6 directly. Use the **flat** enum form (`QtCore.Qt.Checked`, `QMessageBox.Yes`, `QtWidgets.QFrame.NoFrame`).
- **Translations:** every moved or new string keeps translation context `"SettingsDialog"`. The only new strings are:
  - "Limits"
  - "Use tool calling (uncheck to fall back to code generation)"
  - "Built-in MCP Server"
  - "%s (unknown provider)"
  - the four page titles "Provider", "Behavior", "Tools", "MCP"
- **Page classes:** each Preferences class has a distinct module-level `__name__`: `FreeCADAIProviderPrefs`, `FreeCADAIBehaviorPrefs`, `FreeCADAIToolsPrefs`, `FreeCADAIMcpPrefs`. They share one base class, never a factory, because FreeCAD keys Python preference pages by class name.
- **Lazy Qt in `prefs_page.py`:** InitGui imports it at FreeCAD startup, so it must stay importable without building widgets. `test_importing_the_module_builds_no_qt_widgets` pins this. Import the page modules inside methods.
- **Test command:** `env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py`. It must be green before every commit.
- **Bug fixes:** write the regression test first, and watch it fail.
- **Commit trailer:** end every commit message with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Use conventional-commit style with `(#101)`, for example `feat(settings): … (#101)`.
- **Branch:** `feat/101-settings-frame`. Never commit on `master`.
- **FreeCAD config:** never touch the maintainer's real FreeCAD or FreeCAD AI config. The live probe uses isolated HOME, XDG and `FREECAD_AI_CONFIG_DIR` under the scratchpad.
- **Unused code:** remove only the imports and helpers that *your* changes made unused.
- **Save flow:** everything that writes goes onto `get_config()` at save time, never onto a load-time snapshot.

## Rulings made while planning (not in the spec)

1. **Test Connection uses the saved `max_tokens` / `thinking`.** Those widgets move to `BehaviorPage`, so reading them from `ProviderPage` would be a cross-page link, which Decision 3 rejects. With this ruling, the Preferences window and the dialog probe with the same values. The cost: an unsaved edit to Max Output Tokens or Thinking in the dialog isn't used by the probe until OK.
   - `test_max_tokens_comes_from_the_spinbox` and `test_thinking_comes_from_the_combo` in `tests/unit/test_test_connection_config_scope.py` are rewritten to assert the values come from `get_config()`.
   - **Flagged to the maintainer at handoff.** If they want the old behavior, the alternative is an optional `probe_settings` callable on `ProviderPage`: the dialog wires it to `BehaviorPage`, and Preferences falls back to the config.
2. **Probe threads are parented to `QApplication.instance()`, not the page.** Today the dialog disables Save and Cancel while a probe runs, so a running `QThread` never outlives its parent. FreeCAD's Preferences OK/Cancel can't be disabled from a page, and destroying a running `QThread` aborts the process. Parenting the thread to the application keeps it alive past the page.
   - PySide disconnects slots bound to a deleted `QObject`, so a late result never reaches a dead page.
   - `ProviderPage.busyChanged = Signal(bool)` still lets the dialog disable its own OK/Cancel as today.
3. **Editor fallback is decided before the prompt, not after.** The spec says: "`closeHostRequested(save)` → … otherwise open the file with the external editor instead". `ToolsPage` gets a `host_can_close` callable attribute, default `lambda: True`. The Preferences wrapper sets it to `lambda: isinstance(self.form.window(), QDialog)`.
   - When it returns False, Edit/New open externally with no prompt, which is the same outcome with no half-closed state.
   - `closeHostRequested` is emitted only when the host can close.
4. **The dialog keeps `self.provider_section`** as an attribute pointing at `provider_page.section`. Several geometry, screenshot and section-wiring tests reach the section through it, so this keeps the churn down.
5. **`SettingsPage.load(cfg, label=None)` / `view_label()`.** Only `ProviderPage` uses `label`. The Preferences wrapper re-baselines with `page.load(cfg, label=page.view_label())`, so Apply stays on the profile being edited (#100's behavior). The base `view_label()` returns `None`.

## Review Focus

The inputs and failure modes the spec implies but no task's tests would otherwise exercise, most likely first. Each has a test in the task named.

1. **Closing Preferences while Test Connection runs:** no crash, and a late result is not delivered to a dead page. Covered in Task 7: the thread's parent is the application, not the page.
2. **Two Preferences pages edited in one OK:** FreeCAD calls `saveSettings()` on all four pages, and each must keep the others' edits even though each reloads after saving. Covered in Task 9, with Provider and MCP edited before one OK.
3. **The Tools page's editor prompt inside Preferences when the host is not a `QDialog`:** no prompt, and the file opens externally. Covered in Task 9.
4. **A hand-edited value that a combo can't show** (`thinking: "max"`, `viewport_capture: "sometimes"`, `rerank_method: "semantic"`) survives an unrelated OK in the dialog. The dialog used to overwrite it. Covered in Tasks 5 and 6.
5. **Unknown provider, then switching profiles and back:** the temporary item never duplicates, and never lingers on a known-provider profile. Covered in Task 2.

---

## File Structure

| File | Responsibility |
|---|---|
| `freecad_ai/ui/settings_pages/__init__.py` (new) | Package marker. Re-exports the four page classes. |
| `freecad_ai/ui/settings_pages/base.py` (new) | `SettingsPage(QWidget)`: `closeHostRequested`, baseline, `load` / `is_dirty` / `apply_to` / `after_save` / `view_label`. |
| `freecad_ai/ui/settings_pages/provider_page.py` (new) | `ProviderPage`: `ProviderSection`, Model Parameters table, Test Connection row, Test Reranker row. `_TestConnectionThread` and `_TestRerankerThread` move here. |
| `freecad_ai/ui/settings_pages/behavior_page.py` (new) | `BehaviorPage`: Limits, Behavior (including viewport), System Prompt. |
| `freecad_ai/ui/settings_pages/tools_page.py` (new) | `ToolsPage`: Tool Reranking, User Tools, Skills, Hooks, Editor. |
| `freecad_ai/ui/settings_pages/mcp_page.py` (new) | `McpPage`: MCP Servers, Built-in MCP Server. `_AddMCPServerDialog` moves here. |
| `freecad_ai/ui/provider_section.py` (modify) | Unknown-provider temporary item. #10 reranker default recorded on switch and applied in `apply_to`. |
| `freecad_ai/ui/settings_dialog.py` (shrinks to about 150 lines) | The frame: scroll area, four pages, OK/Cancel, #78 sizing, placeholder veto. |
| `freecad_ai/ui/prefs_page.py` (rewrite) | `_PrefsPageBase` plus the four page classes. |
| `InitGui.py:355-363` (modify) | Register the four classes. |

**Page order inside every task:** add the page, host it in the dialog (its `load` and `apply_to` replace the dialog's per-field code for that page), and move its tests. Steps 3–7 keep the suite green because the dialog keeps working throughout.

---

### Task 1: `SettingsPage` base class

**Files:**
- Create: `freecad_ai/ui/settings_pages/__init__.py`
- Create: `freecad_ai/ui/settings_pages/base.py`
- Test: `tests/unit/test_settings_page_base.py`

**Interfaces:**
- Produces:
  - `SettingsPage(QWidget)` with `closeHostRequested = Signal(bool)` and `_baseline: dict | None`.
  - Methods: `load(cfg, label=None) -> None`, `is_dirty() -> bool`, `apply_to(cfg) -> None`, `after_save(cfg) -> None`, `view_label() -> str | None`.
  - Hooks for subclasses: `_show(cfg, label) -> None` (required) and `_values() -> dict` (defaults to `{}`).

- [ ] **Step 1: Write the failing test**

```python
"""SettingsPage: the baseline/diff contract every settings page shares (#101)."""

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

from freecad_ai.config import AppConfig  # noqa: E402
from freecad_ai.ui.settings_pages.base import SettingsPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


class _Page(SettingsPage):
    """Two fields, held in plain attributes rather than widgets."""

    def _show(self, cfg, label):
        self.max_tokens = cfg.max_tokens
        self.pinned = list(cfg.rerank_pinned_tools)

    def _values(self):
        return {"max_tokens": self.max_tokens,
                "rerank_pinned_tools": self.pinned}


class _Broken(SettingsPage):
    def _show(self, cfg, label):
        raise RuntimeError("load failed")


@pytest.fixture
def page(qapp):
    p = _Page()
    yield p
    p.deleteLater()


def test_a_never_loaded_page_is_clean_and_writes_nothing(page):
    cfg = AppConfig()
    before = cfg.to_dict()
    assert page.is_dirty() is False
    page.apply_to(cfg)
    assert cfg.to_dict() == before


def test_an_untouched_load_applies_nothing(page):
    src = AppConfig()
    src.max_tokens = 9999
    page.load(src)
    target = AppConfig()
    target.max_tokens = 1234   # someone else's newer value
    page.apply_to(target)
    assert target.max_tokens == 1234
    assert page.is_dirty() is False


def test_only_the_edited_field_is_written(page):
    src = AppConfig()
    page.load(src)
    page.max_tokens = 8192
    target = AppConfig()
    target.rerank_pinned_tools = ["other_edit"]
    page.apply_to(target)
    assert target.max_tokens == 8192
    assert target.rerank_pinned_tools == ["other_edit"]
    assert page.is_dirty() is True


def test_a_reverted_edit_is_clean(page):
    src = AppConfig()
    page.load(src)
    page.max_tokens = 8192
    page.max_tokens = src.max_tokens
    assert page.is_dirty() is False


def test_applied_values_are_copies(page):
    page.load(AppConfig())
    page.pinned.append("x")
    target = AppConfig()
    page.apply_to(target)
    page.pinned.append("y")
    assert target.rerank_pinned_tools == ["x"]


def test_a_failed_load_leaves_the_baseline_none(qapp):
    p = _Broken()
    with pytest.raises(RuntimeError):
        p.load(AppConfig())
    assert p._baseline is None
    assert p.is_dirty() is False
    p.deleteLater()


def test_the_defaults(page):
    assert page.view_label() is None
    page.after_save(AppConfig())   # no-op, must not raise
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_settings_page_base.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'freecad_ai.ui.settings_pages'`.

- [ ] **Step 3: Write the implementation**

`freecad_ai/ui/settings_pages/__init__.py`:

```python
"""The settings pages both settings windows embed (#101).

Each page is one QWidget owning a slice of config.json. The Settings dialog
stacks all four in a scroll area; Edit → Preferences shows each as its own
page under "FreeCAD AI". Neither window holds settings logic of its own, so
the two cannot drift apart again (#12, #97).
"""
```

(The re-exports are added in Task 8, once all four modules exist.)

`freecad_ai/ui/settings_pages/base.py`:

```python
"""SettingsPage: load a baseline, write back only what changed.

apply_to() writes onto the config it is given, the live singleton at save
time, and only the fields that differ from what load() showed. FreeCAD
calls saveSettings() on every Preferences page in turn, so a page that
wrote all its fields would undo the edits a page before it just saved.
The same rule keeps a hand-edited value a combo cannot show (say
thinking: "max") from being overwritten by an unrelated OK.
"""

import copy

from ..compat import QtCore, QtWidgets


class SettingsPage(QtWidgets.QWidget):
    # True = save, then close the host window; False = discard and close.
    # Raised by the editor prompt (ToolsPage), since FreeCAD's docked script
    # editor is unreachable behind a modal window.
    closeHostRequested = QtCore.Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        # None, not {}: "no successful load" — apply_to writes nothing (#99).
        self._baseline = None

    def load(self, cfg, label=None):
        """Show ``cfg`` and record the baseline. Raises if showing fails,
        leaving the baseline None so the page writes nothing."""
        self._baseline = None
        self._show(cfg, label)
        self._baseline = copy.deepcopy(self._values())

    def is_dirty(self) -> bool:
        return self._baseline is not None and self._values() != self._baseline

    def apply_to(self, cfg) -> None:
        if self._baseline is None:
            return
        for key, value in self._values().items():
            if self._baseline.get(key) != value:
                setattr(cfg, key, copy.deepcopy(value))

    def after_save(self, cfg) -> None:
        """Side effects that must follow a save in either window."""

    def view_label(self):
        """What to keep showing across a re-baseline (ProviderPage only)."""
        return None

    def _show(self, cfg, label) -> None:
        raise NotImplementedError

    def _values(self) -> dict:
        return {}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_settings_page_base.py -q`
Expected: 7 passed.

- [ ] **Step 5: Run the full suite and commit**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py`. Expected: all green.

```bash
git add freecad_ai/ui/settings_pages tests/unit/test_settings_page_base.py
git commit -m "feat(settings): SettingsPage base with a field-level baseline (#101)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Unknown provider shown as itself, not as Anthropic

**Files:**
- Modify: `freecad_ai/ui/provider_section.py`: `__init__`, `_commit_profile_fields`, `_show_profile`, `_on_provider_changed`
- Test: `tests/unit/test_provider_section.py`, extending `class TestUnknownProvider`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `ProviderSection._unknown_item_index() -> int | None`, the index of the temporary item or None.
  - `ProviderSection._stand_in_index` is gone.

This is a bug fix, so the regression tests come first.

- [ ] **Step 1: Write the failing tests.** Append them inside `class TestUnknownProvider` in `tests/unit/test_provider_section.py`. Existing tests there stay unchanged and must keep passing.

```python
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
```

Before you write this, check how `test_provider_section.py` picks profiles: if the profile combo's item data is not the label, use the helper the neighboring tests use (for example `TestDirty.test_browsing_profiles_is_clean`). Also check that `from freecad_ai.config import PROVIDER_PRESETS` is already imported at the top; it is.

- [ ] **Step 2: Run to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_provider_section.py -q -k Unknown`
Expected: the 5 new display/switch tests FAIL, because the combo shows index 0 and has no extra item. `test_current_provider_name_is_empty_while_unknown` FAILs too, because index 0 returns `"anthropic"`.

- [ ] **Step 3: Implement.** In `provider_section.py`:

Replace `self._stand_in_index = None` in `__init__` with nothing, because the temporary item's position is derived, not stored.

Add these helpers next to `_show_profile`:

```python
    def _unknown_item_index(self):
        """Index of the temporary "<name> (unknown provider)" item, if any.

        It is always the last item, past the registry's providers, so the
        registry indices every other method relies on never shift.
        """
        n = len(get_provider_names())
        return n if self.provider_combo.count() > n else None

    def _drop_unknown_item(self):
        idx = self._unknown_item_index()
        if idx is not None:
            self.provider_combo.blockSignals(True)
            try:
                self.provider_combo.removeItem(idx)
            finally:
                self.provider_combo.blockSignals(False)
```

Replace the `names.index` try/except in `_show_profile` with:

```python
        names = get_provider_names()
        self._drop_unknown_item()
        if prof.name in names:
            idx = names.index(prof.name)
        else:
            # A provider the registry lacks (a hand edit, or a config from a
            # newer version) is shown as itself, never as a real provider it
            # is not, which would make picking that provider a no-op.
            self.provider_combo.blockSignals(True)
            try:
                self.provider_combo.addItem(
                    translate("SettingsDialog", "%s (unknown provider)")
                    % prof.name, prof.name)
            finally:
                self.provider_combo.blockSignals(False)
            idx = len(names)
```

(The existing block-signalled `setCurrentIndex(idx)` that follows stays.)

In `_commit_profile_fields`, replace the stand-in comment and `if` with nothing, because `names[idx] if 0 <= idx < len(names) else prof.name` already keeps the name while the temporary item (index `len(names)`) is selected. Adjust the comment above that line:

```python
        # Index past the registry = the temporary unknown-provider item:
        # not a choice, so the stored name stays (#97 in another shape).
        new_name = names[idx] if 0 <= idx < len(names) else prof.name
```

In `_on_provider_changed`, after the early return for an out-of-range index, add `self._drop_unknown_item()` before the preset is applied. A real pick ends the temporary item.

Keep the combo's other items as they are. Check how `_build_ui` fills `provider_combo`: if it uses `addItems(...)` with no item data, `currentData()` on real items is None, which is fine because nothing reads data except the new test.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_provider_section.py tests/unit/test_profile_selector.py tests/unit/test_prefs_page.py -q`
Expected: all pass. If a `test_profile_selector.py` fake combo lacks `count`/`addItem`/`removeItem`, extend that fake rather than the production code. `_ModelCombo` at about line 229 carries a real item model.

- [ ] **Step 5: Full suite, then commit**

```bash
git add freecad_ai/ui/provider_section.py tests/unit/test_provider_section.py tests/unit/test_profile_selector.py
git commit -m "fix(settings): show an unknown provider as itself, not as the first entry (#101)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: #10 reranker defaults apply at save time

**Files:**
- Modify: `freecad_ai/ui/provider_section.py`: `__init__`, `load`, `apply_to`, `_on_provider_changed`
- Modify: `freecad_ai/ui/settings_dialog.py`: shrink `_on_preset_applied`; delete `_rerank_at_factory_defaults`, `_RERANK_METHOD_INDEX`, `_apply_rerank_defaults`
- Test: `tests/unit/test_provider_section.py` (new `class TestRerankDefaults`), `tests/unit/test_settings_dialog_provider_change.py`, `tests/unit/test_settings_dialog_section_wiring.py:81`

**Interfaces:**
- Produces: `ProviderSection._pending_rerank: dict | None`, set by a user switch and applied and cleared by `apply_to(cfg)`. The dialog and Preferences need no reranker code.

- [ ] **Step 1: Write the failing tests.** Append to `tests/unit/test_provider_section.py`:

```python
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

    def test_apply_consumes_the_record(self, section):
        """A second Apply after the user set 'off' again must not re-apply."""
        self._switch_to_github(section)
        section.apply_to(AppConfig())
        out = AppConfig()
        section.apply_to(out)
        assert (out.rerank_method, out.rerank_top_n) == ("off", 15)
```

The last test pins that `apply_to` clears the record after using it. Without that, Preferences' Apply → the Tools page sets "off" → Apply would re-apply the preset.

- [ ] **Step 2: Run to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_provider_section.py -q -k RerankDefaults`
Expected: `test_untouched_reranker_takes_the_preset` FAILs (the method stays "off"). The others pass trivially, which is fine: they guard the implementation.

- [ ] **Step 3: Implement in `provider_section.py`**

In `__init__`: `self._pending_rerank = None`.

In `load`, first line: `self._pending_rerank = None`.

In `_on_provider_changed`, after `preset = PROVIDER_PRESETS.get(...)`:

```python
        # #10: remember the preset's reranker recommendation; apply_to
        # decides at save time whether the reranker is still untouched.
        # Every user switch overwrites it, so the last switch wins.
        self._pending_rerank = dict(preset.get("default_rerank") or {}) or None
```

At the end of `apply_to`:

```python
        # Factory defaults = off + 15. Checked on the *live* config, so an
        # explicit reranker choice saved by the Tools page (in either order)
        # is never overwritten; an explicit off/15 is indistinguishable, as
        # it was when this check read the widgets.
        pending, self._pending_rerank = self._pending_rerank, None
        if (pending and cfg.rerank_method == "off"
                and cfg.rerank_top_n == 15):
            if pending.get("method") in ("off", "keyword", "llm"):
                cfg.rerank_method = pending["method"]
            if "top_n" in pending:
                cfg.rerank_top_n = int(pending["top_n"])
```

In `settings_dialog.py`:
- Delete the reranker half of `_on_preset_applied`, from the `# Apply provider-recommended reranker settings` comment through the `if` block.
- Update its docstring to "A user provider switch: reload the params table from the working copy."
- Delete `_rerank_at_factory_defaults`, `_RERANK_METHOD_INDEX` and `_apply_rerank_defaults`.

In the comment in `ProviderSection._on_provider_changed` that reads "The dialog reloads its params table and applies default_rerank (#10) here", drop "and applies default_rerank (#10)".

The dialog's `_save` already calls `section.apply_to(cfg)` *before* writing the rerank widgets, so in this interim state the dialog's own rerank write overwrites the preset. **That is the order-independence rule working:** the dialog wrote an explicit off/15. To keep the dialog behaving correctly until Task 5, move the `section.apply_to(cfg)` call in `_save` to just before `save_current_config()`. The widget writes then go first, and the section's factory-default check sees them.

- [ ] **Step 4: Update the old dialog tests.** In `test_settings_dialog_provider_change.py`:
- Delete the `_rerank_at_factory_defaults` / `_apply_rerank_defaults` attributes from the fake (lines 74–75).
- Delete the test that asserts `_apply_rerank_defaults` calls (about lines 144–156). It is superseded by `TestRerankDefaults`.

In `test_settings_dialog_section_wiring.py:81`, rewrite `test_a_provider_switch_applies_default_rerank_when_untouched` to go through a real save:
- switch the dialog's provider combo to github;
- monkeypatch `settings_dialog.save_current_config`, `settings_dialog.notify_config_changed` and `command_state.set_command_checked` to no-ops;
- call `dialog._save()`;
- assert `get_config().rerank_method == defaults["method"]` and `get_config().rerank_top_n == defaults["top_n"]`.

Rename it to `test_a_provider_switch_applies_default_rerank_on_save`.

The widgets don't flip before OK any more; that is the cost accepted in Decision 3. So the previous "the widget shows keyword" assertion is removed, not ported.

- [ ] **Step 5: Run and commit**

Run the full suite. Expected: green.

```bash
git add -A freecad_ai/ui tests/unit
git commit -m "refactor(settings): apply #10 reranker defaults at save time (#101)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `McpPage` with two group boxes

**Files:**
- Create: `freecad_ai/ui/settings_pages/mcp_page.py`
- Modify: `freecad_ai/ui/settings_dialog.py`: remove the MCP group from `_build_ui`, the MCP lines from `_load_from_config` and `_save`, the methods `_mcp_list_label`, `_parse_allowed_hosts`, `_parse_server_address`, `_generate_mcp_auth_token`, `_add_mcp_server`, `_edit_mcp_server`, `_remove_mcp_server`, and the class `_AddMCPServerDialog`
- Test: `tests/unit/test_mcp_page.py` (new); retarget `tests/unit/test_settings_dialog_mcp_address.py` and `tests/unit/test_mcp_add_server_dialog.py`

**Interfaces:**
- Consumes: `SettingsPage` (Task 1).
- Produces:
  - `McpPage(SettingsPage)` with widgets `mcp_list`, `mcp_server_host_edit`, `mcp_server_port_edit`, `mcp_server_allowed_hosts_edit`, `mcp_server_auth_token_edit`, and `_mcp_configs: list[dict]`.
  - Static methods `McpPage._parse_server_address(host_text, port_text) -> (str, int)`, `McpPage._parse_allowed_hosts(text) -> list[str]` and `McpPage._mcp_list_label(entry) -> str`.
  - `_AddMCPServerDialog` importable from `freecad_ai.ui.settings_pages.mcp_page`.

- [ ] **Step 1: Write the failing test** `tests/unit/test_mcp_page.py`:

```python
"""McpPage: the MCP client list and the built-in server settings (#101)."""

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

from freecad_ai.config import AppConfig  # noqa: E402
from freecad_ai.ui.settings_pages.mcp_page import McpPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def page(qapp):
    p = McpPage()
    yield p
    p.deleteLater()


def _cfg():
    c = AppConfig()
    c.mcp_servers = [{"name": "a", "command": "x", "args": []}]
    c.mcp_server_host = "127.0.0.1"
    c.mcp_server_port = 30000
    c.mcp_server_allowed_hosts = ["localhost"]
    c.mcp_server_auth_token = "tok"
    return c


def test_two_group_boxes(page):
    titles = [g.title() for g in page.findChildren(QtWidgets.QGroupBox)]
    assert titles == ["MCP Servers", "Built-in MCP Server"]


def test_untouched_load_writes_nothing(page):
    page.load(_cfg())
    target = _cfg()
    target.mcp_server_port = 4242     # a newer value from elsewhere
    page.apply_to(target)
    assert target.mcp_server_port == 4242
    assert page.is_dirty() is False


def test_retyping_the_same_host_is_not_a_change(page):
    page.load(_cfg())
    page.mcp_server_host_edit.setText("  127.0.0.1 ")
    page.mcp_server_allowed_hosts_edit.setText("localhost ,")
    assert page.is_dirty() is False


def test_a_port_edit_writes_only_the_port(page):
    page.load(_cfg())
    page.mcp_server_port_edit.setText("31000")
    target = _cfg()
    target.mcp_server_auth_token = "other"
    page.apply_to(target)
    assert target.mcp_server_port == 31000
    assert target.mcp_server_auth_token == "other"


def test_removing_a_server_is_a_change(page):
    page.load(_cfg())
    page.mcp_list.setCurrentRow(0)
    page._remove_mcp_server()
    target = _cfg()
    page.apply_to(target)
    assert target.mcp_servers == []


def test_an_empty_allowed_hosts_field_saves_an_empty_list(page):
    """Empty must stay empty (the transport default), not the loopback list."""
    page.load(_cfg())
    page.mcp_server_allowed_hosts_edit.setText("")
    target = _cfg()
    page.apply_to(target)
    assert target.mcp_server_allowed_hosts == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_page.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `mcp_page.py`.** Module header:

```python
"""MCP page: servers the workbench connects to, and its own MCP server."""

import secrets

from ..compat import QtWidgets, QtGui
from ...i18n import translate
from .base import SettingsPage
```

Then define the same local Qt aliases `settings_dialog.py` uses (`QGroupBox = QtWidgets.QGroupBox`, …) for the names the moved code references, including `QIntValidator = QtGui.QIntValidator`.

Build UI: `__init__` calls `super().__init__(parent)`, then `layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)`.

- **Group 1:** move the "MCP Servers" group-box code from `SettingsDialog._build_ui` (from `mcp_group = QGroupBox(...)` through the Add/Edit/Remove button row, `mcp_btn_layout`) verbatim.
- **Group 2:** `server_group = QGroupBox(translate("SettingsDialog", "Built-in MCP Server"))` with a `QVBoxLayout`. Move the `server_form` block (host, port, allowed hosts, token row) and `mcp_server_warning` into it verbatim.
- In the port validator, `QIntValidator(1, 65535, self)` keeps `self` as the parent: the page.

Move these methods verbatim (the names stay as they are): `_mcp_list_label` (static), `_parse_allowed_hosts` (static), `_parse_server_address` (static), `_generate_mcp_auth_token`, `_add_mcp_server`, `_edit_mcp_server`, `_remove_mcp_server`. Move the whole `_AddMCPServerDialog` class to the end of this module. Its imports are local, so check its body for any module-level name it needs and import that too.

Page contract:

```python
    def _show(self, cfg, label):
        self.mcp_list.clear()
        self._mcp_configs = [dict(e) for e in cfg.mcp_servers]
        for entry in self._mcp_configs:
            self.mcp_list.addItem(self._mcp_list_label(entry))
        self.mcp_server_host_edit.setText(cfg.mcp_server_host)
        self.mcp_server_port_edit.setText(str(cfg.mcp_server_port))
        self.mcp_server_allowed_hosts_edit.setText(
            ", ".join(cfg.mcp_server_allowed_hosts or []))
        self.mcp_server_auth_token_edit.setText(cfg.mcp_server_auth_token or "")

    def _values(self):
        # Parsed, so re-typing the same host is not a change.
        host, port = self._parse_server_address(
            self.mcp_server_host_edit.text(), self.mcp_server_port_edit.text())
        return {
            "mcp_servers": [dict(e) for e in self._mcp_configs],
            "mcp_server_host": host,
            "mcp_server_port": port,
            "mcp_server_allowed_hosts": self._parse_allowed_hosts(
                self.mcp_server_allowed_hosts_edit.text()),
            "mcp_server_auth_token":
                self.mcp_server_auth_token_edit.text().strip(),
        }
```

In `__init__`, set `self._mcp_configs = []` before building. The moved `_add_mcp_server` / `_remove_mcp_server` use `hasattr(self, "_mcp_configs")` guards; leave them, because they're harmless.

- [ ] **Step 4: Host it in the dialog.** In `settings_dialog.py`:
- Import `from .settings_pages.mcp_page import McpPage`.
- In `_build_ui`, replace the MCP group block with `self.mcp_page = McpPage(); layout.addWidget(self.mcp_page)`, at the same position for now.
- In `_load_from_config`, replace the MCP lines with `self.mcp_page.load(cfg)`.
- In `_save`, replace the `cfg.mcp_servers = …` through `cfg.mcp_server_auth_token = …` lines with `self.mcp_page.apply_to(cfg)`.
- Delete the moved methods and `_AddMCPServerDialog` from the dialog.
- Remove `import secrets` and `QIntValidator` if nothing else in the dialog uses them any more.

- [ ] **Step 5: Retarget the old tests**
- In `test_settings_dialog_mcp_address.py`: import `McpPage` from `freecad_ai.ui.settings_pages.mcp_page`, and use `parse = McpPage._parse_server_address` and `parse_hosts = McpPage._parse_allowed_hosts`. Update the module docstring.
- In `test_mcp_add_server_dialog.py`: change the import to `from freecad_ai.ui.settings_pages.mcp_page import _AddMCPServerDialog`.
- Grep for any remaining `SettingsDialog._mcp` or `settings_dialog._AddMCPServerDialog` in `tests/unit` and retarget it the same way.

- [ ] **Step 6: Run and commit**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_mcp_page.py tests/unit/test_settings_dialog_mcp_address.py tests/unit/test_mcp_add_server_dialog.py -q`, then the full suite. Expected: green.

```bash
git add -A freecad_ai/ui tests/unit
git commit -m "refactor(settings): MCP page, with the built-in server in its own group (#101)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `ToolsPage` (Tool Reranking, User Tools, Skills, Hooks, Editor)

**Files:**
- Create: `freecad_ai/ui/settings_pages/tools_page.py`
- Modify: `freecad_ai/ui/settings_dialog.py`
- Test: `tests/unit/test_tools_page.py` (new); retarget any test calling the moved methods

Use `grep -rn "_new_hook\|_edit_hook\|_confirm_close_for_editor\|_open_path\|_use_external_editor_now\|_refresh_skills_list\|_load_user_tools_list\|rerank_method_combo\|rerank_pinned_edit" tests/unit` to find those tests.

**Interfaces:**
- Consumes: `SettingsPage`.
- Produces:
  - `ToolsPage(SettingsPage)` with widgets `rerank_method_combo`, `rerank_top_n_spin`, `rerank_pinned_edit`, `user_tools_list`, `scan_macros_cb`, `skills_list`, `_skills_reset_btn`, `hooks_list`, `use_external_editor_cb`.
  - Attribute `host_can_close: Callable[[], bool]`, defaulting to always True.
  - It emits `closeHostRequested(bool)` from `_confirm_close_for_editor`.
  - It has **no** Test Reranker row: that moves to `ProviderPage` in Task 7. Until then the dialog keeps building the Test Reranker row itself, directly under the page.

- [ ] **Step 1: Write the failing test** `tests/unit/test_tools_page.py`:

```python
"""ToolsPage: reranking, user tools, skills, hooks, editor (#101)."""

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

from freecad_ai.config import AppConfig  # noqa: E402
import freecad_ai.ui.settings_pages.tools_page as tp_mod  # noqa: E402
from freecad_ai.ui.settings_pages.tools_page import ToolsPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def page(qapp, tmp_config_dir):
    p = ToolsPage()
    yield p
    p.deleteLater()


def test_group_boxes_in_order(page):
    titles = [g.title() for g in page.findChildren(QtWidgets.QGroupBox)]
    assert titles == ["Tool Reranking", "User Tools", "Skills", "Hooks",
                      "Editor"]


def test_untouched_load_writes_nothing(page):
    page.load(AppConfig())
    target = AppConfig()
    target.rerank_method = "keyword"   # e.g. just set by #10 on the provider page
    target.rerank_top_n = 8
    page.apply_to(target)
    assert (target.rerank_method, target.rerank_top_n) == ("keyword", 8)


def test_an_unshowable_method_survives_an_unrelated_edit(page):
    cfg = AppConfig()
    cfg.rerank_method = "semantic"     # hand-edited, no combo entry
    page.load(cfg)
    page.scan_macros_cb.setChecked(not cfg.scan_freecad_macros)
    page.apply_to(cfg)
    assert cfg.rerank_method == "semantic"


def test_pinned_tools_are_compared_parsed(page):
    cfg = AppConfig()
    cfg.rerank_pinned_tools = ["a", "b"]
    page.load(cfg)
    page.rerank_pinned_edit.setText("a ,b,")
    assert page.is_dirty() is False


def test_an_edit_writes_only_that_field(page):
    page.load(AppConfig())
    page.rerank_top_n_spin.setValue(25)
    target = AppConfig()
    target.use_external_editor = True
    page.apply_to(target)
    assert target.rerank_top_n == 25
    assert target.use_external_editor is True


class TestEditorPrompt:
    def _answer(self, monkeypatch, button):
        monkeypatch.setattr(tp_mod.QMessageBox, "question",
                            staticmethod(lambda *a, **k: button))

    def test_save_asks_the_host_to_save_and_close(self, page, monkeypatch):
        self._answer(monkeypatch, tp_mod.QMessageBox.Save)
        got = []
        page.closeHostRequested.connect(got.append)
        page.use_external_editor_cb.setChecked(False)
        assert page._prepare_editor_open() is True
        assert got == [True]

    def test_discard_asks_the_host_to_close(self, page, monkeypatch):
        self._answer(monkeypatch, tp_mod.QMessageBox.Discard)
        got = []
        page.closeHostRequested.connect(got.append)
        page.use_external_editor_cb.setChecked(False)
        assert page._prepare_editor_open() is True
        assert got == [False]

    def test_cancel_does_nothing(self, page, monkeypatch):
        self._answer(monkeypatch, tp_mod.QMessageBox.Cancel)
        got = []
        page.closeHostRequested.connect(got.append)
        page.use_external_editor_cb.setChecked(False)
        assert page._prepare_editor_open() is False
        assert got == []

    def test_a_host_that_cannot_close_opens_externally_unasked(
            self, page, monkeypatch):
        monkeypatch.setattr(tp_mod.QMessageBox, "question",
                            staticmethod(lambda *a, **k: pytest.fail("asked")))
        opened = []
        monkeypatch.setattr(page, "_open_in_external_editor", opened.append)
        page.host_can_close = lambda: False
        page.use_external_editor_cb.setChecked(False)
        assert page._prepare_editor_open() is True
        page._open_path("/tmp/x.py")
        assert opened == ["/tmp/x.py"]
```

(`tmp_config_dir` is the repo's existing fixture that points `USER_TOOLS_DIR` and similar into a temporary directory. Check `tests/conftest.py` for its exact effect on `USER_TOOLS_DIR` and the hooks and skills dirs. If the hooks registry reads the real user dir, the list is just populated from it, which is harmless for these assertions.)

- [ ] **Step 2: Run to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_tools_page.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `tools_page.py`.** Header: `import os`; `from ..compat import QtWidgets, QtCore, QtGui`; `from ...i18n import translate`; `from .base import SettingsPage`. Add the Qt aliases the moved code needs, including `QMessageBox` and `QFileDialog` as module attributes, so tests can monkeypatch `tp_mod.QMessageBox`.

Build order in `__init__`:
1. Move **Tool Reranking** from `SettingsDialog._build_ui` (from `rerank_group = …` through the pinned-tools row) verbatim, *without* the Test Reranker `test_layout` block.
2. Move **User Tools** verbatim.
3. Move **Skills** verbatim, and connect `self.skills_list.currentRowChanged.connect(lambda _: self._update_skills_reset_btn())` here, once. Today `_load_from_config` connects it again on every load, which is a latent duplicate connection.
4. Move **Hooks** verbatim.
5. Move **Editor** verbatim, then fix its tooltip text, which says "so the Settings dialog can stay open". Change it to "so this window can stay open" and "which requires closing this window first". Those are the same string context `SettingsDialog`, with text changes, so add them to the Global Constraints string list in the commit body.

Before building, set `self._user_tool_files = []`, `self._skills_status = []`, `self.host_can_close = lambda: True` and `self._cfg = None`.

Move these methods verbatim: `_load_user_tools_list`, `_add_user_tool`, `_edit_user_tool`, `_new_user_tool`, `_remove_user_tool`, `_reload_user_tools`, `_refresh_hooks_list`, `_add_hook`, `_new_hook`, `_open_in_freecad_editor`, `_open_in_external_editor`, `_prepare_editor_open`, `_open_path`, `_edit_hook`, `_remove_hook`, `_reload_hooks`, `_refresh_skills_list`, `_update_skills_reset_btn`, `_reset_skill_to_builtin`. `_load_user_tools_list` reads `self._cfg`, which `_show` sets.

Changed methods:

```python
    def _use_external_editor_now(self) -> bool:
        """Live checkbox state (governs this Edit/New, saved or not), or a
        host that cannot close to reveal the docked editor."""
        return self.use_external_editor_cb.isChecked() or not self.host_can_close()

    def _confirm_close_for_editor(self) -> bool:
        """Ask to close the host window so the docked editor is reachable.

        The host decides what closing means: the Settings dialog saves or
        rejects itself, Preferences accepts or rejects FreeCAD's dialog.
        """
        choice = QMessageBox.question(
            self,
            translate("SettingsDialog", "Open Script Editor"),
            translate(
                "SettingsDialog",
                "FreeCAD's script editor is docked behind this dialog. "
                "The dialog must close so you can reach it.\n\n"
                "Save your pending settings changes first?"),
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save)
        if choice == QMessageBox.Save:
            self.closeHostRequested.emit(True)
            return True
        if choice == QMessageBox.Discard:
            self.closeHostRequested.emit(False)
            return True
        return False
```

Page contract:

```python
_RERANK_METHODS = ["off", "keyword", "llm"]

    def _show(self, cfg, label):
        self._cfg = cfg
        m = cfg.rerank_method
        self.rerank_method_combo.setCurrentIndex(
            _RERANK_METHODS.index(m) if m in _RERANK_METHODS else 0)
        self.rerank_top_n_spin.setValue(cfg.rerank_top_n)
        self.rerank_pinned_edit.setText(", ".join(cfg.rerank_pinned_tools))
        self.use_external_editor_cb.setChecked(cfg.use_external_editor)
        self.scan_macros_cb.setChecked(cfg.scan_freecad_macros)
        self._load_user_tools_list()
        self._refresh_skills_list()
        self._refresh_hooks_list()

    def _values(self):
        return {
            "rerank_method": _RERANK_METHODS[self.rerank_method_combo.currentIndex()],
            "rerank_top_n": self.rerank_top_n_spin.value(),
            "rerank_pinned_tools": [s.strip() for s in
                                    self.rerank_pinned_edit.text().split(",")
                                    if s.strip()],
            "use_external_editor": self.use_external_editor_cb.isChecked(),
            "scan_freecad_macros": self.scan_macros_cb.isChecked(),
        }
```

- [ ] **Step 4: Host it in the dialog.**
- Import `ToolsPage`.
- In `_build_ui`, replace the Tool Reranking group (keeping the Test Reranker row as a standalone `QHBoxLayout` added to `layout` right after the page), plus the Editor, User Tools, Skills and Hooks groups, with `self.tools_page = ToolsPage(); layout.addWidget(self.tools_page)`.
- Connect `self.tools_page.closeHostRequested.connect(self._on_close_requested)`, and add:

```python
    def _on_close_requested(self, save):
        """A page's editor prompt: Save → the OK path, Discard → Cancel."""
        if save:
            self._save()
        else:
            self.reject()
```

- In `_load_from_config`, replace the reranker, editor, user-tools, skills and hooks lines (including the duplicate `currentRowChanged.connect`) with `self.tools_page.load(cfg)`.
- In `_save`, replace the external-editor, scan-macros and rerank lines with `self.tools_page.apply_to(cfg)`. Keep it **before** `section.apply_to(cfg)` (Task 3 order).
- Delete the moved methods from the dialog.
- Remove `QFileDialog`, `QListWidgetItem` and `os` if they're now unused there.

- [ ] **Step 5: Retarget the old tests.** Any test that called `SettingsDialog._confirm_close_for_editor` / `_prepare_editor_open` / hook or tool methods on a fake self now calls the `ToolsPage` method. Save/Discard assertions change from "`_save` called" / "`reject` called" to "`closeHostRequested` emitted True/False", and a new dialog test asserts `_on_close_requested(True)` calls `_save` and `(False)` calls `reject`. Tests monkeypatching `settings_dialog.QFileDialog` / `QMessageBox` for tool or hook actions switch to `tools_page.QFileDialog` / `QMessageBox`.

- [ ] **Step 6: Run and commit.** Full suite green.

```bash
git add -A freecad_ai/ui tests/unit
git commit -m "refactor(settings): Tools page; the editor prompt asks its host to close (#101)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `BehaviorPage` (Limits, Behavior, System Prompt)

**Files:**
- Create: `freecad_ai/ui/settings_pages/behavior_page.py`
- Modify: `freecad_ai/ui/settings_dialog.py`
- Test: `tests/unit/test_behavior_page.py` (new); retarget `tests/unit/test_prompt_cache_settings.py`, `tests/unit/test_preserve_reasoning_setting.py`, and any test using `strip_thinking_check` / `system_prompt_edit` / `thinking_combo` / `max_tokens_spin` / `viewport_*_combo` on the dialog

**Interfaces:**
- Consumes: `SettingsPage`.
- Produces:
  - `BehaviorPage(SettingsPage)` with the widgets:
    - Limits: `max_tokens_spin`, `context_window_spin`, `max_tool_turns_spin`, `execution_timeout_spin`.
    - Behavior: `enable_tools_check`, `auto_execute_check`, `keep_dock_check`, `thinking_combo`, `strip_thinking_check`, `preserve_reasoning_check`, `prompt_cache_check`, `log_usage_check`, `viewport_capture_combo`, `viewport_resolution_combo`.
    - System Prompt: `system_prompt_edit`, `prompt_reset_btn`.
  - `after_save(cfg)` sets the keep-dock menu checkmark.

- [ ] **Step 1: Write the failing test** `tests/unit/test_behavior_page.py`:

```python
"""BehaviorPage: Limits, Behavior, System Prompt (#101)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    try:
        from PySide2 import QtCore, QtWidgets
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.config import AppConfig  # noqa: E402
from freecad_ai.ui.settings_pages.behavior_page import BehaviorPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def page(qapp, tmp_config_dir):
    p = BehaviorPage()
    yield p
    p.deleteLater()


def _group_of(widget):
    w = widget.parent()
    while w is not None and not isinstance(w, QtWidgets.QGroupBox):
        w = w.parent()
    return w.title() if w is not None else None


def test_group_boxes_in_order(page):
    titles = [g.title() for g in page.findChildren(QtWidgets.QGroupBox)]
    assert titles == ["Limits", "Behavior", "System Prompt"]


def test_the_limits_live_in_limits(page):
    for w in (page.max_tokens_spin, page.context_window_spin,
              page.max_tool_turns_spin, page.execution_timeout_spin):
        assert _group_of(w) == "Limits"


def test_the_viewport_combos_live_in_behavior(page):
    assert _group_of(page.viewport_capture_combo) == "Behavior"
    assert _group_of(page.viewport_resolution_combo) == "Behavior"


def test_the_tool_calling_label(page):
    assert page.enable_tools_check.text() == \
        "Use tool calling (uncheck to fall back to code generation)"


def test_no_mode_control_and_mode_survives(page):
    assert not hasattr(page, "mode_combo")
    cfg = AppConfig()
    cfg.mode = "plan"
    page.load(cfg)
    page.max_tokens_spin.setValue(8192)
    page.apply_to(cfg)
    assert cfg.mode == "plan"


def test_limits_load_and_apply(page):
    cfg = AppConfig()
    cfg.max_tokens, cfg.context_window = 65536, 200000
    cfg.max_tool_turns, cfg.execution_timeout = 0, 120
    page.load(cfg)
    assert page.max_tokens_spin.value() == 65536
    assert page.max_tool_turns_spin.value() == 0
    page.execution_timeout_spin.setValue(90)
    target = AppConfig()
    page.apply_to(target)
    assert target.execution_timeout == 90
    assert target.max_tokens == AppConfig().max_tokens   # untouched


def test_untouched_load_writes_nothing(page):
    page.load(AppConfig())
    target = AppConfig()
    target.thinking = "extended"
    page.apply_to(target)
    assert target.thinking == "extended"


def test_an_unshowable_thinking_survives_an_unrelated_edit(page):
    cfg = AppConfig()
    cfg.thinking = "max"
    cfg.viewport_capture = "sometimes"
    page.load(cfg)
    page.auto_execute_check.setChecked(not cfg.auto_execute)
    page.apply_to(cfg)
    assert (cfg.thinking, cfg.viewport_capture) == ("max", "sometimes")


def test_strip_thinking_auto_round_trips(page):
    cfg = AppConfig()
    cfg.strip_thinking_history = None
    page.load(cfg)
    assert page.strip_thinking_check.checkState() == QtCore.Qt.PartiallyChecked
    assert page.is_dirty() is False


class TestSystemPrompt:
    def test_unedited_default_writes_nothing(self, page):
        page.load(AppConfig())
        target = AppConfig()
        target.system_prompt_override = "set elsewhere"
        page.apply_to(target)
        assert target.system_prompt_override == "set elsewhere"

    def test_an_edit_is_saved(self, page):
        page.load(AppConfig())
        page.system_prompt_edit.setPlainText("Be terse.")
        target = AppConfig()
        page.apply_to(target)
        assert target.system_prompt_override == "Be terse."

    def test_editing_back_to_the_default_clears_the_override(self, page):
        cfg = AppConfig()
        cfg.system_prompt_override = "Be terse."
        page.load(cfg)
        page._reset_system_prompt()
        page.apply_to(cfg)
        assert cfg.system_prompt_override == ""


def test_after_save_sets_the_keep_dock_checkmark(page, monkeypatch):
    calls = []
    monkeypatch.setattr("freecad_ai.ui.command_state.set_command_checked",
                        lambda name, value: calls.append((name, value)))
    cfg = AppConfig()
    cfg.keep_dock_on_workbench_switch = True
    page.after_save(cfg)
    assert calls == [("FreeCADAI_ToggleKeepDock", True)]
```

- [ ] **Step 2: Run to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_behavior_page.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `behavior_page.py`.** Header: `from ..compat import QtWidgets, QtCore`, `from ...i18n import translate`, `from .base import SettingsPage`, and the needed aliases.

Build, in order:
1. `limits_group = QGroupBox(translate("SettingsDialog", "Limits"))` with a `QFormLayout`, into which move the four spinbox blocks verbatim from the Model Parameters "fixed fields" part of `SettingsDialog._build_ui` (`max_tokens_spin` through `execution_timeout_spin`, each with its `addRow`).
2. `behavior_group`: move the Behavior group code verbatim, *except*:
   - the label of `enable_tools_check` becomes `translate("SettingsDialog", "Use tool calling (uncheck to fall back to code generation)")`;
   - the viewport capture and resolution rows (currently built after the System Prompt group but already added to `behavior_layout`) are added at the end of `behavior_layout`, before `behavior_group.setLayout`.
3. `prompt_group`: move "System Prompt" verbatim (the Reset button row and `system_prompt_edit`).

Move these methods verbatim: `_update_strip_thinking_ui`, `_on_strip_thinking_changed`, `_read_strip_thinking_state`, `_get_default_prompt_text`, `_reset_system_prompt`. Set `self._last_default_prompt = ""` in `__init__`.

Page contract:

```python
_THINKING = ["off", "on", "extended"]
_CAPTURE = ["off", "every_message", "after_changes"]
_RESOLUTION = ["low", "medium", "high"]


def _index(values, value, default=0):
    return values.index(value) if value in values else default

    def _show(self, cfg, label):
        self.max_tokens_spin.setValue(cfg.max_tokens)
        self.context_window_spin.setValue(cfg.context_window)
        self.max_tool_turns_spin.setValue(cfg.max_tool_turns)
        self.execution_timeout_spin.setValue(cfg.execution_timeout)
        self.enable_tools_check.setChecked(cfg.enable_tools)
        self.auto_execute_check.setChecked(cfg.auto_execute)
        self.keep_dock_check.setChecked(cfg.keep_dock_on_workbench_switch)
        self.thinking_combo.setCurrentIndex(_index(_THINKING, cfg.thinking))
        self._update_strip_thinking_ui(cfg.strip_thinking_history)
        self.preserve_reasoning_check.setChecked(cfg.preserve_reasoning_history)
        self.prompt_cache_check.setChecked(cfg.optimize_prompt_caching)
        self.log_usage_check.setChecked(cfg.log_token_usage)
        self.viewport_capture_combo.setCurrentIndex(
            _index(_CAPTURE, cfg.viewport_capture))
        self.viewport_resolution_combo.setCurrentIndex(
            _index(_RESOLUTION, cfg.viewport_resolution, 1))
        default_prompt = self._get_default_prompt_text()
        self._last_default_prompt = default_prompt
        self.system_prompt_edit.setPlainText(
            cfg.system_prompt_override or default_prompt)

    def _values(self):
        # The override is computed: text equal to the generated default
        # saves as "" (no override). An unedited prompt compares equal to
        # its baseline and writes nothing at all.
        text = self.system_prompt_edit.toPlainText().strip()
        default = self._get_default_prompt_text().strip()
        return {
            "max_tokens": self.max_tokens_spin.value(),
            "context_window": self.context_window_spin.value(),
            "max_tool_turns": self.max_tool_turns_spin.value(),
            "execution_timeout": self.execution_timeout_spin.value(),
            "enable_tools": self.enable_tools_check.isChecked(),
            "auto_execute": self.auto_execute_check.isChecked(),
            "keep_dock_on_workbench_switch": self.keep_dock_check.isChecked(),
            "thinking": _THINKING[self.thinking_combo.currentIndex()],
            "strip_thinking_history": self._read_strip_thinking_state(),
            "preserve_reasoning_history":
                self.preserve_reasoning_check.isChecked(),
            "optimize_prompt_caching": self.prompt_cache_check.isChecked(),
            "log_token_usage": self.log_usage_check.isChecked(),
            "viewport_capture":
                _CAPTURE[self.viewport_capture_combo.currentIndex()],
            "viewport_resolution":
                _RESOLUTION[self.viewport_resolution_combo.currentIndex()],
            "system_prompt_override": "" if text == default else text,
        }

    def after_save(self, cfg):
        # The menu's "Keep Chat Panel Open" tick mirrors this flag, and
        # FreeCAD never re-asks the command for its state, so a save from
        # either window would otherwise leave the checkmark stale.
        from ..command_state import set_command_checked
        set_command_checked("FreeCADAI_ToggleKeepDock",
                            cfg.keep_dock_on_workbench_switch)
```

The `thinking: "max"` test works because the baseline records `"off"` (index 0) and the widget still says `"off"`, so no diff.

- [ ] **Step 4: Host it in the dialog.**
- Import `BehaviorPage`, and put `self.behavior_page = BehaviorPage(); layout.addWidget(self.behavior_page)` right after the Model Parameters group.
- Remove from `_build_ui`: the four Limits spinboxes and their `fixed_layout` (Model Parameters keeps only the label, table and buttons), the Behavior group, the System Prompt group, and the viewport rows.
- In `_load_from_config`, replace those fields' lines with `self.behavior_page.load(cfg)`.
- In `_save`, replace the `cfg.max_tokens` … `cfg.viewport_resolution` writes (keeping the temperature sync) with `self.behavior_page.apply_to(cfg)`.
- Replace the `set_command_checked` block after save with `self.behavior_page.after_save(cfg)`.
- Delete the moved methods from the dialog.
- `_test_connection` still reads `self.max_tokens_spin` and `self.thinking_combo`. Change them now to `max_tokens=self._cfg.max_tokens` and `thinking=self._cfg.thinking` (Ruling 1, from `get_config()`). Delete `_THINKING_VALUES` from the dialog if it's unused after this.

- [ ] **Step 5: Retarget the old tests**
- In `test_prompt_cache_settings.py` and `test_preserve_reasoning_setting.py`, point widget and load/save assertions at `BehaviorPage` (load a config, check the widget; toggle, `apply_to`, check the config), replacing the dialog `_save` fakes.
- In `test_test_connection_config_scope.py`:
  - rewrite `test_max_tokens_comes_from_the_spinbox` → `test_max_tokens_comes_from_the_saved_config`, and `test_thinking_comes_from_the_combo` → `test_thinking_comes_from_the_saved_config`, asserting the thread received `cfg.max_tokens` / `cfg.thinking`;
  - keep every "is not written" test unchanged.
- Any fake-self for `_save` that sets `max_tokens_spin`, `thinking_combo` and so on now needs a `behavior_page` MagicMock instead.

- [ ] **Step 6: Run and commit.** Full suite green.

```bash
git add -A freecad_ai/ui tests/unit
git commit -m "refactor(settings): Behavior page with a Limits group; tool-calling relabelled (#101)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `ProviderPage` (section, params table, Test Connection, Test Reranker)

**Files:**
- Create: `freecad_ai/ui/settings_pages/provider_page.py`
- Modify: `freecad_ai/ui/settings_dialog.py`
- Test: `tests/unit/test_provider_page.py` (new); retarget `test_test_connection_config_scope.py`, `test_probe_status_names_profile.py`, `test_per_profile_capabilities.py`, `test_reranker_namespace.py`, `test_utility_call_sites.py`, `test_settings_dialog_section_wiring.py`, `test_settings_dialog_provider_change.py`, `test_profile_selector.py` (the parts that call dialog methods), and `test_settings_dialog_base_url_guard.py` (only `_test_*` uses; the veto stays on the dialog)

**Interfaces:**
- Consumes: `SettingsPage`; `ProviderSection` with Tasks 2 and 3 applied.
- Produces:
  - `ProviderPage(SettingsPage)` with attributes `section: ProviderSection`, `model_params_table`, `test_btn`, `test_status`, `_rerank_test_btn`, `_rerank_test_status`.
  - Signal `busyChanged = Signal(bool)`.
  - Overrides `load(cfg, label=None)`, `is_dirty()`, `apply_to(cfg)` and `view_label()`.
  - `_TestConnectionThread` / `_TestRerankerThread` importable from `freecad_ai.ui.settings_pages.provider_page`.
  - Static methods `_probe_running_text` / `_probe_result_text`.

- [ ] **Step 1: Write the failing test** `tests/unit/test_provider_page.py`:

```python
"""ProviderPage: the provider section, its parameter table and probes (#101)."""

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

from freecad_ai.config import AppConfig, ProviderConfig  # noqa: E402
import freecad_ai.ui.settings_pages.provider_page as pp_mod  # noqa: E402
from freecad_ai.ui.settings_pages.provider_page import ProviderPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def _cfg():
    c = AppConfig()
    c.profiles = {
        "cloud": ProviderConfig(name="anthropic", model="m1",
                                base_url="https://api.anthropic.com",
                                params={"temperature": 0.2}),
        "local": ProviderConfig(name="ollama", model="m2",
                                base_url="http://localhost:11434/v1"),
    }
    c.active_profile = "cloud"
    return c


@pytest.fixture
def page(qapp):
    p = ProviderPage()
    yield p
    p.deleteLater()


def test_the_test_reranker_row_sits_in_the_utility_group(page):
    w = page._rerank_test_btn.parent()
    while w is not None and w is not page.section.utility_group:
        w = w.parent()
    assert w is page.section.utility_group


def test_untouched_load_writes_nothing(page):
    page.load(_cfg())
    target = _cfg()
    target.temperature = 0.9
    target.profiles["cloud"].model = "newer"
    page.apply_to(target)
    assert target.temperature == 0.9
    assert target.profiles["cloud"].model == "newer"
    assert page.is_dirty() is False


def test_a_param_edit_reaches_the_profile_and_the_temperature(page):
    page.load(_cfg())
    page.model_params_table.item(0, 1).setText("0.7")
    assert page.is_dirty() is True
    target = _cfg()
    page.apply_to(target)
    assert target.profiles["cloud"].params == {"temperature": 0.7}
    assert target.temperature == 0.7


def test_view_label_follows_the_shown_profile(page):
    page.load(_cfg(), label="local")
    assert page.view_label() == "local"


def test_a_failed_load_writes_nothing(page, monkeypatch):
    monkeypatch.setattr(page.section, "load",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(RuntimeError):
        page.load(_cfg())
    target = _cfg()
    page.apply_to(target)
    assert target.to_dict() == _cfg().to_dict()


class TestConnectionProbe:
    def _capture(self, monkeypatch):
        made = {}

        class _T:
            def __init__(self, *a, **k):
                made["args"], made["kwargs"] = a, k
                self.finished = self.vision_result = \
                    self.capabilities_result = self

            def connect(self, *_):
                pass

            def start(self):
                made["started"] = True

        monkeypatch.setattr(pp_mod, "_TestConnectionThread", _T)
        return made

    def test_max_tokens_and_thinking_come_from_the_saved_config(
            self, page, monkeypatch, tmp_config_dir):
        import freecad_ai.config as config_mod
        live = config_mod.get_config()
        live.max_tokens, live.thinking = 12345, "extended"
        made = self._capture(monkeypatch)
        page.load(_cfg())
        page._test_connection()
        assert made["kwargs"]["max_tokens"] == 12345
        assert made["kwargs"]["thinking"] == "extended"

    def test_the_thread_outlives_the_page(self, page, monkeypatch):
        made = self._capture(monkeypatch)
        page.load(_cfg())
        page._test_connection()
        assert made["kwargs"]["parent"] is QtWidgets.QApplication.instance()

    def test_busy_is_signalled(self, page, monkeypatch):
        self._capture(monkeypatch)
        got = []
        page.busyChanged.connect(got.append)
        page.load(_cfg())
        page._test_connection()
        page._on_test_finished(False, "nope")
        assert got == [True, False]
```

(If `AppConfig` has no `to_dict()`, use `dataclasses.asdict`. Check `config.py`: `AppConfig` is a dataclass, so `dataclasses.asdict(target) == dataclasses.asdict(_cfg())` works.)

- [ ] **Step 2: Run to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_provider_page.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `provider_page.py`.** Header imports: `from ..compat import QtWidgets, QtCore`, `from ...i18n import translate`, `from ...config import get_config, PROVIDER_PRESETS`, `from ..provider_section import ProviderSection`, `from .base import SettingsPage`, plus the aliases.

Move `_TestConnectionThread` and `_TestRerankerThread` verbatim from `settings_dialog.py`. Their relative imports change depth: `from ..llm.client` becomes `from ...llm.client`, and `from ..tools.reranker` becomes `from ...tools.reranker`. Update `_TestConnectionThread`'s docstring paragraph on max_tokens/thinking: "max_tokens and thinking are the saved values: those widgets live on the Behavior page, and a probe reading another page's widgets would link pages (#101)."

Build:
1. `self.section = ProviderSection()`. Connect `profileShown`, `aboutToCommit`, `presetApplied` and `modelChanged` to the moved handlers `_on_profile_shown`, `_on_about_to_commit`, `_on_preset_applied` (table half only, after Task 3) and `_on_model_changed`. Add the section to the layout.
2. **Test Reranker row:** build the `test_layout` block moved from the dialog, and append it to the section's utility group. Check `ProviderSection._build_ui` for the form layout inside `utility_group` (`util_form`, a `QFormLayout`), then add the row with `self.section.utility_group.layout().addRow("", rerank_row_widget)`, where `rerank_row_widget` is a `QWidget` holding the button and status label in an `QHBoxLayout` with zero margins. Connect the button to `self._test_reranker`.
3. **Model Parameters group:** after Task 6, this is only the label, `model_params_table` and the Add/Remove/Load Defaults row, moved verbatim.
4. **Test Connection row:** move `test_btn` and `test_status` verbatim.

`self._last_model_name = ""` and `self._cfg = None` in `__init__`.

Move these methods verbatim (they reference `self.provider_section`, so replace that with `self.section` throughout): `_on_profile_shown`, `_on_about_to_commit`, `_on_preset_applied`, `_on_model_changed`, `_load_model_params_table`, `_populate_model_params_table`, `_read_model_params_table`, `_add_model_param`, `_remove_model_param`, `_load_default_model_params`, `_probe_running_text`, `_probe_result_text`, `_test_reranker`, `_on_rerank_test_finished`, `_test_connection`, `_on_test_finished`, `_on_vision_probed`, `_on_capabilities_detected`.

Changes inside the moved probe methods:
- `_test_connection`:
  - `max_tokens=get_config().max_tokens`, `thinking=get_config().thinking`, `temperature=get_config().temperature`, `parent=QtWidgets.QApplication.instance()`;
  - `api_key` falls back via `get_config().provider_keys`;
  - replace `self.save_btn.setEnabled(False)` / `self.cancel_btn.setEnabled(False)` with `self.busyChanged.emit(True)`.
- `_on_test_finished` failure branch and `_on_vision_probed`: replace the save/cancel re-enables with `self.busyChanged.emit(False)`.
- `_test_reranker`: `parent=QtWidgets.QApplication.instance()`, and `get_config()` in place of `self._cfg` for `provider_keys` and `resolve_params`.

Page contract:

```python
    def load(self, cfg, label=None):
        self._cfg = cfg
        super().load(cfg, label)

    def _show(self, cfg, label):
        # Profile edits stay in the section's own copy until save (see
        # ProviderSection.load for why the live singleton must not see them).
        self.section.load(cfg, label=label)

    # The section keeps its own baseline (profiles, active, utilities); the
    # params table is part of the profile and commits through aboutToCommit.
    def is_dirty(self):
        return self._baseline is not None and self.section.is_dirty()

    def apply_to(self, cfg):
        if not self.is_dirty():
            return
        self.section.apply_to(cfg)
        # cfg.temperature is still the job-level fallback create_client
        # passes when a profile states no temperature; keep it in step.
        if self.section.model_edit.text().strip():
            cfg.temperature = self._read_model_params_table().get(
                "temperature", cfg.temperature)

    def view_label(self):
        return self.section.current_label()
```

`self._baseline` is `{}` after a successful load, because `_values()` returns `{}`, and `None` after a failed one. That is the flag `is_dirty` checks.

The #10 record is applied inside `section.apply_to`, which runs only when the section is dirty. A provider switch always dirties the profile's name, so the record is never skipped.

- [ ] **Step 4: Host it in the dialog.**
- Import `ProviderPage`.
- In `_build_ui`, replace `self.provider_section = ProviderSection()` and its four `connect` lines, the Model Parameters group, the standalone Test Reranker row (from Task 5) and the Test Connection row with:

```python
        self.provider_page = ProviderPage()
        # Kept as a name: tests and the #78 geometry check reach the
        # profile row through it.
        self.provider_section = self.provider_page.section
        self.provider_page.busyChanged.connect(
            lambda busy: (self.save_btn.setEnabled(not busy),
                          self.cancel_btn.setEnabled(not busy)))
        layout.addWidget(self.provider_page)
```

- In `_load_from_config`, replace `self.provider_section.load(cfg)` with `self.provider_page.load(cfg)`.
- In `_save`, replace `section.apply_to(cfg)` and the temperature sync with `self.provider_page.apply_to(cfg)`. It stays last among the `apply_to` calls, because of Task 3.
- Delete everything that moved.
- Remove `_THINKING_VALUES`, `QThread`, `QTableWidget*`, `QHeaderView` and `PROVIDER_PRESETS` if they're now unused there.

- [ ] **Step 5: Retarget the old tests.** This is the biggest retarget. The mechanical rule: `SettingsDialog._test_connection(fake)` → `ProviderPage._test_connection(fake)`, and the same for `_test_reranker`, `_on_test_finished`, `_on_vision_probed`, `_on_capabilities_detected`, `_on_preset_applied`, `_on_model_changed`, `_load_model_params_table`, `_read_model_params_table` and `_probe_*_text`. In each fake:
- the `provider_section` attribute becomes `section`;
- `save_btn`/`cancel_btn` expectations become a `busyChanged` MagicMock with `emit` assertions;
- `self._cfg.provider_keys` / `temperature` come from a monkeypatched `provider_page.get_config`.

Monkeypatch targets move from `settings_dialog._TestConnectionThread` / `_TestRerankerThread` to `provider_page._TestConnectionThread` / `_TestRerankerThread`.

Tests that build a real `SettingsDialog()` and poke `dialog.provider_section` keep working through the alias. Tests that poked `dialog.model_params_table` / `dialog.test_btn` change to `dialog.provider_page.model_params_table` and so on.

- [ ] **Step 6: Run and commit.** Full suite green.

```bash
git add -A freecad_ai/ui tests/unit
git commit -m "refactor(settings): Provider page; Test Reranker beside the Reranker dropdown (#101)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: The dialog becomes a frame

**Files:**
- Modify: `freecad_ai/ui/settings_dialog.py`, the whole module
- Modify: `freecad_ai/ui/settings_pages/__init__.py` (re-exports)
- Test: `tests/unit/test_settings_dialog_frame.py` (new); `tests/unit/test_config_changed_notification.py` (`_save` fakes); `tests/unit/test_settings_dialog_geometry.py` and `test_settings_dialog_screenshots.py` (must pass unchanged)

**Interfaces:**
- Consumes: the four pages.
- Produces: `SettingsDialog` with `provider_page`, `behavior_page`, `tools_page`, `mcp_page`, `provider_section` (alias), `save_btn`, `cancel_btn`, `_pages()`, `_save()`, `_on_close_requested(save)`, `_profiles_missing_base_url` (static) and `_confirm_incomplete_profiles`.

- [ ] **Step 1: Write the failing test** `tests/unit/test_settings_dialog_frame.py`:

```python
"""SettingsDialog is a frame: four pages, one save, one notify (#101)."""

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

import freecad_ai.config as config_mod  # noqa: E402
import freecad_ai.ui.settings_dialog as sd  # noqa: E402
from freecad_ai.ui.settings_pages import (  # noqa: E402
    BehaviorPage, McpPage, ProviderPage, ToolsPage)


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def calls(monkeypatch):
    log = []
    monkeypatch.setattr(sd, "save_current_config", lambda: log.append("save"))
    monkeypatch.setattr(sd, "notify_config_changed",
                        lambda: log.append("notify"))
    monkeypatch.setattr("freecad_ai.ui.command_state.set_command_checked",
                        lambda *a: log.append("checkmark"))
    return log


@pytest.fixture
def dialog(qapp, tmp_config_dir, calls):
    d = sd.SettingsDialog()
    yield d
    d.deleteLater()


def test_it_holds_the_four_pages_in_order(dialog):
    assert [type(p) for p in dialog._pages()] == \
        [ProviderPage, BehaviorPage, ToolsPage, McpPage]


def test_ok_saves_and_notifies_once_then_runs_after_save(dialog, calls):
    dialog._save()
    assert calls == ["save", "notify", "checkmark"]


def test_untouched_ok_changes_no_field(dialog):
    before = config_mod.get_config().__dict__.copy()
    dialog._save()
    assert config_mod.get_config().__dict__ == before


def test_edits_on_two_pages_both_land(dialog):
    dialog.behavior_page.max_tokens_spin.setValue(8192)
    dialog.mcp_page.mcp_server_port_edit.setText("31000")
    dialog._save()
    cfg = config_mod.get_config()
    assert (cfg.max_tokens, cfg.mcp_server_port) == (8192, 31000)


def test_a_declined_veto_saves_nothing(dialog, calls, monkeypatch):
    monkeypatch.setattr(dialog, "_confirm_incomplete_profiles",
                        lambda profiles: False)
    dialog.behavior_page.max_tokens_spin.setValue(8192)
    dialog._save()
    assert calls == []
    assert config_mod.get_config().max_tokens != 8192


def test_close_requested(dialog, monkeypatch):
    seen = []
    monkeypatch.setattr(dialog, "_save", lambda: seen.append("save"))
    monkeypatch.setattr(dialog, "reject", lambda: seen.append("reject"))
    dialog.tools_page.closeHostRequested.emit(True)
    dialog.tools_page.closeHostRequested.emit(False)
    assert seen == ["save", "reject"]


def test_the_dialog_module_is_a_frame():
    """No settings logic left behind: the file stays small."""
    with open(sd.__file__, encoding="utf-8") as f:
        assert sum(1 for _ in f) < 250
```

The `close_requested` test only works if the dialog connects the signal to a lambda or bound call that looks up `self._save` at call time. Connect with `lambda save: self._on_close_requested(save)`, and have `_on_close_requested` call `self._save()` / `self.reject()`.

- [ ] **Step 2: Run to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_settings_dialog_frame.py -q`
Expected: FAIL with `ImportError` on `from freecad_ai.ui.settings_pages import …` (no re-exports yet), and on `_pages`.

- [ ] **Step 3: Implement.** `settings_pages/__init__.py`, after the docstring:

```python
from .provider_page import ProviderPage
from .behavior_page import BehaviorPage
from .tools_page import ToolsPage
from .mcp_page import McpPage

__all__ = ["ProviderPage", "BehaviorPage", "ToolsPage", "McpPage"]
```

Rewrite `settings_dialog.py` to the frame. Module docstring:

```python
"""The workbench's Settings dialog (gear button, FreeCADAI_OpenSettings).

A frame around the four settings pages that Edit → Preferences → FreeCAD
AI also shows (#101): it stacks them in one scroll area and adds OK/Cancel
plus the one thing only a dialog can do, vetoing OK on a profile that
cannot work. The pages own every field; OK writes only what changed.
"""
```

Keep `__init__`: the #78 width block verbatim, and `self._load_from_config()`. `_build_ui` becomes:
- the scroll area as today;
- the four pages added in order Provider, Behavior, Tools, MCP;
- the `busyChanged` wiring and the `provider_section` alias (Task 7);
- `tools_page.closeHostRequested` connected to `_on_close_requested` (via the lambda);
- the Save/Cancel button row verbatim.

Then:

```python
    def _pages(self):
        return (self.provider_page, self.behavior_page, self.tools_page,
                self.mcp_page)

    def _load_from_config(self):
        cfg = get_config()
        for page in self._pages():
            page.load(cfg)

    def _save(self):
        """OK: veto check, every page's changes, one save, one notify."""
        # Commit the visible profile widgets first, so the veto sees the
        # Base URL as typed.
        section = self.provider_page.section
        section.commit()
        if not self._confirm_incomplete_profiles(section.profiles()):
            return
        cfg = get_config()
        # Provider last: its #10 reranker default applies only while the
        # live config is still at factory defaults, so an explicit choice
        # the Tools page just wrote wins.
        for page in (self.behavior_page, self.tools_page, self.mcp_page,
                     self.provider_page):
            page.apply_to(cfg)
        save_current_config()
        # The chat panel (and anything else showing config-derived state)
        # refreshes from this, whichever window saved (#99).
        notify_config_changed()
        for page in self._pages():
            page.after_save(cfg)
        self.accept()
```

Keep `_on_close_requested`, `_profiles_missing_base_url` and `_confirm_incomplete_profiles` verbatim. Remove every import the frame doesn't use. The frame needs only `QtWidgets`, `translate`, `get_config`, `notify_config_changed`, `save_current_config`, `ProviderSection` (for `_profiles_with_url_placeholder` in the veto) and the four pages.

The Task 3 ordering note in the plan said Tools before Provider. The loop above encodes it, and **the Preferences side has no ordering guarantee**. The `test_provider_page_before_and_after_tools_page_agree` test in Task 9 covers both orders.

- [ ] **Step 4: Fix the remaining fakes.** In `test_config_changed_notification.py`, `test_ok_notifies_after_saving` and `test_a_declined_save_does_not_notify` build a fake for `SettingsDialog._save`. Give the fake `provider_page`, `behavior_page`, `tools_page` and `mcp_page` as MagicMocks and `_pages` returning them, keep the monkeypatches on `settings_dialog.get_config` / `save_current_config` / `notify_config_changed`, and keep both assertions. Then grep `tests/unit` for any remaining `SettingsDialog\.` references to moved methods and retarget them per Task 7's rule.

- [ ] **Step 5: Run and commit**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_settings_dialog_frame.py tests/unit/test_settings_dialog_geometry.py tests/unit/test_settings_dialog_screenshots.py tests/unit/test_config_changed_notification.py -q`, then the full suite. Expected: green.

```bash
git add -A freecad_ai/ui tests/unit
git commit -m "refactor(settings): the Settings dialog is a frame around the four pages (#101)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Four Preferences pages

**Files:**
- Rewrite: `freecad_ai/ui/prefs_page.py`
- Modify: `InitGui.py:355-363`
- Test: rewrite `tests/unit/test_prefs_page.py`; new `tests/unit/test_initgui_prefs_registration.py` if `InitGui` registration isn't already tested (grep `addPreferencePage` in `tests/`)

**Interfaces:**
- Consumes: the four pages (lazy import).
- Produces: `_PrefsPageBase` and the classes `FreeCADAIProviderPrefs`, `FreeCADAIBehaviorPrefs`, `FreeCADAIToolsPrefs` and `FreeCADAIMcpPrefs`, each with `form`, `page`, `loadSettings()` and `saveSettings()`. `_warn(parent, title, text)` stays module-level for monkeypatching.

- [ ] **Step 1: Write the failing test.** Replace `tests/unit/test_prefs_page.py`, keeping its header, the `qapp`/`cfg`/`notified`/`warnings` fixtures, `_disk()` and `test_importing_the_module_builds_no_qt_widgets` as they are. Add `import freecad_ai.ui.prefs_page as pp` below the imports. Then:

```python
ALL = ["FreeCADAIProviderPrefs", "FreeCADAIBehaviorPrefs",
       "FreeCADAIToolsPrefs", "FreeCADAIMcpPrefs"]


@pytest.fixture
def pages(qapp, cfg, notified, warnings):
    made = {name: getattr(pp, name)() for name in ALL}
    for p in made.values():
        p.loadSettings()
    yield made
    for p in made.values():
        p.form.deleteLater()


def _ok(pages):
    """FreeCAD's OK: saveSettings() on every page, in tree order."""
    for name in ALL:
        pages[name].saveSettings()


def test_four_distinct_class_names():
    assert len({getattr(pp, n).__name__ for n in ALL}) == 4


def test_titles(pages):
    assert [pages[n].form.windowTitle() for n in ALL] == \
        ["Provider", "Behavior", "Tools", "MCP"]


def test_untouched_ok_writes_nothing(pages, notified):
    before = _disk()
    _ok(pages)
    assert _disk() == before
    assert notified == []


def test_a_never_loaded_page_writes_nothing(qapp, cfg, notified):
    p = pp.FreeCADAIBehaviorPrefs()
    before = _disk()
    p.saveSettings()
    assert _disk() == before
    p.form.deleteLater()


def test_a_page_whose_load_raised_writes_nothing(qapp, cfg, notified,
                                                 monkeypatch):
    p = pp.FreeCADAIMcpPrefs()
    monkeypatch.setattr(p.page, "_show",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    p.loadSettings()             # logs, does not raise into FreeCAD
    p.page.mcp_server_port_edit.setText("31000")
    before = _disk()
    p.saveSettings()
    assert _disk() == before
    p.form.deleteLater()


def test_edits_on_two_pages_in_one_ok_both_land(pages, notified):
    pages["FreeCADAIProviderPrefs"].page.section.model_edit.setText("m-new")
    pages["FreeCADAIMcpPrefs"].page.mcp_server_port_edit.setText("31000")
    _ok(pages)
    fresh = config_mod.load_config()     # from disk, not the singleton
    assert fresh.profiles["cloud"].model == "m-new"
    assert fresh.mcp_server_port == 31000
    assert notified == [1, 1]


def test_a_second_ok_writes_nothing(pages, notified):
    pages["FreeCADAIBehaviorPrefs"].page.max_tokens_spin.setValue(8192)
    _ok(pages)
    before = _disk()
    _ok(pages)
    assert _disk() == before
    assert notified == [1]


def test_a_large_max_tokens_survives_an_unrelated_save(pages, cfg):
    pages["FreeCADAIToolsPrefs"].page.rerank_top_n_spin.setValue(25)
    _ok(pages)
    assert config_mod.get_config().max_tokens == 65536


def test_mode_is_never_touched(pages):
    for name in ALL:
        assert not hasattr(pages[name].page, "mode_combo")
    pages["FreeCADAIBehaviorPrefs"].page.max_tokens_spin.setValue(8192)
    _ok(pages)
    assert config_mod.get_config().mode == "act"


def test_apply_stays_on_the_profile_being_edited(pages):
    prov = pages["FreeCADAIProviderPrefs"].page
    prov.section.profile_combo.setCurrentIndex(
        prov.section.profile_combo.findData("local"))
    prov.section.model_edit.setText("m-local-2")
    pages["FreeCADAIProviderPrefs"].saveSettings()
    assert prov.section.current_label() == "local"


def test_a_placeholder_url_saves_and_warns(pages, warnings):
    prov = pages["FreeCADAIProviderPrefs"].page
    prov.section.base_url_edit.setText("https://x/{ACCOUNT_ID}/v1")
    _ok(pages)
    assert len(warnings) == 1


def test_after_save_runs(pages, monkeypatch):
    calls = []
    monkeypatch.setattr("freecad_ai.ui.command_state.set_command_checked",
                        lambda *a: calls.append(a))
    beh = pages["FreeCADAIBehaviorPrefs"].page
    beh.keep_dock_check.setChecked(not beh.keep_dock_check.isChecked())
    _ok(pages)
    assert len(calls) == 1


def test_provider_page_before_and_after_tools_page_agree(pages):
    """#10 order independence: an explicit reranker choice wins either way."""
    import freecad_ai.llm.providers as prov_mod
    idx = prov_mod.get_provider_names().index("github")
    for order in (ALL, list(reversed(ALL))):
        c = config_mod.get_config()
        c.rerank_method, c.rerank_top_n = "off", 15
        for p in pages.values():
            p.loadSettings()
        pages["FreeCADAIProviderPrefs"].page.section.provider_combo \
            .setCurrentIndex(idx)
        pages["FreeCADAIToolsPrefs"].page.rerank_method_combo \
            .setCurrentIndex(2)      # llm, an explicit choice
        for name in order:
            pages[name].saveSettings()
        assert config_mod.get_config().rerank_method == "llm"


class TestCloseHost:
    def _host(self, qapp, cls):
        dlg = QtWidgets.QDialog()
        lay = QtWidgets.QVBoxLayout(dlg)
        page = cls()
        lay.addWidget(page.form)
        return dlg, page

    def test_save_accepts_a_dialog_host(self, qapp, cfg, notified, monkeypatch):
        dlg, p = self._host(qapp, pp.FreeCADAIToolsPrefs)
        seen = []
        monkeypatch.setattr(dlg, "accept", lambda: seen.append("accept"))
        p.page.closeHostRequested.emit(True)
        assert seen == ["accept"]
        dlg.deleteLater()

    def test_discard_rejects_a_dialog_host(self, qapp, cfg, notified,
                                           monkeypatch):
        dlg, p = self._host(qapp, pp.FreeCADAIToolsPrefs)
        seen = []
        monkeypatch.setattr(dlg, "reject", lambda: seen.append("reject"))
        p.page.closeHostRequested.emit(False)
        assert seen == ["reject"]
        dlg.deleteLater()

    def test_host_can_close_reflects_the_window(self, qapp, cfg, notified):
        dlg, p = self._host(qapp, pp.FreeCADAIToolsPrefs)
        assert p.page.host_can_close() is True
        loose = pp.FreeCADAIToolsPrefs()
        assert loose.page.host_can_close() is False
        dlg.deleteLater()
        loose.form.deleteLater()
```

`load_config()` reads `config.json` from disk (the idiom `tests/unit/test_config.py` uses); reading the singleton would prove nothing about the second page's save.

`monkeypatch.setattr(dlg, "accept", …)` on a Qt object works because the handler looks up `window.accept` at call time. Make the handler do exactly that (`win = self.form.window(); win.accept()`), not a pre-bound method.

- [ ] **Step 2: Run to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_prefs_page.py -q`
Expected: FAIL with `AttributeError: module … has no attribute 'FreeCADAIProviderPrefs'`.

- [ ] **Step 3: Rewrite `prefs_page.py`**

```python
"""Edit → Preferences → FreeCAD AI: four pages, one per settings page (#101).

FreeCAD instantiates each class itself (InitGui.py registers them with
Gui.addPreferencePage) and calls loadSettings() each time Preferences
opens and saveSettings() on every OK and Apply, for every page, opened or
not. Each page writes only the fields that differ from what it loaded, so
an untouched OK leaves config.json byte-identical, and four pages saving in
turn never undo one another.

Every class needs its own module-level name: FreeCAD keys Python pages by
class __name__, and two classes sharing one showed as a single page. So
the common logic sits in a base class, not a factory.

Qt-heavy imports live in methods: InitGui imports this module at FreeCAD
startup, long before a page is first shown.
"""

from ..config import get_config, notify_config_changed, save_current_config
from ..i18n import QT_TRANSLATE_NOOP


def _warn(parent, title, text):
    from .compat import QtWidgets
    QtWidgets.QMessageBox.warning(parent, title, text)


class _PrefsPageBase:
    _TITLE = ""

    def __init__(self, parent=None):
        from .compat import QtWidgets
        from ..i18n import translate

        self.form = QtWidgets.QWidget(parent)
        self.form.setWindowTitle(translate("SettingsDialog", self._TITLE))
        layout = QtWidgets.QVBoxLayout(self.form)
        self.page = self._make_page()
        layout.addWidget(self.page)
        layout.addStretch()
        self.page.closeHostRequested.connect(self._close_host)

    def _make_page(self):
        raise NotImplementedError

    def loadSettings(self):  # noqa: N802 — FreeCAD's name
        self._load(label=None)

    def _load(self, label):
        try:
            self.page.load(get_config(), label=label)
        except Exception as e:   # the page's baseline stays None: no writes
            try:
                import FreeCAD
                FreeCAD.Console.PrintError(
                    f"FreeCAD AI: {self._TITLE} settings failed to load: {e}\n")
            except ImportError:
                pass

    def saveSettings(self):  # noqa: N802 — FreeCAD's name
        if not self.page.is_dirty():
            return
        cfg = get_config()
        self.page.apply_to(cfg)
        save_current_config()
        notify_config_changed()
        self.page.after_save(cfg)
        # Re-baseline, so a second Apply writes nothing; stay on the
        # profile being edited rather than jumping back to the active one.
        self._load(label=self.page.view_label())
        self._after_saved(cfg)

    def _after_saved(self, cfg):
        pass

    def _close_host(self, save):
        """The page's editor prompt: close Preferences so the docked script
        editor is reachable. Accept runs every page's saveSettings(), safe
        because each writes only its own changes."""
        from .compat import QtWidgets
        win = self.form.window()
        if isinstance(win, QtWidgets.QDialog):
            win.accept() if save else win.reject()

    def _host_can_close(self):
        from .compat import QtWidgets
        return isinstance(self.form.window(), QtWidgets.QDialog)


class FreeCADAIProviderPrefs(_PrefsPageBase):
    _TITLE = QT_TRANSLATE_NOOP("SettingsDialog", "Provider")

    def _make_page(self):
        from .settings_pages.provider_page import ProviderPage
        return ProviderPage()

    def _after_saved(self, cfg):
        """saveSettings() cannot veto FreeCAD's OK, so warn after saving."""
        from ..i18n import translate
        from .provider_section import ProviderSection
        unfilled = ProviderSection._profiles_with_url_placeholder(cfg.profiles)
        if unfilled:
            _warn(self.form,
                  translate("SettingsDialog", "Profile cannot be used as set up"),
                  translate(
                      "SettingsDialog",
                      "The Base URL for %s still contains a placeholder such "
                      "as {ACCOUNT_ID}. Replace it with the value from your "
                      "provider account.") % ", ".join(unfilled))


class FreeCADAIBehaviorPrefs(_PrefsPageBase):
    _TITLE = QT_TRANSLATE_NOOP("SettingsDialog", "Behavior")

    def _make_page(self):
        from .settings_pages.behavior_page import BehaviorPage
        return BehaviorPage()


class FreeCADAIToolsPrefs(_PrefsPageBase):
    _TITLE = QT_TRANSLATE_NOOP("SettingsDialog", "Tools")

    def _make_page(self):
        from .settings_pages.tools_page import ToolsPage
        page = ToolsPage()
        # A host that cannot close cannot reveal the docked editor, so
        # Edit/New open externally instead of prompting.
        page.host_can_close = self._host_can_close
        return page


class FreeCADAIMcpPrefs(_PrefsPageBase):
    _TITLE = QT_TRANSLATE_NOOP("SettingsDialog", "MCP")

    def _make_page(self):
        from .settings_pages.mcp_page import McpPage
        return McpPage()
```

The placeholder-warning strings move from context `FreeCADAIPrefs` to `SettingsDialog`, which is identical to the dialog's veto text, so translations are shared. lupdate cannot see `translate("SettingsDialog", self._TITLE)`, so mark each title where it is defined, using the helper `freecad_ai/i18n.py` already exports:

```python
from ..i18n import QT_TRANSLATE_NOOP
...
class FreeCADAIProviderPrefs(_PrefsPageBase):
    _TITLE = QT_TRANSLATE_NOOP("SettingsDialog", "Provider")
```

The same goes for Behavior, Tools and MCP. `QT_TRANSLATE_NOOP` returns the text unchanged, so the module stays free of Qt widgets at import time. The i18n fallback is a plain function, and PySide's is a no-op marker. Check that `from ..i18n import QT_TRANSLATE_NOOP` doesn't construct a `QApplication`: the no-widgets import test catches that.

- [ ] **Step 4: Register them in `InitGui.py`.** Replace lines 355–363:

```python
# Register the FreeCAD AI pages in Edit → Preferences: the same four pages
# the Settings dialog shows, writing config.json directly (#99, #101).
try:
    from freecad_ai.ui import prefs_page as _pp
    for _cls in (_pp.FreeCADAIProviderPrefs, _pp.FreeCADAIBehaviorPrefs,
                 _pp.FreeCADAIToolsPrefs, _pp.FreeCADAIMcpPrefs):
        Gui.addPreferencePage(_cls, "FreeCAD AI")
except Exception as _e:
    import FreeCAD as _App
    _App.Console.PrintWarning(
        f"FreeCAD AI: preferences pages not registered: {_e}\n")
```

Grep `tests/` for `FreeCADAIPrefsPage` and `addPreferencePage`, and update any test that asserts on the old name. If none checks InitGui, add to `test_prefs_page.py`:

```python
def test_initgui_registers_the_four_pages_in_order():
    src = open(os.path.join(PROJECT_ROOT, "InitGui.py"), encoding="utf-8").read()
    start = src.index("_pp.FreeCADAIProviderPrefs")
    block = src[start:src.index("addPreferencePage", start)]
    assert [n for n in ALL if n in block] == ALL
    assert "FreeCADAIPrefsPage" not in src
```

Note: a source-text check like this is weaker than executing InitGui. InitGui needs FreeCAD, so the live probe in Task 10 is the real check.

- [ ] **Step 5: Run and commit**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_prefs_page.py -q`, then the full suite. Also `grep -rn "FreeCADAIPrefs\b\|FreeCADAIPrefsPage" freecad_ai InitGui.py translations/*.ts`: the `.ts` entries are removed by the next `update_translations.sh` run, and nothing in code may still reference them.

```bash
git add -A freecad_ai/ui/prefs_page.py InitGui.py tests/unit
git commit -m "feat(prefs): four Preferences pages sharing the dialog's pages (#101)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Live probe and docs

**Files:**
- Scratchpad: `…/scratchpad/probe99/probe101.py` (not committed)
- Modify: `CHANGELOG.md` (the `## [Unreleased]` section), `README.md` (Configuration bullets)
- Wiki repo `/home/alf/Projects/programming/misc/freecad-ai-wiki`: `Configuration.md` ("FreeCAD Preferences Page" section), and `Home.md` / `FAQ.md` where they mention the hint or a single page. Commit there separately. **Pushing the wiki is outward-facing: ask the maintainer first.**

**Interfaces:** none. This task verifies the whole branch in real FreeCAD.

- [ ] **Step 1: Write the probe.** Base it on `probe99/probe_check.py` and `run.sh`, which set isolated HOME, XDG and `FREECAD_AI_CONFIG_DIR` and run the 1.1.1 AppImage under `xvfb-run`. The script, run through `run.sh` with `PROBE=probe101.py`:
  1. Seeds an isolated `config.json` with two profiles.
  2. Opens `Gui.showPreferences("FreeCAD AI")` on a `QTimer.singleShot`, finds the `QTreeWidget` in the Preferences dialog, and writes the children of "FreeCAD AI" to the result file. Expected: `Provider, Behavior, Tools, MCP`.
  3. Takes the SHA-256 of `config.json`, clicks OK untouched, and hashes again. Expected: equal.
  4. Reopens, edits the Provider page model field and the MCP page port (found via `findChildren` on the page widget types), and clicks OK. It reads `config.json`: both edits present.
  5. Registers a config listener via `freecad_ai.config` (the same function the chat panel uses: grep `add_config_listener` or similar in `config.py`), repeats one edit, and OKs. Expected: the listener fired once.
  6. Reopens, edits the Behavior page max tokens, and emits `ToolsPage.closeHostRequested(True)`. Expected: the Preferences dialog is gone and max tokens is in `config.json`.
  7. Writes every line to `result-101.txt` and ends with `exit(0)`.

- [ ] **Step 2: Run it**

Run: `bash …/scratchpad/probe99/run.sh` with `PROBE=probe101.py`, then `cat …/scratchpad/probe99/result-101.txt`.
Expected: all six checks print `OK`. If any prints `FAIL`, stop and debug (superpowers:systematic-debugging) before writing docs. Quote the actual result lines in the final report; don't paraphrase them.

- [ ] **Step 3: CHANGELOG.** Under `## [Unreleased]` → `### Changed` (create it if missing, keeping the file's existing style):

```markdown
- **Edit → Preferences → FreeCAD AI now shows every setting**, in four pages
  (Provider, Behavior, Tools, MCP) built from the same widgets as the
  Settings dialog, so the two windows can no longer disagree (#101).
- The Settings dialog writes only the fields you changed, so a hand-edited
  value a dropdown cannot show is no longer overwritten by an unrelated OK.
- Fields regrouped: a new *Limits* group (max output tokens, context
  window, max tool-loop turns, code execution timeout); viewport capture
  moved into *Behavior*; the MCP group split into *MCP Servers* and
  *Built-in MCP Server*. "Model supports tool calling" is now "Use tool
  calling", which is what the switch does.
- *Default mode* is gone from Preferences: the chat panel's Plan/Act
  dropdown is the control, and it overwrote the Preferences value anyway.
- *Test Reranker* moved next to the Reranker utility dropdown. A provider's
  recommended reranker settings (#10) now apply when you save rather than
  flipping the fields on screen.
- Test Connection uses the saved max output tokens and thinking mode.
```

Under `### Fixed`:

```markdown
- A profile whose provider this version does not know is shown as
  "<name> (unknown provider)" instead of as Anthropic, and picking
  Anthropic for it actually switches (#101).
```

If the maintainer overturns Ruling 1 at plan review, drop the Test Connection line.

- [ ] **Step 4: README.** In the Configuration section, replace the bullet(s) describing the Preferences page's subset or hint with: "Edit → Preferences → FreeCAD AI has four pages (Provider, Behavior, Tools, MCP) showing the same settings as the workbench's Settings dialog (gear icon); both write `config.json` directly." Grep README for "Preferences" and fix every mention of "one page" / "open the full settings dialog".

- [ ] **Step 5: Wiki.** In the wiki repo:
  - update `Configuration.md`: the "FreeCAD Preferences Page" section describes the four pages and their groups, the removed Default mode, and the Limits group;
  - grep `Home.md` and `FAQ.md` for "Preferences", "full settings dialog" and "Default mode", and update them;
  - commit locally with the same trailer, but **do not push**; ask at handoff.

- [ ] **Step 6: Final suite and commit (main repo)**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py`. Expected: green, with the test count higher than the 1917 on master.

```bash
git add CHANGELOG.md README.md
git commit -m "docs: four Preferences pages, regrouped settings (#101)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

Remind the maintainer: `translations/update_translations.sh` runs before the next release tag, not in this branch.
