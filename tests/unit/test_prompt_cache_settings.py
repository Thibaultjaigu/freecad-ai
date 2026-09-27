"""The two prompt-caching switches reach the config and come back (#47).

A settings widget with a reader but no writer -- or the reverse -- is a
silent no-op: the checkbox moves, nothing happens, and nothing reports an
error. That has caught this dialog repeatedly, so both directions are
pinned here rather than assumed.

The defaults matter as much as the plumbing. Both flags ship off, so an
existing install behaves after the upgrade exactly as it did before it;
the caching one in particular changes what the model is shown, which is
not a thing to switch on for someone without asking.
"""

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
from freecad_ai.ui.settings_pages.behavior_page import BehaviorPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def cfg(monkeypatch):
    """A throwaway config, with the disk flush stubbed out."""
    c = AppConfig()
    monkeypatch.setattr("freecad_ai.ui.settings_dialog.get_config", lambda: c)
    monkeypatch.setattr(
        "freecad_ai.ui.settings_dialog.save_current_config", lambda: None)
    return c


@pytest.fixture
def page(qapp, tmp_config_dir):
    p = BehaviorPage()
    yield p
    p.deleteLater()


class TestTheDefaults:

    def test_both_switches_ship_off(self):
        fresh = AppConfig()

        assert fresh.optimize_prompt_caching is False
        assert fresh.log_token_usage is False

    def test_a_config_written_before_this_release_still_loads(self):
        """Upgrading must not fail on JSON that predates these keys."""
        restored = AppConfig.from_dict({"max_tokens": 20000})

        assert restored.optimize_prompt_caching is False
        assert restored.log_token_usage is False


class TestOKWritesThemToTheConfig:

    def test_caching_on(self, page):
        page.load(AppConfig())
        page.prompt_cache_check.setChecked(True)
        target = AppConfig()

        page.apply_to(target)

        assert target.optimize_prompt_caching is True

    def test_usage_logging_on(self, page):
        page.load(AppConfig())
        page.log_usage_check.setChecked(True)
        target = AppConfig()

        page.apply_to(target)

        assert target.log_token_usage is True

    def test_unchecked_writes_false_not_merely_leaves_the_default(self, page):
        """Start from True, so an absent write leaves True and fails."""
        cfg = AppConfig()
        cfg.optimize_prompt_caching = True
        cfg.log_token_usage = True
        page.load(cfg)

        page.prompt_cache_check.setChecked(False)
        page.log_usage_check.setChecked(False)
        page.apply_to(cfg)

        assert cfg.optimize_prompt_caching is False
        assert cfg.log_token_usage is False

    def test_the_two_are_independent(self, page):
        page.load(AppConfig())
        page.prompt_cache_check.setChecked(True)
        target = AppConfig()

        page.apply_to(target)

        assert target.optimize_prompt_caching is True
        assert target.log_token_usage is False


class TestReopeningTheDialogShowsWhatWasSaved:

    def test_both_checkboxes_are_restored(self, page):
        cfg = AppConfig()
        cfg.optimize_prompt_caching = True
        cfg.log_token_usage = True

        page.load(cfg)

        assert page.prompt_cache_check.isChecked() is True
        assert page.log_usage_check.isChecked() is True

    def test_an_off_config_leaves_them_unticked(self, page):
        page.load(AppConfig())

        assert page.prompt_cache_check.isChecked() is False
        assert page.log_usage_check.isChecked() is False


class TestTheClientIsBuiltFromThoseFlags:
    """The switches are inert unless create_client passes them on."""

    def test_both_flags_reach_the_client(self, cfg, monkeypatch):
        from freecad_ai.llm.client import create_client
        cfg.optimize_prompt_caching = True
        cfg.log_token_usage = True

        client = create_client(cfg)

        assert client.prompt_caching is True
        assert client.log_usage is True

    def test_a_utility_client_never_caches(self, cfg):
        """The reranker and the probes send a different prefix every call,
        so a cache write there is paid for and never read back."""
        from freecad_ai.llm.client import create_client
        cfg.optimize_prompt_caching = True

        client = create_client(cfg, utility="rerank")

        assert client.prompt_caching is False

    def test_off_by_default_the_client_agrees(self, cfg):
        from freecad_ai.llm.client import create_client

        client = create_client(cfg)

        assert client.prompt_caching is False
        assert client.log_usage is False
