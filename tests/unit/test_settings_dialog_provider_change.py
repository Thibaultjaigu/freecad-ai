"""Regression tests for a provider switch: ProviderSection._on_provider_changed
and the dialog's half of it, ProviderPage._on_preset_applied (#99, #101).

Issue #12 (xtc0r): switching the provider combo to "custom" was wiping the
user's gateway URL and model, because the "custom" preset ships empty
strings and the dialog applied them unconditionally. After v0.14.3 the
dialog only overwrites a field when the preset has a concrete value.

These tests exercise the methods via the unbound-method-with-fake-self
pattern — no QApplication required.
"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

# settings_dialog imports through ui/compat.py which needs PySide6 or PySide2.
# In dev venvs without either, skip the entire module — the dialog can't be
# imported. Inside FreeCAD it's always available.
try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.config import (  # noqa: E402
    AppConfig,
    PROVIDER_PRESETS,
    ProviderConfig,
)
from freecad_ai.llm.providers import get_provider_names  # noqa: E402
from freecad_ai.ui.provider_section import ProviderSection  # noqa: E402
from freecad_ai.ui.settings_pages.provider_page import ProviderPage  # noqa: E402


class _Sig:
    """Stand-in for a Qt signal on a fake self."""
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


def _make_fake_section(base_url="http://gateway.example/v1", model="my-model"):
    """Build a fake section with just the attributes _on_provider_changed touches."""
    base_url_edit = MagicMock()
    base_url_edit.text.return_value = base_url
    model_edit = MagicMock()
    model_edit.text.return_value = model
    provider_combo = MagicMock()
    provider_combo.count.return_value = len(get_provider_names())
    provider_combo.removeItem = MagicMock()
    fake = SimpleNamespace(
        base_url_edit=base_url_edit,
        model_edit=model_edit,
        provider_combo=provider_combo,
        profileShown=_Sig(),
        aboutToCommit=_Sig(),
        presetApplied=_Sig(),
        _commit_profile_fields=MagicMock(),
        _current_profile_label="p",
    )
    fake._unknown_item_index = lambda: ProviderSection._unknown_item_index(fake)
    fake._drop_unknown_item = lambda: ProviderSection._drop_unknown_item(fake)
    return fake


def _make_fake_dialog(model="my-model", profile=None):
    """Build a fake dialog with just the attributes _on_preset_applied touches."""
    section = MagicMock()
    section.model_edit.text.return_value = model
    section.current_profile.return_value = profile
    return SimpleNamespace(
        section=section,
        _cfg=AppConfig(),
        _load_model_params_table=MagicMock(),
    )


def test_switch_to_custom_preserves_fields():
    """Custom preset has empty base_url/default_model — must NOT overwrite."""
    assert PROVIDER_PRESETS["custom"]["base_url"] == ""
    assert PROVIDER_PRESETS["custom"]["default_model"] == ""

    fake = _make_fake_section()
    custom_idx = get_provider_names().index("custom")
    ProviderSection._on_provider_changed(cast(ProviderSection, fake), custom_idx)

    fake.base_url_edit.setText.assert_not_called()
    fake.model_edit.setText.assert_not_called()
    assert fake.presetApplied.calls == [(PROVIDER_PRESETS["custom"],)]


def test_preset_applied_reloads_the_table_for_the_field_model():
    """_load_model_params_table is called with whatever's in the field, not ""."""
    fake = _make_fake_dialog(model="my-model")
    ProviderPage._on_preset_applied(
        cast(ProviderPage, fake), PROVIDER_PRESETS["custom"])

    fake._load_model_params_table.assert_called_once()
    args, _ = fake._load_model_params_table.call_args
    assert args[0] == "my-model"


def test_switch_to_real_provider_applies_preset():
    """Anthropic (or any non-custom provider) overwrites fields as before."""
    fake = _make_fake_section()
    anthropic_idx = get_provider_names().index("anthropic")
    ProviderSection._on_provider_changed(cast(ProviderSection, fake), anthropic_idx)

    fake.base_url_edit.setText.assert_called_once_with(
        PROVIDER_PRESETS["anthropic"]["base_url"])
    fake.model_edit.setText.assert_called_once_with(
        PROVIDER_PRESETS["anthropic"]["default_model"])


def test_invalid_index_is_noop():
    """Out-of-range index leaves all widgets untouched, and the dialog is
    never told to reload its table."""
    fake = _make_fake_section()
    ProviderSection._on_provider_changed(cast(ProviderSection, fake), -1)
    ProviderSection._on_provider_changed(cast(ProviderSection, fake), 9999)
    fake.base_url_edit.setText.assert_not_called()
    fake.model_edit.setText.assert_not_called()
    assert fake.presetApplied.calls == []
    fake._commit_profile_fields.assert_not_called()


def test_params_table_reload_gets_the_working_copy_profile():
    """A vendor switch must keep the profile's own parameters. Passing
    the working-copy profile (never the get_config() singleton, and never
    None) is what makes the new preset's default_params a fallback rather
    than an override."""
    profile = ProviderConfig(name="ollama", model="qwen3:8b",
                             params={"top_k": 40})
    fake = _make_fake_dialog(profile=profile)
    ProviderPage._on_preset_applied(
        cast(ProviderPage, fake), PROVIDER_PRESETS["anthropic"])

    args, kwargs = fake._load_model_params_table.call_args
    assert args[1] is fake._cfg
    assert args[2] is profile
