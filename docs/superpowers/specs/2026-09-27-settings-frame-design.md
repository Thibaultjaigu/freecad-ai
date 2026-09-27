# Settings frame: every section shared by the dialog and Preferences

**Issue:** #101. Edit → Preferences → FreeCAD AI and the workbench's Settings
dialog should offer **exactly the same settings from the same widgets**. The
dialog stays, but becomes a thin frame with no settings logic of its own.
Also in scope: a profile whose provider the dropdown doesn't know is shown
as "Anthropic".
**Predecessor:** #99 / PR #100 (`ac732a4`) shared *LLM Provider* and
*Utility models* through `ProviderSection` and retired the param-store
bridge. This design finishes the job for the remaining groups.

## The problem

After #100 the two windows share one widget, but not the rest:

- `freecad_ai/ui/settings_dialog.py` (2210 lines) still builds Model
  Parameters, Behavior, System Prompt, Tool Reranking, MCP Servers, Editor,
  User Tools, Skills and Hooks itself, and its `_save()` writes **every**
  field on OK.
- `freecad_ai/ui/prefs_page.py` has its own small *Behavior* group (context
  `FreeCADAIPrefs`) with Default mode, which the dialog lacks, and a hint
  pointing at the dialog for everything else.

Two implementations of one setting drift apart; #12 and #97 were that bug.

## Decisions (maintainer, 2026-09-27)

1. **Four Preferences pages** under the "FreeCAD AI" group, split along the
   sections' dependencies (below). Rejected: one page per group box (ten
   entries, and coupled groups still have to share), two long pages.
2. **Editor open from a Preferences page:** same Save / Discard / Cancel
   prompt as the dialog, then the section asks its host window to close.
   Rejected: silently opening externally on one side only (the #97 kind of
   divergence); disabling Edit/New there.
3. **Cut the cross-page links** rather than add a shared session object.
   Test Reranker moves next to the Reranker utility dropdown; the #10
   reranker defaults move from a live widget reaction to save time.
   Accepted cost: in the dialog, the reranker fields no longer visibly flip
   before OK after a provider switch.
4. The gear / toolbar / menu button (`FreeCADAI_OpenSettings`) keeps opening
   the dialog.
5. **Sort the fields while moving them** (see "Regrouping" below): a new
   *Limits* group, viewport capture into Behavior, the tool-calling switch
   relabelled, and *Default mode* dropped.

## Live probe (FreeCAD 1.1.1 AppImage, Xvfb, isolated HOME/XDG dirs)

Two extra Python pages registered with
`Gui.addPreferencePage(cls, "FreeCAD AI")` next to the existing one:

```
item: FreeCAD AI
  item: FreeCAD AI
  item: Probe Two
  item: Probe Tall
probe_Probe Two: hint=127 parents=['Gui::Dialog::PreferencePagePython', 'QStackedWidget', 'QStackedWidget', 'QWidget', 'QWidget', 'QScrollArea', 'QFrame']
probe_Probe Tall: hint=1852 parents=[... 'QScrollArea', 'QFrame']
dialog_size=936x900
```

- Several pages in one group work, **but each page class needs a distinct
  `__name__`**. In the first run both probe classes were named `P`; the tree
  showed two entries, both "Probe Tall", and "Probe Two" was never built.
  FreeCAD keys Python pages by class name.
- Pages sit inside FreeCAD's own `QScrollArea` (page area about 664×786
  px). An 1852 px page scrolls instead of clipping, so the #78 sizing trap
  does not apply here.

## Architecture

### Pages are the shared unit

New package `freecad_ai/ui/settings_pages/`, one module per page, each a
`QWidget` built from code moved out of `settings_dialog.py`:

| Page class | Module | Group boxes |
|---|---|---|
| `ProviderPage` | `provider_page.py` | `ProviderSection` (LLM Provider + Utility models), Model Parameters (the per-profile parameter table only), Test Connection row, Test Reranker row (moved beside the Reranker utility dropdown) |
| `BehaviorPage` | `behavior_page.py` | **Limits** (new: max output tokens, context window, max tool-loop turns, code execution timeout), Behavior (use tool calling, auto-execute, keep dock, thinking, strip thinking history, preserve reasoning, prompt caching, token-usage logging, viewport capture + capture resolution), System Prompt (prompt text and Reset to Default only) |
| `ToolsPage` | `tools_page.py` | Tool Reranking (method, top N, pinned tools), User Tools (incl. scan macros), Skills, Hooks, Editor (external editor) |
| `McpPage` | `mcp_page.py` | **MCP Servers** (the servers the workbench connects to: list + Add/Edit/Remove) and **Built-in MCP Server** (new group box: host, port, allowed Host headers, bearer token, the security warning) |

Apart from the regrouping below, widgets move with their current labels,
tooltips and code. The parameter table stays wired to `ProviderSection` through its existing
`profileShown` / `aboutToCommit` / `presetApplied` / `modelChanged` signals —
links that now stay within one page. `_TestConnectionThread`, `_TestRerankerThread` and
`_AddMCPServerDialog` move with the page that uses them.

`ProviderSection` stays in `freecad_ai/ui/provider_section.py`.

### Regrouping (maintainer, 2026-09-27)

Moving every widget anyway, the groups are sorted by what the fields are:

- **Limits group.** `max_tokens`, `context_window`, `max_tool_turns` and
  `execution_timeout` are global `config.json` fields. Beside the
  per-profile parameter table they read as per-profile, which they are not.
  They get their own group box, *Limits*, first on the Behavior page, with
  their current labels and ranges. Model Parameters keeps only the table and
  its Add / Remove / Load Defaults buttons.
- **Viewport capture → Behavior.** Capture mode and resolution sat inside the
  *System Prompt* group; they decide when screenshots go to the model, not
  what the prompt says.
- **Tool-calling switch relabelled.** `enable_tools` is a global switch —
  chat uses tools only when it is on, the mode is Act, and the active
  profile supports tools (`chat_widget.py`, `use_tools = cfg.enable_tools
  and mode == "act" and cfg.supports_tools`). The label "Model supports tool
  calling (uncheck to fall back to code generation)" describes the profile's
  capability instead; it becomes **"Use tool calling (uncheck to fall back to
  code generation)"**. Field and behavior unchanged.
- **Default mode dropped.** `cfg.mode` is the chat panel's Plan/Act
  dropdown, saved on every flip (`ChatDockWidget._on_mode_changed`); it is
  not a default. The #100 Preferences page edited it remotely, and the panel
  never re-reads it (`_on_config_changed` watches provider, model and MCP
  only), so with the panel open the edit did not show and the next toggle
  overwrote it. No page shows it; the panel's dropdown is its only control.
  `cfg.mode` itself is unchanged.
- **MCP group split** (proposed in the spec, for the maintainer's review):
  the client list and the workbench's own server settings are two
  directions, so two group boxes on the MCP page — *MCP Servers* (unchanged
  title) and *Built-in MCP Server* (new).

New strings: "Limits", "Use tool calling (uncheck to fall back to code
generation)", "Built-in MCP Server" — context `SettingsDialog`, untranslated
until the next `update_translations.sh` run.

### The page interface

Every page implements:

```python
def load(self, cfg) -> None          # show cfg, record the baseline
def is_dirty(self) -> bool           # widgets differ from the baseline
def apply_to(self, cfg) -> None      # set only the fields that differ
def after_save(self, cfg) -> None    # post-save side effects (may be a no-op)
closeHostRequested = Signal(bool)    # True = save first, False = discard
```

`after_save` holds side effects that must follow a save in *both* windows,
e.g. `BehaviorPage` calling `set_command_checked("FreeCADAI_ToggleKeepDock",
cfg.keep_dock_on_workbench_switch)` (today only the dialog does this).

A shared base class `SettingsPage` in `settings_pages/base.py` provides the
signal, a `None` baseline, and the default no-op `after_save`. Pages with
fields keep a `_values()` → dict of the fields they own; `is_dirty` and
`apply_to` compare it with the baseline, the same shape as #100's
`_behavior_values`.

### The Settings dialog is a frame

`SettingsDialog` keeps: the scroll area holding the four pages in order
(Provider, Behavior, Tools, MCP), OK / Cancel, the width-from-layout sizing
(#78), and what only a dialog can do — the `_confirm_incomplete_profiles`
veto before saving. Its OK:

1. `provider_page.section.commit()`, then the placeholder veto (Cancel →
   return, dialog stays open);
2. `apply_to(cfg)` on every page, in order;
3. `save_current_config()` once, `notify_config_changed()` once;
4. `after_save(cfg)` on every page; `accept()`.

On `closeHostRequested(True)` it runs that OK path; on `False`, `reject()`.

### Preferences: four page classes

`prefs_page.py` defines four module-level classes with distinct names —
`FreeCADAIProviderPrefs`, `FreeCADAIBehaviorPrefs`, `FreeCADAIToolsPrefs`,
`FreeCADAIMcpPrefs` — each wrapping one page widget in `self.form` (window
titles "Provider", "Behavior", "Tools", "MCP", context `SettingsDialog`).
Common logic lives in one base class (not a factory: nested classes share a
`__name__`). `InitGui.py` registers all four under "FreeCAD AI" in that
order.

- `loadSettings()` → `page.load(get_config())`; an exception leaves the
  baseline `None` and is logged.
- `saveSettings()` → if baseline is `None` or not `page.is_dirty()`: return.
  Otherwise `apply_to(get_config())`, `save_current_config()`,
  `notify_config_changed()`, `after_save`, reload to re-baseline (keeping
  the shown profile, as #100 does), then the placeholder warning (the
  provider page only; `saveSettings()` cannot veto FreeCAD's OK).
- `closeHostRequested(save)` → if `form.window()` is a `QDialog`, call its
  `accept()` / `reject()` (accept runs every page's `saveSettings()`, safe
  because each writes only its own changes); otherwise open the file with
  the external editor instead.

### Removed

- The `FreeCADAIPrefs` Behavior group, its translation context, and the
  "open the full settings dialog" hint — including its Default mode
  control, which no page replaces (see Regrouping).
- `SettingsDialog._on_preset_applied`'s reranker reaction and
  `_rerank_at_factory_defaults` / `_apply_rerank_defaults` in their widget
  form (replaced below).
- `ProviderSection._stand_in_index`.

## Save semantics

- **Field-level diff, onto the live config.** `apply_to` writes onto
  `get_config()` at save time — the singleton, so the latest state, never
  the load-time snapshot. FreeCAD calls `saveSettings()` on all four pages
  in turn; each adds only its own changes, so none undoes another's.
- **Behavior change for the dialog:** OK now writes only edited fields
  instead of every field. A hand-edited value a dropdown can't show (say
  `thinking: "max"`) displays as the first entry and is no longer overwritten
  by an unrelated OK — the rule #100 introduced for the Preferences page.
- **Special baselines:**
  - *System Prompt:* baseline is the text shown. Unedited → nothing written.
    Edited to equal the generated default → `system_prompt_override = ""`,
    as today.
  - *Model Parameters:* belong to the profile; they ride on
    `ProviderSection`'s own dirty tracking and commit, not a page baseline.
  - *MCP list and server address:* compared as parsed values (the list of
    entry dicts; `_parse_server_address` / `_parse_allowed_hosts` output),
    so re-typing the same host is not a change.
- **File actions stay immediate**, as today, in both windows: add / new /
  remove hooks and user tools, reset a skill to built-in. They touch disk,
  not `config.json`. Only the config checkboxes in those groups (scan
  macros, external editor) go through the baseline. The live external-editor
  checkbox still governs the current Edit/New action.
- **Errors:** a page whose `load` raised writes nothing (the #99 rule, per
  page); the others still save. A failed disk write raises as today.

## #10 reranker defaults at save time

`ProviderSection` records, on each user provider switch (`_on_provider_changed`),
the preset's `default_rerank` if it has one (last switch wins). Its
`apply_to(cfg)` then, if a recorded default exists and the live config's
reranker is at factory defaults (`rerank_method == "off"` and
`rerank_top_n == 15`), sets `rerank_method` / `rerank_top_n` from it. `load`
clears the record.

Order independence: if `ToolsPage` saved an explicit reranker choice first,
the config is no longer at factory defaults and is left alone; if it saves
after, its diff overwrites the preset values. An explicit choice of exactly
off/15 is indistinguishable from factory defaults — the same as today's
widget check.

## Unknown provider display

`ProviderSection._show_profile`, for a `prof.name` not in
`get_provider_names()`:

- appends one temporary item at the **end** of `provider_combo`, text
  `"<name> (unknown provider)"` (translatable, context `SettingsDialog`,
  `%s` placeholder), item data = the stored name, and selects it with
  signals blocked;
- any previous temporary item is removed first, and again whenever a
  profile with a known provider is shown.

Picking a real provider is an ordinary switch (`_on_provider_changed` runs:
preset applied, name recorded) and removes the temporary item.
`_on_provider_changed` already returns early for an index outside the real
provider list. `_commit_profile_fields` keeps `prof.name` unchanged while the
temporary item is selected, so an untouched OK preserves it (#100's
deliberate change 1). Picking Anthropic for such a profile now actually
switches — the stand-in problem is gone because nothing stands in.

## Testing

Unit (no FreeCAD, offscreen Qt), `env PYTHONPATH= .venv/bin/pytest tests/unit
-q --ignore=tests/unit/test_document_attach.py` green before every commit:

- Existing `test_settings_dialog_*.py` and `test_prefs_page.py` follow the
  widgets to their pages, assertions unchanged unless a decision above
  changes the behavior.
- Per page: untouched load → `apply_to` leaves the config equal; one edited
  field → only that field changes; two pages applied in sequence keep each
  other's edits; `load` raising → `saveSettings` writes nothing;
  `BehaviorPage.after_save` sets the keep-dock checkmark.
- Frames: the dialog holds the four pages and calls save and notify exactly
  once; the four Preferences classes have distinct `__name__`s and
  `InitGui` registers four under "FreeCAD AI"; `closeHostRequested(True /
  False)` reaches `_save` / `reject` in the dialog and `accept` / `reject`
  on a Preferences `QDialog`, and the external-editor fallback otherwise.
- Unknown provider: temporary item shown and selected; removed on a real
  pick and on a profile switch; picking Anthropic switches; untouched
  commit keeps `"foo"`.
- #10: provider page before tools page and after give the same config;
  an explicit non-default reranker survives.
- Dialog geometry (#78) test still passes with the pages inside.
- Regrouping: the Limits fields live on `BehaviorPage` and load / apply
  `max_tokens` etc.; the viewport combos live in the Behavior group; no page
  has a mode control and saving any page leaves `cfg.mode` as it was; the
  tool-calling checkbox carries the new label; the MCP page has both group
  boxes.

Live probe (FreeCAD 1.1.1, Xvfb, isolated dirs, the #99 harness):

- four pages appear under "FreeCAD AI" in order;
- untouched OK → `config.json` byte-identical;
- edits on the Provider and MCP pages in one OK → both in the file;
- the chat panel's config listener fires;
- `closeHostRequested(True)` from a page closes Preferences with its edit
  saved.

## Docs

- CHANGELOG (`### Changed`): Preferences shows every setting in four pages;
  the dialog writes only edited fields; fields regrouped (Limits, viewport
  capture, split MCP group); tool-calling switch relabelled; Default mode
  removed from Preferences (the chat panel's Plan/Act dropdown is the
  control); Test Reranker moved; #10 defaults
  apply on save; unknown providers are labelled as such.
- README Configuration bullets; wiki Configuration "FreeCAD Preferences
  Page" section, Home / FAQ where they mention the hint or the one page.
- Moved strings keep context `SettingsDialog`; run
  `translations/update_translations.sh` before the next tag.

## Out of scope

- Pointing the gear button at `Gui.showPreferences` (a later one-line change
  in the command).
- New settings (retention knobs, dialog size memory) — the UX backlog.
- Changing what the file actions do.
