"""ToolsPage: reranking, user tools, skills, hooks, editor (#101)."""

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

from freecad_ai.config import AppConfig  # noqa: E402
import freecad_ai.ui.settings_pages.tools_page as tp_mod  # noqa: E402
from freecad_ai.ui.settings_pages.tools_page import ToolsPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def page(qapp, tmp_config_dir):
    p = ToolsPage()
    yield p
    p.deleteLater()


def test_group_boxes_in_order(page):
    titles = [g.title() for g in page.findChildren(QtWidgets.QGroupBox)]
    assert titles == ["Tool Reranking", "User Tools", "Skills", "Hooks",
                      "Editor"]


def test_untouched_load_writes_nothing(page):
    page.load(AppConfig())
    target = AppConfig()
    target.rerank_method = "keyword"   # e.g. just set by #10 on the provider page
    target.rerank_top_n = 8
    page.apply_to(target)
    assert (target.rerank_method, target.rerank_top_n) == ("keyword", 8)


def test_an_unshowable_method_survives_an_unrelated_edit(page):
    cfg = AppConfig()
    cfg.rerank_method = "semantic"     # hand-edited, no combo entry
    page.load(cfg)
    page.scan_macros_cb.setChecked(not cfg.scan_freecad_macros)
    page.apply_to(cfg)
    assert cfg.rerank_method == "semantic"


def test_pinned_tools_are_compared_parsed(page):
    cfg = AppConfig()
    cfg.rerank_pinned_tools = ["a", "b"]
    page.load(cfg)
    page.rerank_pinned_edit.setText("a ,b,")
    assert page.is_dirty() is False


def test_an_edit_writes_only_that_field(page):
    page.load(AppConfig())
    page.rerank_top_n_spin.setValue(25)
    target = AppConfig()
    target.use_external_editor = True
    page.apply_to(target)
    assert target.rerank_top_n == 25
    assert target.use_external_editor is True


@pytest.mark.parametrize("edit", ["method", "top_n"])
def test_a_reranker_edit_writes_method_and_top_n_together(page, edit):
    """#10: the Provider page may have just set the preset pair; editing
    either half here must overwrite both, or the preset's other half
    survives when Provider saves first."""
    page.load(AppConfig())
    if edit == "method":
        page.rerank_method_combo.setCurrentIndex(2)       # llm
        expected = ("llm", 15)
    else:
        page.rerank_top_n_spin.setValue(20)
        expected = ("off", 20)
    target = AppConfig()
    target.rerank_method, target.rerank_top_n = "keyword", 8
    target.use_external_editor = True                     # not ours
    page.apply_to(target)
    assert (target.rerank_method, target.rerank_top_n) == expected
    assert target.use_external_editor is True


def test_a_hand_edited_method_survives_a_top_n_edit(page):
    """The pair write takes the unedited half from the config as loaded,
    not from the widget, so a value the combo cannot show survives."""
    cfg = AppConfig()
    cfg.rerank_method = "semantic"     # hand-edited, shown as "off"
    page.load(cfg)
    page.rerank_top_n_spin.setValue(20)
    page.apply_to(cfg)
    assert (cfg.rerank_method, cfg.rerank_top_n) == ("semantic", 20)


class TestEditorPrompt:
    def _answer(self, monkeypatch, button):
        monkeypatch.setattr(tp_mod.QMessageBox, "question",
                            staticmethod(lambda *a, **k: button))

    def test_save_asks_the_host_to_save_and_close(self, page, monkeypatch):
        self._answer(monkeypatch, tp_mod.QMessageBox.Save)
        got = []
        page.closeHostRequested.connect(got.append)
        page.use_external_editor_cb.setChecked(False)
        assert page._prepare_editor_open() is True
        assert got == [True]

    def test_discard_asks_the_host_to_close(self, page, monkeypatch):
        self._answer(monkeypatch, tp_mod.QMessageBox.Discard)
        got = []
        page.closeHostRequested.connect(got.append)
        page.use_external_editor_cb.setChecked(False)
        assert page._prepare_editor_open() is True
        assert got == [False]

    def test_cancel_does_nothing(self, page, monkeypatch):
        self._answer(monkeypatch, tp_mod.QMessageBox.Cancel)
        got = []
        page.closeHostRequested.connect(got.append)
        page.use_external_editor_cb.setChecked(False)
        assert page._prepare_editor_open() is False
        assert got == []

    def test_a_host_that_cannot_close_opens_externally_unasked(
            self, page, monkeypatch):
        monkeypatch.setattr(tp_mod.QMessageBox, "question",
                            staticmethod(lambda *a, **k: pytest.fail("asked")))
        opened = []
        monkeypatch.setattr(page, "_open_in_external_editor", opened.append)
        page.host_can_close = lambda: False
        page.use_external_editor_cb.setChecked(False)
        assert page._prepare_editor_open() is True
        page._open_path("/tmp/x.py")
        assert opened == ["/tmp/x.py"]
