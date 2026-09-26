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
