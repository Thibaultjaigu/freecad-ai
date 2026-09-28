# Per-profile Thinking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each connection profile carry its own thinking value, sent to the vendor verbatim, with "Use global" as the default so upgrades change nothing (#108).

**Architecture:** A new `ProviderConfig.thinking: str | None` field. `create_client` resolves call site > profile > global into the one `thinking` string `LLMClient` already takes. The request builders sort that string with a new `_thinking_kind()` helper into off / preset (`on`, `extended`) / default / budget (digits) / level (any other word); off and preset keep today's bytes exactly. The shared `ProviderSection` gets an editable combo, and Test Connection sends the profile's value.

**Tech Stack:** Python 3.11, PySide6/PySide2 via `freecad_ai/ui/compat.py`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-28-per-profile-thinking-design.md`

## Global Constraints

- Test command: `env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py` (the `PYTHONPATH=` is required; `test_document_attach.py` segfaults on clean master too).
- Never touch the real FreeCAD / FreeCAD AI config; tests build `AppConfig()` objects in memory.
- Qt: import only through `freecad_ai/ui/compat.py`; use **flat** enums (`QtWidgets.QComboBox.NoInsert`, `QtCore.Qt.ToolTipRole`) — PySide2 accepts only flat.
- `off`, `on`, `extended` must produce **byte-identical** requests to today on every provider. The golden fixtures (`tests/unit/test_golden_request_bodies.py`) must pass unchanged — never regenerate them.
- Values are sent verbatim and case-sensitively; no validation beyond stripping whitespace.
- A profile's `thinking` defaults to `None` ("Use global").
- Commits end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Wiki commits stay local (pushed at release).

## Review Focus

1. A hand-edited `"thinking": 5` or `"thinking": "  "` in config.json — expect `None` (global), a warning for the non-string, no crash. → Task 1 tests.
2. A user types `Low` (capitalised) or ` low ` — expect `Low` sent as typed / `low` stripped; no lowercasing. → Task 1 (strip) and Task 5 (combo round-trip) tests.
3. Switching a profile's vendor from Anthropic to Ollama with `xhigh` typed — expect the value kept while suggestions change. → Task 5 test.
4. The reranker on a profile with `thinking="high"` — expect it still forced `off`. → Task 4 test.
5. Act mode (tools sent) with the global `on` — expect still no `reasoning_effort` (unchanged), while a profile level *is* sent with tools. → Task 3 tests.

---

### Task 1: Store `thinking` on the profile

**Files:**
- Modify: `freecad_ai/config.py` (`ProviderConfig`, `_profile_from_dict`)
- Test: `tests/unit/test_per_profile_thinking.py` (create)

**Interfaces:**
- Produces: `ProviderConfig.thinking: str | None = None`; `_profile_from_dict` yields a stripped non-empty string or `None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_per_profile_thinking.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_per_profile_thinking.py -q`
Expected: FAIL — `AttributeError: 'ProviderConfig' object has no attribute 'thinking'` (and the unknown-key path dropping `thinking`).

- [ ] **Step 3: Implement**

In `ProviderConfig`, after the `context_window` field:

```python
    # Thinking for this profile's model, sent to the vendor as typed (#108).
    # None = use the global AppConfig.thinking. "default" sends no thinking
    # field at all; "off"/"on"/"extended" mean what they mean globally.
    thinking: str | None = None
```

In `_profile_from_dict`, after the `context_window` check and before `return`:

```python
    th = kept.get("thinking")
    if th is not None:
        if not isinstance(th, str):
            logger.warning(
                "Profile thinking %s is not a string — using the global "
                "value instead.", json.dumps(th, default=str))
            kept["thinking"] = None
        else:
            kept["thinking"] = th.strip() or None
```

- [ ] **Step 4: Run to verify pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_per_profile_thinking.py tests/unit/test_profiles.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/config.py tests/unit/test_per_profile_thinking.py
git commit -m "feat(config): a thinking value per profile (#108)"
```

---

### Task 2: Classify thinking values; Anthropic builder and headers

**Files:**
- Modify: `freecad_ai/llm/client.py` (new `_thinking_kind` after `_current_claude_format`; `_anthropic_headers`; the thinking/temperature block of `_anthropic_body`)
- Test: `tests/unit/test_per_profile_thinking.py`

**Interfaces:**
- Produces: `_thinking_kind(value: str) -> str` returning one of `"off"`, `"preset"`, `"default"`, `"budget"`, `"level"`. Task 3 consumes it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_per_profile_thinking.py`:

```python
from freecad_ai.llm.client import LLMClient, _thinking_kind

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
```

- [ ] **Step 2: Run to verify failure**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_per_profile_thinking.py -q`
Expected: FAIL — `ImportError: cannot import name '_thinking_kind'`.

- [ ] **Step 3: Implement**

After `_current_claude_format` in `client.py`:

```python
def _thinking_kind(value: str) -> str:
    """How the request builders render a thinking value (#108).

    "off", "on" and "extended" are the global vocabulary and keep the
    bytes they always had ("off" / "preset"). "default" sends no thinking
    field. Anything else came from a profile and goes to the vendor as
    typed: ASCII digits are an Anthropic token budget, any other word a
    level. Case-sensitive — "Off" is a word the vendor gets to reject.
    """
    if value == "off":
        return "off"
    if value in ("on", "extended"):
        return "preset"
    if value == "default":
        return "default"
    if value.isascii() and value.isdigit():
        return "budget"
    return "level"
```

In `_anthropic_headers`, replace the beta-header condition:

```python
        # Only `type: enabled` thinking interleaves on request; adaptive
        # does it unasked, and current models never take `enabled` (#107).
        if (_thinking_kind(self.thinking) in ("preset", "budget")
                and not _current_claude_format(self.model)):
            headers["anthropic-beta"] = "interleaved-thinking-2025-05-14"
```

In `_anthropic_body`, replace the whole `if _current_claude_format(...) / elif / else` temperature-and-thinking block with:

```python
        kind = _thinking_kind(self.thinking)
        current = _current_claude_format(self.model)
        if kind == "level" or (kind == "preset" and current):
            # Adaptive thinking: current models take nothing else (#107),
            # and a level typed on a profile is sent whatever the model —
            # if the model refuses it, its 400 says so (#108).
            effort = (self.thinking if kind == "level" else
                      {"on": "medium", "extended": "high"}[self.thinking])
            body["thinking"] = {"type": "adaptive"}
            body["output_config"] = {"effort": effort}
            # A row the user set on this profile is still theirs to send.
            if "temperature" in self.model_params:
                body["temperature"] = self.model_params["temperature"]
        elif kind in ("preset", "budget"):
            # `type: enabled` requires temperature=1 and a budget.
            budget = (int(self.thinking) if kind == "budget" else
                      {"on": 4096, "extended": 16384}[self.thinking])
            body["temperature"] = 1
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
        elif current:
            # off / default. Current models 400 on a global temperature
            # and think by default regardless; no key at all, since Opus
            # 5.5 rejects `disabled` (#107).
            if "temperature" in self.model_params:
                body["temperature"] = self.model_params["temperature"]
        else:
            body["temperature"] = self.model_params.get(
                "temperature", self.temperature
            )
```

- [ ] **Step 4: Run to verify pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_per_profile_thinking.py tests/unit/test_anthropic_current_models.py tests/unit/test_golden_request_bodies.py -q`
Expected: PASS (the #107 tests and golden fixtures prove off/on/extended did not move).

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/llm/client.py tests/unit/test_per_profile_thinking.py
git commit -m "feat(llm): Anthropic sends a profile's thinking level or budget verbatim (#108)"
```

---

### Task 3: OpenAI-style builder (incl. Ollama)

**Files:**
- Modify: `freecad_ai/llm/client.py` (`_openai_body`: the Ollama tag and the `reasoning_effort` branch)
- Test: `tests/unit/test_per_profile_thinking.py`

**Interfaces:**
- Consumes: `_thinking_kind(value: str) -> str` from Task 2.

- [ ] **Step 1: Write the failing tests**

Append:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_per_profile_thinking.py -q -k OpenAIStyle`
Expected: FAIL — e.g. `KeyError: 'reasoning_effort'` with tools, and `/think` present for `xhigh`.

- [ ] **Step 3: Implement**

In `_openai_body`, add `kind = _thinking_kind(self.thinking)` as the first line of the method, then replace the Ollama tag block:

```python
            # For Ollama: append /think or /no_think tags for models that support them
            # (models that don't will just ignore these as text). Only for the
            # global vocabulary: a profile's own value travels as
            # reasoning_effort, which Ollama's /v1 validates (#108).
            if self.provider_name == "ollama":
                if kind == "off":
                    sys_content += "\n/no_think"
                elif kind == "preset":
                    sys_content += "\n/think"
```

and replace the `elif self.thinking != "off":` reasoning branch after `if tools:` so the block reads:

```python
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if kind in ("level", "budget"):
            # Set on the profile, so meant for Act mode too (#108).
            body["reasoning_effort"] = self.thinking
        # OpenAI reasoning models (o1, o3, etc.) — the global setting keeps
        # its old "not with tools" rule, so an upgrade changes no request.
        elif kind == "preset" and not tools:
            effort_map = {"on": "medium", "extended": "high"}
            body["reasoning_effort"] = effort_map[self.thinking]
```

- [ ] **Step 4: Run to verify pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_per_profile_thinking.py tests/unit/test_golden_request_bodies.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/llm/client.py tests/unit/test_per_profile_thinking.py
git commit -m "feat(llm): OpenAI-style APIs get a profile's reasoning_effort verbatim, tools or not (#108)"
```

---

### Task 4: Resolve call site > profile > global in `create_client`

**Files:**
- Modify: `freecad_ai/llm/client.py` (`create_client`)
- Test: `tests/unit/test_per_profile_thinking.py`

**Interfaces:**
- Consumes: `ProviderConfig.thinking` (Task 1).
- Produces: `create_client(...).thinking` = call-site value if not None, else the resolved profile's `thinking` if not None, else `cfg.thinking`.

- [ ] **Step 1: Write the failing tests**

Append:

```python
from freecad_ai.llm.client import create_client


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
```

- [ ] **Step 2: Run to verify failure**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_per_profile_thinking.py -q -k Resolution`
Expected: FAIL — the three profile-value tests get `"on"`.

- [ ] **Step 3: Implement**

In `create_client`, before `return LLMClient(`:

```python
    if thinking is None:
        thinking = (chosen.thinking if chosen.thinking is not None
                    else cfg.thinking)
```

and change the keyword argument to `thinking=thinking,`. Update the docstring's second paragraph:

```
    Connection settings (vendor, url, key, model, params) come from the
    resolved profile. Job settings (max_tokens, temperature, thinking)
    come from the config unless the call site overrides them — the
    reranker wants 1024 tokens and no thinking whichever profile it runs
    on. A valid ``max_tokens`` row in the profile's params, and a
    profile's own ``thinking`` value, sit between the two: they beat the
    config, never a call-site override (#103, #108).
```

- [ ] **Step 4: Run to verify pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_per_profile_thinking.py tests/unit/test_create_client.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/llm/client.py tests/unit/test_per_profile_thinking.py
git commit -m "feat(llm): a profile's thinking beats the global one, never a call site (#108)"
```

---

### Task 5: Thinking combo in the shared provider section

**Files:**
- Modify: `freecad_ai/ui/provider_section.py` (`_build_ui`, `load`, `_commit_profile_fields`, `_show_profile`, `_on_provider_changed`, new `_fill_thinking_combo` / `_show_thinking` / `_thinking_value`)
- Modify: `freecad_ai/ui/settings_pages/behavior_page.py` (label text only)
- Test: `tests/unit/test_provider_section.py`

**Interfaces:**
- Consumes: `ProviderConfig.thinking` (Task 1); `get_api_style(provider_name) -> "anthropic" | "openai"` from `freecad_ai.llm.providers`.
- Produces: `ProviderSection.thinking_combo` (editable `QComboBox`); committed profiles carry `thinking` (None / `"default"` / typed text). Task 6 reads `section.current_profile().thinking`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_provider_section.py`:

```python
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
```

(`profiles()` is a method on `ProviderSection`, as in the file's existing tests.)

- [ ] **Step 2: Run to verify failure**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_provider_section.py -q -k Thinking`
Expected: FAIL — `AttributeError: 'ProviderSection' object has no attribute 'thinking_combo'`.

- [ ] **Step 3: Implement**

Module level in `provider_section.py`, next to `_COMPACT_ABOVE_FLOOR`, and add `from ..llm.providers import get_api_style` to the existing providers import:

```python
# Suggestions only (#108): anything typed is saved and sent as-is, so a
# level a vendor adds next year needs no release.
_THINKING_SUGGESTIONS = {
    "anthropic": ("low", "medium", "high", "xhigh", "max"),
    "openai": ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
}
```

In `_build_ui`, right after `provider_layout.addRow(... "Compact above:" ...)`:

```python
        # Thinking for this profile's model (#108). Editable: the list is
        # suggestions, and whatever is typed goes to the vendor verbatim.
        self.thinking_combo = QtWidgets.QComboBox()
        self.thinking_combo.setEditable(True)
        self.thinking_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.thinking_combo.setToolTip(
            translate("SettingsDialog",
                      "Sent to the vendor as-is. Anthropic: a level uses "
                      "adaptive thinking, a number is a token budget.\n"
                      "Use Test Connection to check a value."))
        provider_layout.addRow(translate("SettingsDialog", "Thinking:"),
                               self.thinking_combo)
        self._global_thinking = "off"
        self._fill_thinking_combo("anthropic")
```

New methods (place after `_update_vision_ui`):

```python
    def _fill_thinking_combo(self, api_style: str) -> None:
        """Rebuild the suggestions for an API style, keeping the text."""
        combo = self.thinking_combo
        text = combo.currentText()
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem(translate("SettingsDialog", "Use global"))
            combo.setItemData(
                0, translate("SettingsDialog", "Currently: %s")
                % self._global_thinking, QtCore.Qt.ToolTipRole)
            combo.addItem(translate("SettingsDialog", "Model default"))
            combo.addItem("off")
            for level in _THINKING_SUGGESTIONS.get(
                    api_style, _THINKING_SUGGESTIONS["openai"]):
                combo.addItem(level)
            if text:
                combo.setEditText(text)
        finally:
            combo.blockSignals(False)

    def _show_thinking(self, value) -> None:
        if value is None:
            self.thinking_combo.setCurrentIndex(0)
        elif value == "default":
            self.thinking_combo.setCurrentIndex(1)
        else:
            self.thinking_combo.setEditText(value)

    def _thinking_value(self):
        """The combo's value as stored: None, "default" or the typed text."""
        text = self.thinking_combo.currentText().strip()
        if not text or text == self.thinking_combo.itemText(0):
            return None
        if text == self.thinking_combo.itemText(1):
            return "default"
        return text
```

In `load(self, cfg, label=None)`, before the profile is shown (first line of the method body):

```python
        self._global_thinking = cfg.thinking
```

In `_show_profile`, after the `compact_above_spin` lines:

```python
        self._fill_thinking_combo(get_api_style(prof.name))
        self._show_thinking(prof.thinking)
```

In `_on_provider_changed`, just before `self.presetApplied.emit(preset)`:

```python
        self._fill_thinking_combo(get_api_style(names[index]))
```

In `_commit_profile_fields`, after the `context_window` block:

```python
        if hasattr(self, "thinking_combo"):
            prof.thinking = self._thinking_value()
```

In `behavior_page.py`, change the global combo's label:

```python
        thinking_layout.addWidget(QLabel(
            translate("SettingsDialog", "Thinking (default for profiles):")))
```

- [ ] **Step 4: Run to verify pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_provider_section.py tests/unit/test_profile_selector.py -q`
Expected: PASS. If `test_untouched_load_is_clean` fails, `_show_profile` runs before `_global_thinking` is set or `_commit_profile_fields` sees stale text — check the order in `load`.

- [ ] **Step 5: Commit**

```bash
git add freecad_ai/ui/provider_section.py freecad_ai/ui/settings_pages/behavior_page.py tests/unit/test_provider_section.py
git commit -m "feat(ui): per-profile Thinking combo in the provider section (#108)"
```

---

### Task 6: Test Connection sends the profile's value

**Files:**
- Modify: `freecad_ai/ui/settings_pages/provider_page.py` (`_test_connection`)
- Test: `tests/unit/test_test_connection_config_scope.py`

**Interfaces:**
- Consumes: `section.current_profile().thinking` (Tasks 1, 5).

- [ ] **Step 1: Write the failing test**

First let `_run` take a prepared fake — change its signature and last call:

```python
def _run(monkeypatch, cfg, fake=None):
    ...
    ProviderPage._test_connection(fake or _fake(cfg))
    return captured
```

Then append:

```python
class TestTheProbeSendsTheProfilesThinking:
    """#108: a level the model refuses fails here, not mid-chat."""

    def test_profile_value_wins(self, monkeypatch):
        cfg = _cfg()
        cfg.thinking = "extended"
        fake = _fake(cfg)
        fake.section.current_profile.return_value.thinking = "xhigh"
        assert _run(monkeypatch, cfg, fake)["thinking"] == "xhigh"
```

(`test_thinking_comes_from_the_saved_config` already covers an unset profile falling back to the global value; it must stay green.)

- [ ] **Step 2: Run to verify failure**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_test_connection_config_scope.py -q`
Expected: FAIL — `assert 'extended' == 'xhigh'`.

- [ ] **Step 3: Implement**

In `_test_connection`, change the thread's thinking argument:

```python
            # The profile's own value if it has one (#108), so a level the
            # model refuses shows the vendor's error here, not mid-chat.
            thinking=(profile.thinking if profile.thinking is not None
                      else get_config().thinking),
```

- [ ] **Step 4: Run to verify pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_test_connection_config_scope.py tests/unit/test_probe_status_names_profile.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite, then commit**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py`
Expected: all pass (2253 before this plan, plus the new tests).

```bash
git add freecad_ai/ui/settings_pages/provider_page.py tests/unit/test_test_connection_config_scope.py
git commit -m "feat(ui): Test Connection sends the profile's thinking value (#108)"
```

---

### Task 7: Wiki — "Setting up thinking for a model"

**Files:**
- Modify: `/home/alf/Projects/programming/misc/freecad-ai-wiki/Configuration.md` (LLM Provider Section table, Behavior Section "Thinking" row, Test Connection paragraph, Full Schema, Field Reference, Thinking Mode section)

**Interfaces:** none (docs).

- [ ] **Step 1: Read the affected sections**

Run: `grep -n "^##\|Thinking\|thinking" /home/alf/Projects/programming/misc/freecad-ai-wiki/Configuration.md`, then read lines 23–57 (LLM Provider Section), 147–160, 257–283, 578–705 and 736–end.

- [ ] **Step 2: Edit**

1. LLM Provider Section table — add a row after "Compact above":
   `| **Thinking** | This profile's thinking value, sent to the vendor as typed. *Use global* follows the Behavior page; *Model default* sends no thinking field; the list offers the vendor's usual levels, and any other value can be typed. See Setting up thinking for a model. | Use global |`
2. Behavior Section — rename the row to **Thinking (default for profiles)** and say it applies to profiles left on *Use global*.
3. Test Connection paragraph — replace "Max output tokens and thinking come from the saved settings" with: max output tokens come from the saved settings (or the `max_tokens` row); thinking is the profile's own value, or the saved global one when the profile uses the global.
4. Full Schema — add `"thinking": null` to the example profile object. Field Reference — add
   `| `profiles.<label>.thinking` | string or null | `null` | This profile's thinking value, sent verbatim. `null` = use the global `thinking`. `"default"` = send no thinking field. `"off"`/`"on"`/`"extended"` mean what they mean globally. |`
5. Thinking Mode — update the per-provider list to the current shapes (Anthropic current models: adaptive + effort, no temperature; legacy models: `enabled` + budget; OpenAI-style: `reasoning_effort`), then append this subsection:

```markdown
### Setting up thinking for a model

Each profile can carry its own thinking value (Settings → Provider → **Thinking**). The workbench sends it to the vendor exactly as typed and does not check it — the vendor does, and its error message lists what it accepts.

| Value | Anthropic | OpenAI-style (incl. Ollama) |
|---|---|---|
| *Use global* | the Behavior page's setting | the Behavior page's setting |
| *Model default* | no thinking field | no `reasoning_effort` |
| `off` | same as the global Off | same as the global Off |
| a word (`low`, `xhigh`, `none` …) | adaptive thinking, `effort: <word>` | `reasoning_effort: <word>`, also in Act mode |
| a number (`8000`) | `budget_tokens: 8000` | sent as typed (vendors reject it) |

A good way to tune a model:

1. **Take the profile out of the fallback list** while testing, so a rejected value fails visibly instead of silently falling through to the next profile.
2. Start with **Model default** and press **Test Connection**.
3. Try levels one at a time with Test Connection; it sends the profile's value.
4. **Raise the profile's `max_tokens` row.** Reasoning counts against the output cap; a small cap gives empty answers (qwen3:8b at `low` used all 8000 tokens on reasoning).
5. Put the profile back in the fallback list.

Known quirks (probed September 2026):

- gpt-oss on Ollama still reasons at `none`.
- Claude Haiku 4.5 rejects levels — give it a budget number.
- Claude Sonnet 5 and Opus 5.5 reject budget numbers — give them a level.
```

- [ ] **Step 3: Commit locally (do not push)**

```bash
cd /home/alf/Projects/programming/misc/freecad-ai-wiki
git add Configuration.md
git commit -m "docs: per-profile thinking and how to set it up (#108)"
```

---

## Self-review notes

- Spec §1 Storage → Task 1; §2 Resolution → Task 4; §3 table → Tasks 2 and 3; §4 UI → Task 5; §5 Test Connection → Task 6; Docs → Task 7; CHANGELOG is at release, not here.
- `_thinking_kind` defined in Task 2, consumed in Task 3; `ProviderConfig.thinking` defined in Task 1, consumed in Tasks 4–6; `thinking_combo` defined in Task 5 only.
- Golden fixtures and `test_anthropic_current_models.py` are the byte-identity guard for off/on/extended (Tasks 2, 3).
