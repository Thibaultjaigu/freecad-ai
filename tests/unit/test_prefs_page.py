"""Edit → Preferences → FreeCAD AI (#99, #101).

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
import freecad_ai.ui.prefs_page as pp  # noqa: E402

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


def _disk():
    with open(config_mod.CONFIG_FILE, "rb") as f:
        return f.read()


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


@pytest.mark.parametrize("order", ["preferences", "reversed"])
@pytest.mark.parametrize("edit, expected", [
    ("method", ("llm", 15)),     # method → llm; top_n stays 15 on screen
    ("top_n", ("off", 20)),      # top_n → 20; method stays off on screen
])
def test_provider_page_before_and_after_tools_page_agree(pages, order, edit,
                                                         expected):
    """#10 order independence: the reranker pair shown on the Tools page
    wins whole, whichever page saves first. Fresh config and pages per
    order (the fixture), so the provider switch really fires each time."""
    import freecad_ai.llm.providers as prov_mod
    c = config_mod.get_config()
    assert (c.rerank_method, c.rerank_top_n) == ("off", 15)
    section = pages["FreeCADAIProviderPrefs"].page.section
    idx = prov_mod.get_provider_names().index("github")
    assert section.provider_combo.currentIndex() != idx
    section.provider_combo.setCurrentIndex(idx)
    assert section._pending_rerank == {"method": "keyword", "top_n": 8}
    tools = pages["FreeCADAIToolsPrefs"].page
    if edit == "method":
        tools.rerank_method_combo.setCurrentIndex(2)      # llm
    else:
        tools.rerank_top_n_spin.setValue(20)
    names = ALL if order == "preferences" else list(reversed(ALL))
    for name in names:
        pages[name].saveSettings()
    c = config_mod.get_config()
    assert (c.rerank_method, c.rerank_top_n) == expected


def test_provider_switch_alone_applies_the_preset_rerank(pages):
    """The #10 default itself still lands when Tools is untouched."""
    import freecad_ai.llm.providers as prov_mod
    section = pages["FreeCADAIProviderPrefs"].page.section
    section.provider_combo.setCurrentIndex(
        prov_mod.get_provider_names().index("github"))
    _ok(pages)
    c = config_mod.get_config()
    assert (c.rerank_method, c.rerank_top_n) == ("keyword", 8)


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


def test_initgui_registers_the_four_pages_in_order():
    src = open(os.path.join(PROJECT_ROOT, "InitGui.py"), encoding="utf-8").read()
    start = src.index("_pp.FreeCADAIProviderPrefs")
    block = src[start:src.index("addPreferencePage", start)]
    assert [n for n in ALL if n in block] == ALL
    assert "FreeCADAIPrefsPage" not in src


def test_importing_the_module_builds_no_qt_widgets():
    """InitGui imports the page class at startup; the Qt-heavy imports
    must wait until FreeCAD instantiates it."""
    code = ("import sys, freecad_ai.ui.prefs_page; "
            "print('freecad_ai.ui.provider_section' in sys.modules)")
    env = dict(os.environ, PYTHONPATH="")
    out = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT,
                         env=env, capture_output=True, text=True)
    assert out.stdout.strip() == "False", out.stderr


def test_preferences_saves_the_fallback_list(pages):
    section = pages["FreeCADAIProviderPrefs"].page.section
    section.fallback_picker.setCurrentIndex(
        section.fallback_picker.findData("local"))
    section.fallback_add_btn.click()
    _ok(pages)
    assert config_mod.load_config().fallback_profiles == ["local"]
