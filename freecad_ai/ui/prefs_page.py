"""Edit → Preferences → FreeCAD AI: four pages, one per settings page (#101).

FreeCAD instantiates each class itself (InitGui.py registers them with
Gui.addPreferencePage) and calls loadSettings() each time Preferences
opens and saveSettings() on every OK and Apply, for every page, opened or
not. Each page writes only the fields that differ from what it loaded, so
an untouched OK leaves config.json byte-identical, and four pages saving in
turn never undo one another.

Every class needs its own module-level name: FreeCAD keys Python pages by
class __name__, and two classes sharing one showed as a single page. So
the common logic sits in a base class, not a factory.

Qt-heavy imports live in methods: InitGui imports this module at FreeCAD
startup, long before a page is first shown.
"""

from ..config import get_config, notify_config_changed, save_current_config
from ..i18n import QT_TRANSLATE_NOOP


def _warn(parent, title, text):
    from .compat import QtWidgets
    QtWidgets.QMessageBox.warning(parent, title, text)


class _PrefsPageBase:
    _TITLE = ""

    def __init__(self, parent=None):
        from .compat import QtWidgets
        from ..i18n import translate

        self.form = QtWidgets.QWidget(parent)
        self.form.setWindowTitle(translate("SettingsDialog", self._TITLE))
        layout = QtWidgets.QVBoxLayout(self.form)
        self.page = self._make_page()
        layout.addWidget(self.page)
        layout.addStretch()
        self.page.closeHostRequested.connect(self._close_host)

    def _make_page(self):
        raise NotImplementedError

    def loadSettings(self):  # noqa: N802 — FreeCAD's name
        self._load(label=None)

    def _load(self, label):
        try:
            self.page.load(get_config(), label=label)
        except Exception as e:   # the page's baseline stays None: no writes
            try:
                import FreeCAD
                FreeCAD.Console.PrintError(
                    f"FreeCAD AI: {self._TITLE} settings failed to load: {e}\n")
            except ImportError:
                pass

    def saveSettings(self):  # noqa: N802 — FreeCAD's name
        if not self.page.is_dirty():
            return
        cfg = get_config()
        self.page.apply_to(cfg)
        save_current_config()
        notify_config_changed()
        self.page.after_save(cfg)
        # Re-baseline, so a second Apply writes nothing; stay on the
        # profile being edited rather than jumping back to the active one.
        self._load(label=self.page.view_label())
        self._after_saved(cfg)

    def _after_saved(self, cfg):
        pass

    def _close_host(self, save):
        """The page's editor prompt: close Preferences so the docked script
        editor is reachable. Accept runs every page's saveSettings(), safe
        because each writes only its own changes."""
        from .compat import QtWidgets
        win = self.form.window()
        if isinstance(win, QtWidgets.QDialog):
            win.accept() if save else win.reject()

    def _host_can_close(self):
        from .compat import QtWidgets
        return isinstance(self.form.window(), QtWidgets.QDialog)


class FreeCADAIProviderPrefs(_PrefsPageBase):
    _TITLE = QT_TRANSLATE_NOOP("SettingsDialog", "Provider")

    def _make_page(self):
        from .settings_pages.provider_page import ProviderPage
        return ProviderPage()

    def _after_saved(self, cfg):
        """saveSettings() cannot veto FreeCAD's OK, so warn after saving."""
        from ..i18n import translate
        from .provider_section import ProviderSection
        unfilled = ProviderSection._profiles_with_url_placeholder(cfg.profiles)
        if unfilled:
            _warn(self.form,
                  translate("SettingsDialog", "Profile cannot be used as set up"),
                  translate(
                      "SettingsDialog",
                      "The Base URL for %s still contains a placeholder such "
                      "as {ACCOUNT_ID}. Replace it with the value from your "
                      "provider account.") % ", ".join(unfilled))


class FreeCADAIBehaviorPrefs(_PrefsPageBase):
    _TITLE = QT_TRANSLATE_NOOP("SettingsDialog", "Behavior")

    def _make_page(self):
        from .settings_pages.behavior_page import BehaviorPage
        return BehaviorPage()


class FreeCADAIToolsPrefs(_PrefsPageBase):
    _TITLE = QT_TRANSLATE_NOOP("SettingsDialog", "Tools")

    def _make_page(self):
        from .settings_pages.tools_page import ToolsPage
        page = ToolsPage()
        # A host that cannot close cannot reveal the docked editor, so
        # Edit/New open externally instead of prompting.
        page.host_can_close = self._host_can_close
        return page


class FreeCADAIMcpPrefs(_PrefsPageBase):
    _TITLE = QT_TRANSLATE_NOOP("SettingsDialog", "MCP")

    def _make_page(self):
        from .settings_pages.mcp_page import McpPage
        return McpPage()
