"""The switch that keeps a turn's reasoning in the history.

This is deliberately *not* one of the #47 caching switches, and it is the
one Behavior switch in this release that ships **on**. Moonshot's engineers
report a clear, measurable drop in reply quality on turns whose
``reasoning_content`` is missing, in ordinary multi-turn chat and not merely
in tool loops, and recommend preserving every turn's reasoning whether or
not you care about caching (forum thread 602). A default that quietly
degrades replies is not a conservative default, so the flag exists as an
escape hatch rather than as an opt-in.

A widget with a reader and no writer is a silent no-op that reports no
error, which has caught this dialog repeatedly, so both directions are
pinned here rather than assumed.
"""

import inspect
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
from freecad_ai.ui import chat_widget as cw  # noqa: E402
from freecad_ai.ui.settings_pages.behavior_page import BehaviorPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def page(qapp, tmp_config_dir):
    p = BehaviorPage()
    yield p
    p.deleteLater()


class TestTheDefault:

    def test_it_ships_on(self):
        assert AppConfig().preserve_reasoning_history is True

    def test_a_config_written_before_this_release_still_loads_on(self):
        """Upgrading must not fail on JSON that predates the key, and must
        not read its absence as "the user turned this off"."""
        restored = AppConfig.from_dict({"max_tokens": 20000})

        assert restored.preserve_reasoning_history is True


class TestOKWritesItToTheConfig:

    def test_unticked_turns_it_off(self, page):
        page.load(AppConfig())
        page.preserve_reasoning_check.setChecked(False)
        target = AppConfig()

        page.apply_to(target)

        assert target.preserve_reasoning_history is False

    def test_ticked_writes_true_not_merely_leaves_the_default(self, page):
        """Start from False, so an absent write leaves False and fails."""
        cfg = AppConfig()
        cfg.preserve_reasoning_history = False
        page.load(cfg)

        page.preserve_reasoning_check.setChecked(True)
        page.apply_to(cfg)

        assert cfg.preserve_reasoning_history is True


class TestReopeningTheDialogShowsWhatWasSaved:

    def test_an_off_config_leaves_it_unticked(self, page):
        cfg = AppConfig()
        cfg.preserve_reasoning_history = False

        page.load(cfg)

        assert page.preserve_reasoning_check.isChecked() is False

    def test_an_on_config_ticks_it(self, page):
        page.load(AppConfig())

        assert page.preserve_reasoning_check.isChecked() is True


class TestTheWorkerActuallyConsultsIt:
    """Without this the escape hatch is inert: ``preserve_history`` defaults
    on, so a call site that forgets to pass it keeps working -- and unticking
    the box would silently do nothing."""

    def test_run_resolves_the_flag_from_the_config(self):
        src = inspect.getsource(cw._LLMWorker.run)

        assert "preserve_reasoning_history" in src

    def test_the_tool_loop_passes_the_resolved_flag_on(self):
        src = inspect.getsource(cw._LLMWorker._tool_loop)

        assert "self._preserve_reasoning" in src
