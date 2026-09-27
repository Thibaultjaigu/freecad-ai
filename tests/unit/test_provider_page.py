"""ProviderPage: the provider section, its parameter table and probes (#101)."""

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

from freecad_ai.config import AppConfig, ProviderConfig  # noqa: E402
import freecad_ai.ui.settings_pages.provider_page as pp_mod  # noqa: E402
from freecad_ai.ui.settings_pages.provider_page import ProviderPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def _cfg():
    c = AppConfig()
    c.profiles = {
        "cloud": ProviderConfig(name="anthropic", model="m1",
                                base_url="https://api.anthropic.com",
                                params={"temperature": 0.2}),
        "local": ProviderConfig(name="ollama", model="m2",
                                base_url="http://localhost:11434/v1"),
    }
    c.active_profile = "cloud"
    return c


@pytest.fixture
def page(qapp):
    p = ProviderPage()
    yield p
    p.deleteLater()


def test_the_test_reranker_row_sits_in_the_utility_group(page):
    w = page._rerank_test_btn.parent()
    while w is not None and w is not page.section.utility_group:
        w = w.parent()
    assert w is page.section.utility_group


def test_untouched_load_writes_nothing(page):
    page.load(_cfg())
    target = _cfg()
    target.temperature = 0.9
    target.profiles["cloud"].model = "newer"
    page.apply_to(target)
    assert target.temperature == 0.9
    assert target.profiles["cloud"].model == "newer"
    assert page.is_dirty() is False


def test_a_param_edit_reaches_the_profile_and_the_temperature(page):
    page.load(_cfg())
    page.model_params_table.item(0, 1).setText("0.7")
    assert page.is_dirty() is True
    target = _cfg()
    page.apply_to(target)
    assert target.profiles["cloud"].params == {"temperature": 0.7}
    assert target.temperature == 0.7


def test_view_label_follows_the_shown_profile(page):
    page.load(_cfg(), label="local")
    assert page.view_label() == "local"


def test_a_failed_load_writes_nothing(page, monkeypatch):
    monkeypatch.setattr(page.section, "load",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(RuntimeError):
        page.load(_cfg())
    target = _cfg()
    page.apply_to(target)
    assert target.to_dict() == _cfg().to_dict()


class TestConnectionProbe:
    def _capture(self, monkeypatch):
        made = {}

        class _T:
            def __init__(self, *a, **k):
                made["args"], made["kwargs"] = a, k
                self.finished = self.vision_result = \
                    self.capabilities_result = self

            def connect(self, *_):
                pass

            def start(self):
                made["started"] = True

        monkeypatch.setattr(pp_mod, "_TestConnectionThread", _T)
        return made

    def test_max_tokens_and_thinking_come_from_the_saved_config(
            self, page, monkeypatch, tmp_config_dir):
        import freecad_ai.config as config_mod
        live = config_mod.get_config()
        live.max_tokens, live.thinking = 12345, "extended"
        made = self._capture(monkeypatch)
        page.load(_cfg())
        page._test_connection()
        assert made["kwargs"]["max_tokens"] == 12345
        assert made["kwargs"]["thinking"] == "extended"

    def test_the_thread_outlives_the_page(self, page, monkeypatch):
        made = self._capture(monkeypatch)
        page.load(_cfg())
        page._test_connection()
        assert made["kwargs"]["parent"] is QtWidgets.QApplication.instance()

    def test_busy_is_signalled(self, page, monkeypatch):
        self._capture(monkeypatch)
        got = []
        page.busyChanged.connect(got.append)
        page.load(_cfg())
        page._test_connection()
        page._on_test_finished(False, "nope")
        assert got == [True, False]
