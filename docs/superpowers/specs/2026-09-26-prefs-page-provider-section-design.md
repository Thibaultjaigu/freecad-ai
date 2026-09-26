# Preferences page: the Settings dialog's provider sections

**Issue:** #99. The **Edit → Preferences → FreeCAD AI** page should show the
same *LLM Provider* and *Utility models* sections as the workbench's Settings
dialog, from one shared widget rather than a second copy.
**Predecessors:** #12 and #97, the same bug twice: the preferences page wrote a
value that silently rewrote the active profile. #98 (`7c3b1de`) is the stopgap
this design retires.

## The problem

The preferences page is still the pre-profiles design:
`resources/panels/FreeCADAIPrefs.ui`, registered in `InitGui.py` with
`Gui.addPreferencePage(<ui path>, "FreeCAD AI")`, holding eight `Gui::Pref*`
widgets. Those widgets read and write FreeCAD's parameter store
(`BaseApp/Preferences/Mod/FreeCADAI`), and `config.py` mirrors that store
into `config.json` and back: `_apply_param_store_overrides` in `load_config`,
`_write_to_param_store` in `load_config` and `save_config`.

Three consequences:

1. The page cannot show profiles or utility routing. A single set of widgets
   in the parameter store can't represent a dictionary of profiles.
2. It edits `cfg.provider`, which since connection profiles is **the active
   profile**. Whatever the widgets write lands in a profile the user never
   opened.
3. `Gui::Pref*` widgets save on every OK in Edit → Preferences, on every page,
   whether or not the user touched them (probed live on FreeCAD 1.1.1 for
   #97). So any value a widget can't represent correctly is written back
   wrong.

## Decisions

- **Scope (maintainer, 2026-09-26):** the page mirrors exactly the two
  sections, *LLM Provider* and *Utility models*, plus today's *Behavior*
  group. Everything else stays in the dialog only.
- **Approach A (maintainer, 2026-09-26):** extract one shared widget.
  Rejected: borrowing a hidden `SettingsDialog`'s widgets (its `_save`
  writes every section, and the page would depend on dialog internals), and
  building the sections a second time (two copies drift, which is the
  problem being fixed).
- `config.json` becomes the only store. The parameter-store bridge is
  removed, with a one-time migration.

## Components

### `freecad_ai/ui/provider_section.py`: `ProviderSection(QWidget)` (new)

Holds the two group boxes now built inline in `SettingsDialog._build_ui`:

- *LLM Provider*: profile combo with New / Rename / Delete, "Use this profile
  for chat", provider, API key, base URL, model, and the vision row (checkbox,
  status label, Reset).
- *Utility models*: one combo per entry in `SettingsDialog.UTILITIES`
  (moves to the section).

It owns the scratch copy that `SettingsDialog` keeps today: `profiles`,
`active_profile`, `utility_profiles`. The methods that operate on it move
with it, behaviour unchanged: `_show_profile`, `_commit_profile_fields`,
`_on_profile_changed`, `_on_profile_add`, `_on_profile_rename`,
`_on_profile_delete`, `_on_profile_active_toggled`, `_rename_profile`,
`_delete_profile`, `_refresh_profile_combo`, `_refresh_utility_combos`,
`_on_utility_combo_changed`, `_collect_utility_profiles`,
`_profiles_with_url_placeholder`, the preset half of `_on_provider_changed`,
and the vision UI (`_update_vision_ui`, `_on_vision_override_changed`,
`_reset_vision_override`).

Public interface:

| Member | Purpose |
|---|---|
| `load(cfg)` | Fill the widget and its scratch copy from a config; set the change-tracking baseline. |
| `apply_to(cfg)` | Commit the visible fields, then write `profiles`, `active_profile`, `utility_profiles` into `cfg`. |
| `current_label()` / `current_profile()` | The profile being edited (Test Connection probes it). |
| `utility_selection(name)` | A utility combo's live choice (the reranker test reads `"rerank"`). |
| `set_probe_result(label, ...)` | Record a probe's findings on a profile and refresh the vision row. |
| `is_dirty()` | Whether anything differs from the `load` baseline. |

Signals, so the dialog keeps its extra features without the section knowing
about them:

| Signal | Emitted | Dialog uses it to |
|---|---|---|
| `profileShown(ProviderConfig)` | after a profile is shown | reload the Model Parameters table |
| `aboutToCommit(ProviderConfig)` | inside `_commit_profile_fields`, before returning | write the table into `prof.params` |
| `presetApplied(dict)` | after a user provider switch applies a preset | apply `default_rerank` (#10) |
| `modelChanged(str)` | when the model field is edited | stash and swap the params table |

`is_dirty()` compares a snapshot taken at `load` (profiles serialised through
`to_dict`, `active_profile`, `utility_profiles`) against the current state
after committing the visible fields. Comparing state rather than counting
edits means that typing a value and then restoring it counts as clean.

### `SettingsDialog` (changed)

Builds one `ProviderSection` where the two groups were, connects the four
signals, and delegates: `_load_from_config` calls `section.load(cfg)`, `_save`
calls `section.apply_to(cfg)`, and the Test Connection and reranker test paths
read `section.current_profile()` and `section.utility_selection("rerank")`.
The Model Parameters table, reranker settings and all other groups stay.
Save-time confirmation for the `{ACCOUNT_ID}` placeholder
(`_confirm_incomplete_profiles`) stays in the dialog, using the section's
`_profiles_with_url_placeholder`.

### `freecad_ai/ui/prefs_page.py`: `FreeCADAIPrefsPage` (new)

A FreeCAD Python preferences page: a class with a `form` attribute and
`loadSettings()` / `saveSettings()`, registered in `InitGui.py` with
`Gui.addPreferencePage(FreeCADAIPrefsPage, "FreeCAD AI")`. Its `form` holds:

- a `ProviderSection`;
- a plain *Behavior* group: default mode, thinking, max tokens, enable tool
  calling. These are ordinary Qt widgets, not `Gui::Pref*` widgets;
- the existing hint pointing to the full Settings dialog for everything else.

No Model Parameters table and no Test Connection button. The vision row can
show "(not tested)"; the manual override works. Qt-heavy imports happen in
`__init__`, not at `InitGui` import time.

`resources/panels/FreeCADAIPrefs.ui` and `paths.get_prefs_ui_path()` are
deleted.

## Load and save

**Load.** FreeCAD calls `loadSettings()` each time Preferences opens. The page
calls `section.load(get_config())` (the live config object the chat panel
also uses), fills the Behavior widgets, and records their baseline.

**Save only when something changed.** FreeCAD calls `saveSettings()` on every
page for every OK and Apply. If neither `section.is_dirty()` nor a Behavior
value differs from its baseline, the page returns without writing, so an OK
elsewhere in Preferences leaves `config.json` byte-identical. Otherwise it
calls `section.apply_to(cfg)`, sets the Behavior fields, calls
`save_current_config()` and `notify_config_changed()`, then re-baselines by
calling `load` again, so a second Apply writes nothing.

**The `{ACCOUNT_ID}` guard warns after saving.** `saveSettings()` cannot veto
FreeCAD's OK. The page saves, then shows a `QMessageBox.warning` naming each
profile whose Base URL still contains the placeholder.

**Before the workbench loads.** The page works without the chat panel:
`get_config()` needs only `config.py`, and no listener is registered yet.

The Settings dialog and Preferences are both modal, so the two never edit at
the same time.

## Config-changed notification

`config.py` gains `add_config_listener(fn)`, `remove_config_listener(fn)` and
`notify_config_changed()`.
Listeners are called with no arguments; one that raises is logged and
skipped, and does not stop the others.

The chat panel's post-dialog refresh (`chat_widget.py`, after
`SettingsDialog.exec()`) moves into a listener, `_on_config_changed`. It keeps
its own last-seen snapshot of `(provider.name, provider.model, mcp_servers)`
and, exactly as today, resets `_vision_fallback_tool` when the provider or
model changed, disconnects MCP servers when their list changed, then calls
`_ensure_vision_fallback()` and `_refresh_image_controls()`. The chat panel
registers the listener when it is created and removes it on its `destroyed`
signal. Without the removal, a closed panel would stay alive through the
listener list and raise `RuntimeError` on its deleted Qt object. `SettingsDialog._save` and the
preferences page both call `notify_config_changed()` after saving, so there
is one refresh path.

## Retiring the parameter-store bridge

Removed from `config.py`: `_apply_param_store_overrides`,
`_write_to_param_store`, `_PARAM_PROVIDERS`, `_PARAM_MODES`,
`_PARAM_THINKING`, and their calls in `load_config` and `save_config`.
Removed from `InitGui.py`: the `.ui` registration and the startup block that
seeds the parameter store. Removed from the tests: the bridge tests in
`test_config.py`, including the #97 tests from `7c3b1de`. The page now takes
its provider list from `get_provider_names()`, like the dialog, so the lists
can no longer diverge.

`_get_param_group()` stays, used only by the migration.

### One-time migration

Covers one case: a value changed in Edit → Preferences under an old version,
followed by an upgrade before the workbench loaded again. That value exists
only in the parameter store.

`load_config()` calls `_migrate_param_store(cfg)` after reading the JSON:

- If the group is missing or has no keys, do nothing. This is the normal
  path from the second start onward.
- Otherwise apply the eight fields exactly as `_apply_param_store_overrides`
  did. The old order-based lists are frozen as private constants inside the
  migration. As #12 requires, a missing `ProviderIndex` leaves the provider
  alone, and an out-of-range index is ignored.
- Save `config.json`, then remove the group (`RemGroup` on its parent). The
  missing group is the marker; no version flag is needed.

The eight fields are `ProviderIndex`, `Model`, `BaseUrl`, `ApiKey`,
`ModeIndex`, `ThinkingIndex`, `MaxTokens`, `EnableTools`.

## Visible changes (CHANGELOG, under *Changed*)

- Edit → Preferences → FreeCAD AI shows profiles and utility models, like the
  Settings dialog.
- FreeCAD's `user.cfg` no longer holds FreeCAD AI settings under
  `Mod/FreeCADAI`; `config.json` is the only store. Scripts that read
  `ParamGet(".../Mod/FreeCADAI")` stop seeing values.
- Cloudflare Workers AI's position in the Settings dialog from #97 stays.

Wiki: update any page that describes the preferences page or the
parameter-store mirror (checked during implementation).

## Testing

**Existing behaviour tests move over unchanged.** Tests calling the moved
methods on `SettingsDialog`, in `test_profile_selector.py`,
`test_per_profile_capabilities.py`, `test_settings_dialog_provider_change.py`,
`test_settings_dialog_base_url_guard.py` and
`test_probe_status_names_profile.py`, are pointed at `ProviderSection` with
the same assertions. Where a test uses a `MagicMock` stand-in for `self`, it
moves to a real `ProviderSection` under offscreen Qt where practical.
Dialog-only behaviour (params table, reranker defaults) stays tested on the
dialog, plus tests that the four signals are connected.

**New, written first:**

- `load` then `apply_to` round-trips losslessly, including fields the page
  does not show: `params`, `vision_detected`, `tools_detected`,
  `thinking_detected`, `vision_override`.
- `is_dirty()` is false after `load`; true after each kind of edit (field,
  New, Rename, Delete, the "use for chat" tick, a utility combo); false again
  after an edit is reverted.
- Preferences page: `saveSettings()` on an unchanged page leaves
  `config.json` byte-identical; with a change it saves and re-baselines, and
  a second call does not write; a `{ACCOUNT_ID}` Base URL saves and warns.
- Notification: the dialog and the page both call `notify_config_changed()`;
  a raising listener does not block the others; a removed listener is not
  called; the chat panel's listener
  resets the vision fallback and disconnects MCP servers under the same
  conditions as the code it replaces.
- Migration: keys are applied once and the group is removed; the second load
  is a no-op; a missing `ProviderIndex` leaves the provider alone; an
  out-of-range index is ignored.

**Live, FreeCAD 1.1.1 under Xvfb** with isolated `HOME`, `XDG_CONFIG_HOME`,
`XDG_DATA_HOME` and `FREECAD_AI_CONFIG_DIR`:

- the page registers and lists the profiles;
- OK on an untouched page, with Preferences opened on the FreeCAD AI page and
  on *General*, leaves `config.json` byte-identical;
- an edited model is saved, and a running chat panel's listener fires;
- a pre-seeded old parameter store is migrated and the group removed.

**Maintainer GUI pass:** the preferences page and the Settings dialog side by
side, because looking the same is the goal.

The full unit suite runs before every commit.
