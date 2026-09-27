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


def test_an_unedited_fallback_preview_is_not_promoted_into_the_profile(page):
    """A profile with no params shows the global temperature as a preview
    of what resolve_params() will send. Never touching that preview must
    not turn it into an explicit override — Save with nothing edited has
    to leave a params-less profile params-less (#101)."""
    page.load(_cfg(), label="local")
    assert page.is_dirty() is False
    target = _cfg()
    page.apply_to(target)
    assert target.profiles["local"].params == {}


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

            def wait(self):
                pass

            def deleteLater(self):
                pass

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


class TestProbeThreadsAreFreed:
    """Each Test Connection / Test Reranker click used to leak its QThread
    (parented to the QApplication, never deleted) and the LLM client in it.
    Real threads here, with a fake LLMClient that answers at once."""

    @pytest.fixture
    def fake_client(self, monkeypatch, tmp_config_dir):
        behaviour = {"fail": False}

        class _Client:
            def __init__(self, **kwargs):
                pass

            def test_connection(self):
                if behaviour["fail"]:
                    raise RuntimeError("refused")
                return "pong"

            def detect_capabilities(self):
                return {"vision": False, "tools": True}

        monkeypatch.setattr("freecad_ai.llm.client.LLMClient", _Client)
        return behaviour

    @staticmethod
    def _run_to_completion(qapp, page, attr):
        import time
        from freecad_ai.ui.compat import QtCore
        thread = getattr(page, attr)
        assert thread is not None
        destroyed = []
        thread.destroyed.connect(lambda *_: destroyed.append(True))
        deadline = time.monotonic() + 10
        while getattr(page, attr) is not None and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert getattr(page, attr) is None
        QtCore.QCoreApplication.sendPostedEvents(
            None, QtCore.QEvent.DeferredDelete)
        assert destroyed == [True]

    @pytest.mark.parametrize("fail", [False, True])
    def test_test_connection_frees_its_thread(self, qapp, page, fake_client,
                                              fail):
        fake_client["fail"] = fail
        page.load(_cfg())
        page._test_connection()
        self._run_to_completion(qapp, page, "_test_thread")
        assert page.test_btn.isEnabled()
        if not fail:
            # The capabilities answer still landed before the release.
            assert page.section.profiles()["cloud"].tools_detected is True

    def test_test_reranker_frees_its_thread(self, qapp, page, fake_client,
                                            monkeypatch):
        monkeypatch.setattr(
            "freecad_ai.tools.reranker.rerank_tools_llm",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
        page.load(_cfg())
        page._test_reranker()
        self._run_to_completion(qapp, page, "_rerank_test_thread")
        assert page._rerank_test_btn.isEnabled()
        assert "down" in page._rerank_test_status.text()
