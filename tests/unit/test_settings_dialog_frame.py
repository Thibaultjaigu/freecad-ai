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
