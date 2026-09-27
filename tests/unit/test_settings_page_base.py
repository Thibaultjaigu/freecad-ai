"""SettingsPage: the baseline/diff contract every settings page shares (#101)."""

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
from freecad_ai.ui.settings_pages.base import SettingsPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


class _Page(SettingsPage):
    """Two fields, held in plain attributes rather than widgets."""

    def _show(self, cfg, label):
        self.max_tokens = cfg.max_tokens
        self.pinned = list(cfg.rerank_pinned_tools)

    def _values(self):
        return {"max_tokens": self.max_tokens,
                "rerank_pinned_tools": self.pinned}


class _Broken(SettingsPage):
    def _show(self, cfg, label):
        raise RuntimeError("load failed")


@pytest.fixture
def page(qapp):
    p = _Page()
    yield p
    p.deleteLater()


def test_a_never_loaded_page_is_clean_and_writes_nothing(page):
    cfg = AppConfig()
    before = cfg.to_dict()
    assert page.is_dirty() is False
    page.apply_to(cfg)
    assert cfg.to_dict() == before


def test_an_untouched_load_applies_nothing(page):
    src = AppConfig()
    src.max_tokens = 9999
    page.load(src)
    target = AppConfig()
    target.max_tokens = 1234   # someone else's newer value
    page.apply_to(target)
    assert target.max_tokens == 1234
    assert page.is_dirty() is False


def test_only_the_edited_field_is_written(page):
    src = AppConfig()
    page.load(src)
    page.max_tokens = 8192
    target = AppConfig()
    target.rerank_pinned_tools = ["other_edit"]
    page.apply_to(target)
    assert target.max_tokens == 8192
    assert target.rerank_pinned_tools == ["other_edit"]
    assert page.is_dirty() is True


def test_a_reverted_edit_is_clean(page):
    src = AppConfig()
    page.load(src)
    page.max_tokens = 8192
    page.max_tokens = src.max_tokens
    assert page.is_dirty() is False


def test_applied_values_are_copies(page):
    page.load(AppConfig())
    page.pinned.append("x")
    target = AppConfig()
    page.apply_to(target)
    page.pinned.append("y")
    assert target.rerank_pinned_tools == ["x"]


def test_a_failed_load_leaves_the_baseline_none(qapp):
    p = _Broken()
    with pytest.raises(RuntimeError):
        p.load(AppConfig())
    assert p._baseline is None
    assert p.is_dirty() is False
    p.deleteLater()


def test_the_defaults(page):
    assert page.view_label() is None
    page.after_save(AppConfig())   # no-op, must not raise
