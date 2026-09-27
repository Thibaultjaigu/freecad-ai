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


def test_a_provider_switch_applies_default_rerank_on_save(dialog, cfg, monkeypatch):
    """#10 at save time: the widgets don't flip before OK any more (Decision
    3's accepted cost) — only get_config() after a real save shows it."""
    import freecad_ai.ui.settings_dialog as sd
    import freecad_ai.ui.command_state as command_state
    defaults = PROVIDER_PRESETS["github"]["default_rerank"]
    dialog.provider_section.provider_combo.setCurrentIndex(
        get_provider_names().index("github"))
    monkeypatch.setattr(sd, "save_current_config", lambda: None)
    monkeypatch.setattr(sd, "notify_config_changed", lambda: None)
    monkeypatch.setattr(command_state, "set_command_checked",
                        lambda *a, **k: None)
    dialog._save()
    from freecad_ai.config import get_config
    assert get_config().rerank_method == defaults["method"]
    assert get_config().rerank_top_n == defaults["top_n"]


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


def test_edit_survives_save_and_resolve_params(dialog, cfg):
    """The #75-round regression end to end: a migrated profile whose params
    were also copied into the legacy cfg.model_params. A table edit must be
    what runtime resolves after OK, not the legacy copy."""
    from freecad_ai.llm.client import resolve_params
    cfg.model_params = {"m-a": {"temperature": 1}}
    dialog._populate_model_params_table({"temperature": 0.2})
    dialog._save()
    assert resolve_params(cfg, cfg.provider)["temperature"] == 0.2
