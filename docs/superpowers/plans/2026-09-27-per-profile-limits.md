# Per-profile Output Cap and Compaction Threshold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each connection profile can carry its own output cap (a `max_tokens` row in its Model Parameters table) and its own compaction threshold ("Compact above"), with the Behavior page values as the fallback.

**Architecture:** `create_client` resolves the output cap once (call site > valid profile row > `cfg.max_tokens`) and strips the row from the params it hands to `LLMClient`. A new `ProviderConfig.context_window` field (None = global) is read through one helper, `compaction_threshold(cfg)`, which always looks at the *active* profile. `ProviderSection` gains a spin box for the new field; the truncation warning and Test Connection stop reading `cfg.max_tokens` directly.

**Tech Stack:** Python 3.11, PySide6/PySide2 via `freecad_ai/ui/compat.py`, pytest with offscreen Qt.

**Spec:** `docs/superpowers/specs/2026-09-27-per-profile-limits-design.md`

## Global Constraints

- Test command: `env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py` (the full run takes ~130 s; the shell's `PYTHONPATH` breaks pluggy, and `test_document_attach.py` segfaults under Qt even on clean master).
- Qt imports only through `freecad_ai/ui/compat.py`; never hard-import PySide2. Flat enum forms only.
- Config key `context_window` is unchanged; nothing is migrated. Provider presets' `default_params` gain **no** `max_tokens`.
- `max_tokens` **stays on `_RESERVED`** in both request builders (`freecad_ai/llm/client.py` ~line 423).
- Valid `max_tokens` row: an `int` > 0 that is not a `bool`, or a `float` > 0 with no fractional part (`8192.0` → 8192). Warning text, exactly: `max_tokens row "abc" in profile "local" ignored — not a positive integer`.
- "Compact above" spin: range 0–1,000,000, single step 10000, `setSpecialValueText("Use global")`. Commit only when the value differs from what was shown; then 0 → `None`, 1–3999 → 4000, otherwise as typed.
- New truncation text, exactly: `Response was cut off at the output limit ({max_tokens} tokens). Raise Max Output Tokens in Settings → Behavior, or this profile's max_tokens row, or ask the model to continue.`
- Every commit ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Work on branch `feat/103-per-profile-limits`, never on master.
- Run the full suite before every commit.
- Never touch the maintainer's real FreeCAD or FreeCAD AI config. Live probes use an isolated HOME / XDG / `FREECAD_AI_CONFIG_DIR` under the scratchpad.

## Review Focus

1. **A `max_tokens` row that a user typed as text such as "8k".** The table auto-detects numbers, so "8k" stays a string. It must fall back to the global cap and log the warning, never 400 at the vendor. Pinned in Task 2 (`"abc"` case) and Task 5 (the Test Connection path).
2. **Reverting a "Compact above" edit.** Typing 80000 over a hand-edited 3000 and then typing 3000 back must leave the page clean, not dirty with a clamped 4000. Pinned in Task 4 (`test_a_reverted_edit_is_clean`).
3. **A profile switch with an edit pending.** Typing a value on one profile and then switching must keep the edit on that profile and show the other profile's own value. Pinned in Task 4.
4. **Compaction utility routed to another profile.** The threshold must still come from the chat profile. Pinned in Task 1, where the two profiles carry different values.
5. **Test Connection with a row present.** The probe must not send the row inside `model_params`, where it would be dropped anyway because `max_tokens` is reserved. It must use the row as the cap. Pinned in Task 5.

---

### Task 1: Profile field, load validation, and compaction threshold

**Files:**
- Modify: `freecad_ai/config.py` (`ProviderConfig` ~line 377, `_profile_from_dict` ~line 409; add `compaction_threshold` right after `_profile_from_dict`)
- Modify: `freecad_ai/ui/chat_widget.py:1495`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `ProviderConfig.context_window: int | None = None`; `compaction_threshold(cfg) -> int` in `freecad_ai.config`.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_config.py`:

```python
class TestProfileContextWindow:
    """#103: a profile may carry its own compaction threshold."""

    def test_defaults_to_none(self):
        assert ProviderConfig().context_window is None

    def test_an_old_profile_without_the_key_loads_none(self):
        from freecad_ai.config import _profile_from_dict
        assert _profile_from_dict({"name": "ollama"}).context_window is None

    def test_a_small_hand_edit_is_kept(self):
        from freecad_ai.config import _profile_from_dict
        assert _profile_from_dict({"context_window": 3000}).context_window == 3000

    @pytest.mark.parametrize("bad", ["x", 0, -1, True, 2.5])
    def test_bad_values_load_as_none_with_a_warning(self, bad, caplog):
        from freecad_ai.config import _profile_from_dict
        assert _profile_from_dict({"context_window": bad}).context_window is None
        assert "context_window" in caplog.text

    def test_null_and_3000_survive_a_save(self, tmp_config_dir):
        c = AppConfig()
        c.profiles = {"a": ProviderConfig(context_window=None),
                      "b": ProviderConfig(context_window=3000)}
        c.active_profile = "a"
        save_config(c)
        with open(config_mod.CONFIG_FILE) as f:
            raw = json.load(f)
        assert raw["profiles"]["a"]["context_window"] is None
        loaded = load_config()
        assert loaded.profiles["a"].context_window is None
        assert loaded.profiles["b"].context_window == 3000


class TestCompactionThreshold:
    def _cfg(self):
        c = AppConfig()
        c.context_window = 20000
        c.profiles = {"chat": ProviderConfig(context_window=150000),
                      "cheap": ProviderConfig(context_window=8000)}
        c.active_profile = "chat"
        c.utility_profiles = {"compaction": "cheap"}
        return c

    def test_comes_from_the_active_profile_not_the_compaction_one(self):
        from freecad_ai.config import compaction_threshold
        assert compaction_threshold(self._cfg()) == 150000

    def test_none_falls_back_to_the_global_value(self):
        from freecad_ai.config import compaction_threshold
        c = self._cfg()
        c.profiles["chat"].context_window = None
        assert compaction_threshold(c) == 20000
```

- [ ] **Step 2: Run to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_config.py -q -k "ContextWindow or CompactionThreshold"`
Expected: FAIL — `ProviderConfig.__init__() got an unexpected keyword argument 'context_window'` / `cannot import name 'compaction_threshold'`.

- [ ] **Step 3: Implement.** In `ProviderConfig`, after `thinking_detected`:

```python
    # Compaction threshold for conversations on this profile, in tokens.
    # Client-side only — never sent to a vendor. None = use the global
    # AppConfig.context_window (#103).
    context_window: int | None = None
```

In `_profile_from_dict`, replace the final `return` with:

```python
    kept = {k: v for k, v in raw.items() if k in known}
    cw = kept.get("context_window")
    if cw is not None and (isinstance(cw, bool) or not isinstance(cw, int)
                           or cw <= 0):
        logger.warning(
            "Profile context_window %s is not a positive integer — using "
            "the global value instead.", json.dumps(cw, default=str))
        kept["context_window"] = None
    return ProviderConfig(**kept)
```

After `_profile_from_dict`, add:

```python
def compaction_threshold(cfg) -> int:
    """Token count above which the conversation is compacted.

    Read from the *active chat* profile, never from the compaction
    utility's profile: the question is whether this conversation is too
    big for the model being talked to. Who writes the summary is a
    separate setting (#103).
    """
    own = cfg.provider.context_window
    return own if own else cfg.context_window
```

In `freecad_ai/ui/chat_widget.py` at line ~1495, replace `if self.conversation.needs_compaction(cfg.context_window):` with:

```python
        if self.conversation.needs_compaction(compaction_threshold(cfg)):
```

and add `compaction_threshold` to the module-level `from ..config import (LOGS_DIR, add_config_listener, get_config, ...)` block at `chat_widget.py:37`.

- [ ] **Step 4: Run to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite, then commit**

```bash
env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py
git add freecad_ai/config.py freecad_ai/ui/chat_widget.py tests/unit/test_config.py
git commit -m "feat(config): per-profile compaction threshold (#103)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Output cap resolution in `create_client`

**Files:**
- Modify: `freecad_ai/llm/client.py` (add two helpers before `create_client` ~line 1131; change `create_client`)
- Test: `tests/unit/test_create_client.py`

**Interfaces:**
- Produces: `take_max_tokens_row(params: dict, label: str) -> int | None` in `freecad_ai.llm.client`. It **pops** `"max_tokens"` from `params` in place and returns the valid cap or None, logging the warning for an invalid value. Task 5 uses it.

- [ ] **Step 1: Write the failing tests.** Add `import logging` and `import pytest` to the top of `tests/unit/test_create_client.py`, and add `take_max_tokens_row` to its existing `from freecad_ai.llm.client import (...)` block. Then append:

```python
class TestOutputCapResolution:
    """#103: call site > profile max_tokens row > cfg.max_tokens."""

    def _cfg(self, row):
        cfg = _cfg()
        cfg.max_tokens = 4096
        cfg.profiles["local"].params = {"top_k": 40, "max_tokens": row}
        cfg.active_profile = "local"
        return cfg

    def test_the_row_beats_the_global(self):
        assert create_client(self._cfg(16000)).max_tokens == 16000

    def test_the_call_site_beats_the_row(self):
        """The reranker's 1024 must survive a max_tokens: 32000 row."""
        client = create_client(self._cfg(32000), "rerank", max_tokens=1024)
        assert client.max_tokens == 1024

    def test_no_row_uses_the_global(self):
        assert create_client(_cfg()).max_tokens == AppConfig().max_tokens

    def test_the_row_is_not_sent_as_a_param(self):
        client = create_client(self._cfg(16000))
        assert "max_tokens" not in client.model_params
        assert client.model_params == {"top_k": 40}

    def test_the_profile_keeps_its_row(self):
        cfg = self._cfg(16000)
        create_client(cfg)
        assert cfg.profiles["local"].params["max_tokens"] == 16000

    def test_an_integral_float_is_accepted(self):
        assert create_client(self._cfg(8192.0)).max_tokens == 8192

    @pytest.mark.parametrize("bad", ["abc", 0, -5, True, 8192.5])
    def test_bad_rows_fall_back_with_a_warning(self, bad, caplog):
        caplog.set_level(logging.WARNING)
        client = create_client(self._cfg(bad))
        assert client.max_tokens == 4096
        assert "max_tokens" not in client.model_params
        assert 'in profile "local" ignored — not a positive integer' in caplog.text

    def test_the_warning_quotes_a_string_value(self, caplog):
        caplog.set_level(logging.WARNING)
        take_max_tokens_row({"max_tokens": "abc"}, "local")
        assert ('max_tokens row "abc" in profile "local" ignored'
                ' — not a positive integer') in caplog.text

    def test_the_utility_profile_row_applies_to_that_utility(self):
        cfg = _cfg()
        cfg.profiles["local"].params = {"max_tokens": 2000}
        cfg.utility_profiles["compaction"] = "local"
        assert create_client(cfg, "compaction").max_tokens == 2000
        assert create_client(cfg).max_tokens == cfg.max_tokens
```

- [ ] **Step 2: Run to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_create_client.py -q`
Expected: FAIL — `cannot import name 'take_max_tokens_row'`.

- [ ] **Step 3: Implement.** Before `def create_client`, add:

```python
def take_max_tokens_row(params: dict, label: str) -> int | None:
    """Pop a profile's ``max_tokens`` params row; return it if usable.

    The row is the per-profile output cap (#103). It never travels as a
    request param — ``max_tokens`` is reserved in both request builders —
    so it is removed from ``params`` whatever its value. A value that is
    not a positive integer falls back to the caller's default, with a
    warning: silently ignoring it would leave the user no way to tell why
    their row did nothing.
    """
    if "max_tokens" not in params:
        return None
    raw = params.pop("max_tokens")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    if isinstance(raw, float) and raw.is_integer() and raw > 0:
        return int(raw)
    logger.warning(
        'max_tokens row %s in profile "%s" ignored — not a positive integer',
        json.dumps(raw, default=str), label)
    return None


def _profile_label(cfg, profile) -> str:
    """The key ``profile`` is stored under in ``cfg.profiles``."""
    for label, candidate in cfg.profiles.items():
        if candidate is profile:
            return label
    return cfg.active_profile
```

In `create_client`, update the docstring's second paragraph and the resolution:

```python
    """Build an LLMClient for one call site.

    Connection settings (vendor, url, key, model, params) come from the
    resolved profile. Job settings (max_tokens, temperature, thinking)
    come from the config unless the call site overrides them — the
    reranker wants 1024 tokens and no thinking whichever profile it runs
    on. A valid ``max_tokens`` row in the profile's params sits between
    the two: it beats the config, never a call-site override (#103).
    ...
    """
    from ..config import get_config
    if cfg is None:
        cfg = get_config()
    profile = resolve_profile(cfg, utility)

    params = resolve_params(cfg, profile)
    row_cap = take_max_tokens_row(params, _profile_label(cfg, profile))
    if max_tokens is None:
        max_tokens = row_cap if row_cap is not None else cfg.max_tokens
```

Keep the remaining docstring paragraph (the one about `api_key`) unchanged. Change the constructor argument to `max_tokens=max_tokens,`.

- [ ] **Step 4: Run to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_create_client.py -q`
Expected: PASS, and this includes the existing `TestCreateClientFromConfigIsUnchanged`.

- [ ] **Step 5: Full suite, then commit**

```bash
env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py
git add freecad_ai/llm/client.py tests/unit/test_create_client.py
git commit -m "feat(llm): a profile's max_tokens row sets its output cap (#103)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Truncation warning names the cap that applied

**Files:**
- Modify: `freecad_ai/ui/chat_widget.py` — `_LLMWorker.__init__` (~line 224), `_LLMWorker.run` (~line 236), `_on_response_finished` (~line 2350)
- Modify: `freecad_ai/ui/message_view.py:316` (`render_truncation_warning` text)
- Test: `tests/unit/test_act_truncation.py`

**Interfaces:**
- Consumes: `LLMClient.max_tokens` (resolved by Task 2).
- Produces: `_LLMWorker._response_max_tokens: int | None`, set in `run()` from the client.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_act_truncation.py`:

```python
class TestTruncationWarningNamesTheAppliedCap:
    """#103: the cap can come from a profile row, so the warning must quote
    the client that ran, not cfg.max_tokens."""

    def test_run_records_the_clients_cap(self, monkeypatch, tmp_config_dir):
        import freecad_ai.llm.client as client_mod
        from freecad_ai.ui.chat_widget import _LLMWorker
        fake_client = SimpleNamespace(model="qwen3:8b", max_tokens=16000)
        monkeypatch.setattr(client_mod, "create_client_from_config",
                            lambda **k: fake_client)
        worker = SimpleNamespace(
            conversation=None, describe_fn=None, tools=[],
            _simple_stream=lambda c: None, _tool_loop=lambda c: None,
            error_occurred=MagicMock(), _response_max_tokens=None)
        _LLMWorker.run(worker)  # type: ignore[arg-type]
        worker.error_occurred.emit.assert_not_called()
        assert worker._response_max_tokens == 16000

    def test_the_warning_points_at_behavior_and_the_row(self):
        from freecad_ai.ui.message_view import render_truncation_warning
        html = render_truncation_warning(16000)
        assert "16000 tokens" in html
        assert "Settings → Behavior" in html
        assert "max_tokens row" in html
        assert "Model Parameters" not in html
```

- [ ] **Step 2: Run to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_act_truncation.py -q -k AppliedCap`
Expected: FAIL. `_response_max_tokens` is still None, and the old text still says "Model Parameters".

- [ ] **Step 3: Implement.** In `_LLMWorker.__init__`, after `self._response_truncated = False ...`:

```python
        self._response_max_tokens = None  # the cap the client ran with (#103)
```

In `run()`, directly after `client = create_client_from_config(...)`:

```python
            # The truncation warning quotes this: the cap may come from a
            # profile row, and cfg can change while the turn runs (#103).
            self._response_max_tokens = client.max_tokens
```

In `_on_response_finished`, replace `self._append_html(render_truncation_warning(get_config().max_tokens))` with:

```python
            self._append_html(render_truncation_warning(
                self._worker._response_max_tokens or get_config().max_tokens))
```

In `message_view.py`, replace the message string in `render_truncation_warning`:

```python
    msg = translate(
        "MessageView",
        "Response was cut off at the output limit ({max_tokens} tokens). "
        "Raise Max Output Tokens in Settings → Behavior, "
        "or this profile's max_tokens row, "
        "or ask the model to continue."
    ).replace("{max_tokens}", str(max_tokens))
```

- [ ] **Step 4: Run to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_act_truncation.py tests/unit/test_reasoning_on_text_only_turns.py -q`
Expected: PASS. If an existing test asserts the old "Model Parameters" text, update it to the new text; it asserts copy, not behavior.

- [ ] **Step 5: Full suite, then commit**

```bash
env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py
git add freecad_ai/ui/chat_widget.py freecad_ai/ui/message_view.py tests/unit/test_act_truncation.py
git commit -m "fix(chat): truncation warning quotes the cap that applied (#103)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: "Compact above" in `ProviderSection`

**Files:**
- Modify: `freecad_ai/ui/provider_section.py`:
  - the Qt aliases (~line 21-34);
  - a module constant;
  - the widget after the Vision row (~line 194);
  - `_show_profile` (~line 626);
  - `_commit_profile_fields` (~line 573).
- Test: `tests/unit/test_provider_section.py`

**Interfaces:**
- Consumes: `ProviderConfig.context_window` (Task 1).
- Produces: `ProviderSection.compact_above_spin: QSpinBox`.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_provider_section.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_provider_section.py -q -k CompactAbove`
Expected: FAIL — `'ProviderSection' object has no attribute 'compact_above_spin'`.

- [ ] **Step 3: Implement.** Add to the Qt aliases block:

```python
QSpinBox = QtWidgets.QSpinBox
```

Below the aliases, add a module constant:

```python
# The Behavior page's "Compact above" spin starts here too. Applied only to
# values typed on this page, so a smaller hand edit in config.json survives.
_COMPACT_ABOVE_FLOOR = 4000
```

After `provider_layout.addRow(translate("SettingsDialog", "Vision:"), vision_layout)`:

```python
        # Compaction threshold for this profile's model (#103). A profile
        # field, not a params row: it is client-side only and must never
        # reach a vendor, where a thin proxy may 400 on an unknown key.
        self.compact_above_spin = QSpinBox()
        self.compact_above_spin.setRange(0, 1000000)
        self.compact_above_spin.setSingleStep(10000)
        self.compact_above_spin.setSpecialValueText(
            translate("SettingsDialog", "Use global"))
        self.compact_above_spin.setToolTip(
            translate("SettingsDialog",
                      "Compact older messages once the conversation is "
                      "estimated above this many tokens.\n"
                      "\"Use global\" takes the value from Behavior → Limits."))
        provider_layout.addRow(translate("SettingsDialog", "Compact above:"),
                               self.compact_above_spin)
```

In `_show_profile`, after `self._update_vision_ui(prof)`:

```python
        # Remember what was shown: _commit_profile_fields writes the spin
        # back only when the user changed it, so an untouched hand edit
        # (3000, below the typed floor) is never re-clamped.
        self._compact_shown_value = prof.context_window
        self.compact_above_spin.setValue(prof.context_window or 0)
        self._compact_shown = self.compact_above_spin.value()
```

In `_commit_profile_fields`, before `self.aboutToCommit.emit(prof)`:

```python
        if hasattr(self, "_compact_shown"):
            typed = self.compact_above_spin.value()
            if typed == self._compact_shown:
                # Unchanged, or typed back: restore exactly what was loaded.
                prof.context_window = self._compact_shown_value
            elif typed == 0:
                prof.context_window = None
            else:
                prof.context_window = max(typed, _COMPACT_ABOVE_FLOOR)
```

- [ ] **Step 4: Run to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_provider_section.py tests/unit/test_provider_page.py tests/unit/test_prefs_page.py -q`
Expected: PASS. `TestRoundTrip.test_load_then_apply_is_lossless` must still pass, which proves that `context_window=None` round-trips.

- [ ] **Step 5: Full suite, then commit**

```bash
env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py
git add freecad_ai/ui/provider_section.py tests/unit/test_provider_section.py
git commit -m "feat(settings): per-profile Compact above field (#103)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Provider page — params table copy, Test Connection cap, `apply_to` guard

**Files:**
- Modify: `freecad_ai/ui/settings_pages/provider_page.py`:
  - Model Parameters label and tooltip (~line 237-258);
  - `_test_connection` (~line 560-600).
- Test: `tests/unit/test_provider_page.py`

**Interfaces:**
- Consumes: `take_max_tokens_row(params, label)` from `freecad_ai.llm.client` (Task 2).

- [ ] **Step 1: Write the failing tests.** Append these methods inside `class TestConnectionProbe` in `tests/unit/test_provider_page.py`:

```python
    def test_a_max_tokens_row_is_the_probe_cap(
            self, page, monkeypatch, tmp_config_dir):
        made = self._capture(monkeypatch)
        page.load(_cfg())
        page._populate_model_params_table(
            {"temperature": 0.2, "max_tokens": 16000})
        page._test_connection()
        assert made["kwargs"]["max_tokens"] == 16000
        assert "max_tokens" not in made["args"][4]     # model_params

    def test_a_bad_row_falls_back_to_the_saved_global(
            self, page, monkeypatch, tmp_config_dir):
        import freecad_ai.config as config_mod
        config_mod.get_config().max_tokens = 12345
        made = self._capture(monkeypatch)
        page.load(_cfg())
        page._populate_model_params_table({"max_tokens": "8k"})
        page._test_connection()
        assert made["kwargs"]["max_tokens"] == 12345
        assert "max_tokens" not in made["args"][4]
```

Append at module level:

```python
def test_the_params_label_says_per_profile(page):
    texts = [w.text() for w in page.findChildren(QtWidgets.QLabel)]
    assert "Parameters sent with each request (saved per profile):" in texts


def test_the_params_tooltip_mentions_max_tokens(page):
    assert "max_tokens" in page.model_params_table.toolTip()


def test_a_max_tokens_row_never_writes_the_global(page):
    page.load(_cfg())
    page._populate_model_params_table(
        {"temperature": 0.2, "max_tokens": 16000})
    target = _cfg()
    page.apply_to(target)
    assert target.max_tokens == AppConfig().max_tokens
    assert target.profiles["cloud"].params["max_tokens"] == 16000
```

- [ ] **Step 2: Run to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_provider_page.py -q`
Expected: FAIL. The probe still sends the row inside `model_params` with `max_tokens` taken from config, and the label and tooltip still have the old text. `test_a_max_tokens_row_never_writes_the_global` may already pass; it is a guard against someone copying the temperature pattern in `apply_to`.

- [ ] **Step 3: Implement.** Replace the label text:

```python
        # Freeform request parameters, saved per profile since #99. A
        # max_tokens row is this profile's output cap (#103).
        model_params_layout.addWidget(QLabel(
            translate("SettingsDialog",
                      "Parameters sent with each request (saved per profile):")
        ))
```

Replace the tooltip text:

```python
        self.model_params_table.setToolTip(
            translate("SettingsDialog",
                      "Parameters are merged into the API request body.\n"
                      "Common: temperature, top_p, top_k, n, max_tokens,\n"
                      "presence_penalty, frequency_penalty, repetition_penalty.\n"
                      "max_tokens overrides Max Output Tokens for this profile.\n"
                      "Values are auto-detected as number or string.")
        )
```

In `_test_connection`, replace `model_params = dict(profile.params)` with:

```python
        model_params = dict(profile.params)
        # Same rule as create_client (#103): a valid row is the cap, and the
        # row itself never travels as a param.
        row_cap = take_max_tokens_row(model_params, section.current_label())
```

In the `_TestConnectionThread(...)` call, replace `max_tokens=get_config().max_tokens,` with:

```python
            max_tokens=row_cap if row_cap is not None else get_config().max_tokens,
```

Add the import at module level, after `from ...config import get_config, PROVIDER_PRESETS` (line 6): `from ...llm.client import take_max_tokens_row`.

- [ ] **Step 4: Run to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_provider_page.py tests/unit/test_test_connection_config_scope.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite, then commit**

```bash
env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py
git add freecad_ai/ui/settings_pages/provider_page.py tests/unit/test_provider_page.py
git commit -m "feat(settings): Test Connection honours a max_tokens row (#103)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Behavior page — relabel and tooltips

**Files:**
- Modify: `freecad_ai/ui/settings_pages/behavior_page.py:41-64`
- Test: `tests/unit/test_behavior_page.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_behavior_page.py`. It already has a `page(qapp, tmp_config_dir)` fixture:

```python
def _row_label(widget):
    form = widget.parentWidget().layout()
    return form.labelForField(widget).text()


def test_context_window_is_labelled_compact_above(page):
    assert _row_label(page.context_window_spin) == "Compact above:"
    tip = page.context_window_spin.toolTip()
    assert "compacted" in tip and "Provider page" in tip


def test_max_output_tokens_tooltip_mentions_the_row(page):
    assert _row_label(page.max_tokens_spin) == "Max Output Tokens:"
    tip = page.max_tokens_spin.toolTip()
    assert "max_tokens row" in tip
    assert "determined by the model" not in tip
```

The group box is the parent of each spin: `limits_group.setLayout(fixed_layout)`. If `parentWidget().layout()` is not the `QFormLayout`, walk to it with `page.findChildren(QtWidgets.QFormLayout)` and pick the layout whose `labelForField(widget)` is not None.

- [ ] **Step 2: Run to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_behavior_page.py -q`
Expected: FAIL — the label is still `"Context Window:"`.

- [ ] **Step 3: Implement.** Replace the two tooltips and the label:

```python
        self.max_tokens_spin.setToolTip(
            translate("SettingsDialog",
                      "Default output cap per response.\n"
                      "A profile can set its own with a max_tokens row\n"
                      "in its Model Parameters table.")
        )
```

```python
        self.context_window_spin.setToolTip(
            translate("SettingsDialog",
                      "Older messages are compacted once the conversation\n"
                      "is estimated above this many tokens.\n"
                      "Default for profiles that don't set their own\n"
                      "on the Provider page.")
        )
        fixed_layout.addRow(translate("SettingsDialog", "Compact above:"), self.context_window_spin)
```

The attribute name `context_window_spin` and the config key stay unchanged.

- [ ] **Step 4: Run to verify they pass**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_behavior_page.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite, then commit**

```bash
env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py
git add freecad_ai/ui/settings_pages/behavior_page.py tests/unit/test_behavior_page.py
git commit -m "feat(settings): relabel Context Window as Compact above (#103)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Docs and live probe

**Files:**
- Modify: `CHANGELOG.md` (`## [Unreleased]`, `### Added` / `### Changed`)
- Modify: `README.md`, in the profiles section around line 180-200
- Modify (wiki repo, commit locally, **do not push**): `/home/alf/Projects/programming/misc/freecad-ai-wiki/Configuration.md`, lines ~108-109, ~554, ~628, plus the profile field table

- [ ] **Step 1: CHANGELOG.** Under `### Added` in `[Unreleased]`:

```markdown
- **Per-profile output cap and compaction threshold (#103).** A profile can
  now carry its own limits, so switching from a cloud model with a 200k window
  to a local 8k model no longer keeps the cloud model's numbers. Put a
  `max_tokens` row in a profile's Model Parameters table to set its output
  cap — it beats Max Output Tokens but never a job's own cap (the reranker
  keeps its 1024). A row that is not a positive integer is ignored with a
  warning in the Report view. The new **Compact above** field under Vision
  sets the profile's compaction threshold; **Use global** takes the Behavior
  page's value. The threshold always comes from the chat profile, even when
  compaction runs on another one. Existing configs are unchanged: no profile
  has either value until you set one.
```

Under `### Changed` (create the heading if `[Unreleased]` has none):

```markdown
- **"Context Window" is now labelled "Compact above" (#103).** It always was
  a compaction threshold, not the model's window; the config key is still
  `context_window` and existing values keep their meaning.
- **The truncation warning names the cap that actually applied (#103)** and
  points at Settings → Behavior or the profile's `max_tokens` row, instead of
  the Model Parameters group the field left in #101.
```

- [ ] **Step 2: README.** After the paragraph ending "…never touches the ones you're not looking at." in the profiles section, add:

```markdown
A profile can also carry its own limits: a `max_tokens` row in its Model
Parameters table sets its output cap, and **Compact above** sets how large a
conversation on it may grow before older messages are summarised. Both fall
back to the Behavior page's values when left unset.
```

- [ ] **Step 3: Wiki** (`freecad-ai-wiki/Configuration.md`):
  - Row "Context Window" → "Compact above". Description: "Older messages are compacted once the conversation is estimated above this many tokens. Default for profiles that don't set their own on the Provider page. Config key `context_window`." Keep the range and default.
  - "Max Output Tokens" description: add "A profile's `max_tokens` params row overrides it for that profile."
  - The JSON example at ~line 554 already shows `"max_tokens": 8192` inside `params`. Before #103 that row was silently dropped; it now works. Add a sentence under the example saying so, and add `"context_window": null` to that profile in the example.
  - The `max_tokens` key row (~628): add the same override note.
  - Add `context_window` to the profile-field table if one exists (grep `vision_override` to find it): `integer or null`, default `null`, "Compaction threshold for this profile; null = the global `context_window`."
  - Commit in the wiki repo with message `docs(config): per-profile output cap and Compact above (#103)` plus the trailer. **Do not push**: the wiki is pushed at the next release, together with `febb41e`/`996baa9`.

- [ ] **Step 4: Live probe** (spec Testing item 9). Use the #101 scaffolding at `/tmp/claude-1000/-home-alf-Projects-programming-misc-freecad-ai/5b27868e-3e5d-421e-864a-80be2593ec4a/scratchpad/probe99/run.sh`. It runs the FreeCAD 1.1.1 AppImage under `xvfb-run` with an isolated HOME / XDG / `FREECAD_AI_CONFIG_DIR`. The probe script must:
  1. Seed an isolated `config.json` with two profiles, one of them with `"context_window": 3000`.
  2. Open Edit → Preferences on the FreeCAD AI provider page programmatically, press OK untouched, and assert the config file's sha256 is unchanged.
  3. Reopen, set "Compact above" to 150000 on the other profile, press OK, and assert the JSON holds `150000` there and still holds `3000` on the first profile.
  4. Write the results to a file (FreeCAD's stdout goes to its own console) and `exit(0)`.

  Record the output lines verbatim in the task report.

- [ ] **Step 5: Full suite, then commit** (main repo only)

```bash
env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py
git add CHANGELOG.md README.md
git commit -m "docs: per-profile output cap and Compact above (#103)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

Translations (`translations/update_translations.sh`) run at release time, not here.
