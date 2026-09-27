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
# _fake_save_dialog mirrors the SettingsDialog._save fake used to exercise
# the profile-section save path (tests/unit/test_profile_selector.py).
from tests.unit.test_profile_selector import _fake_save_dialog  # noqa: E402


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
    def test_ok_notifies_after_saving(self, monkeypatch):
        order = []
        c = AppConfig()
        monkeypatch.setattr("freecad_ai.ui.settings_dialog.get_config", lambda: c)
        monkeypatch.setattr("freecad_ai.ui.settings_dialog.save_current_config",
                            lambda: order.append("save"))
        monkeypatch.setattr("freecad_ai.ui.settings_dialog.notify_config_changed",
                            lambda: order.append("notify"))
        fake = _fake_save_dialog()
        fake._confirm_incomplete_profiles.return_value = True
        SettingsDialog._save(fake)
        assert order == ["save", "notify"]

    def test_a_declined_save_does_not_notify(self, monkeypatch):
        calls = []
        monkeypatch.setattr("freecad_ai.ui.settings_dialog.get_config",
                            lambda: AppConfig())
        monkeypatch.setattr("freecad_ai.ui.settings_dialog.notify_config_changed",
                            lambda: calls.append(1))
        fake = _fake_save_dialog()
        fake._confirm_incomplete_profiles.return_value = False
        SettingsDialog._save(fake)
        assert calls == []
