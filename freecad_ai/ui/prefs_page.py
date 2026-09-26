"""Edit → Preferences → FreeCAD AI.

FreeCAD instantiates this class itself (InitGui.py registers it with
Gui.addPreferencePage) and calls loadSettings() each time Preferences
opens and saveSettings() on every OK and Apply — for every page, opened
or not. So the page saves only when something differs from what it
loaded; an OK elsewhere in Preferences leaves config.json byte-identical.

The provider sections are the Settings dialog's own widget (#99), so the
two windows cannot drift apart again (#12, #97).

Qt-heavy imports live in methods: InitGui imports this module at FreeCAD
startup, long before the page is first shown.
"""

from ..config import get_config, notify_config_changed, save_current_config

_MODES = ["plan", "act"]
_THINKING = ["off", "on", "extended"]


def _warn(parent, title, text):
    from .compat import QtWidgets
    QtWidgets.QMessageBox.warning(parent, title, text)


class FreeCADAIPrefsPage:
    def __init__(self, parent=None):
        from .compat import QtWidgets
        from ..i18n import translate
        from .provider_section import ProviderSection

        self.form = QtWidgets.QWidget(parent)
        self.form.setWindowTitle(translate("FreeCADAIPrefs", "FreeCAD AI"))
        layout = QtWidgets.QVBoxLayout(self.form)

        self.section = ProviderSection()
        layout.addWidget(self.section)

        behavior = QtWidgets.QGroupBox(translate("FreeCADAIPrefs", "Behavior"))
        form = QtWidgets.QFormLayout(behavior)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems([translate("FreeCADAIPrefs", "Plan"),
                                  translate("FreeCADAIPrefs", "Act")])
        form.addRow(translate("FreeCADAIPrefs", "Default mode:"),
                    self.mode_combo)
        self.thinking_combo = QtWidgets.QComboBox()
        self.thinking_combo.addItems([translate("FreeCADAIPrefs", "Off"),
                                      translate("FreeCADAIPrefs", "On"),
                                      translate("FreeCADAIPrefs", "Extended")])
        form.addRow(translate("FreeCADAIPrefs", "Thinking mode:"),
                    self.thinking_combo)
        self.max_tokens_spin = QtWidgets.QSpinBox()
        # The Settings dialog's range. The old page capped at 32768, which
        # clamped a larger saved value on display and wrote it back.
        self.max_tokens_spin.setRange(256, 262144)
        self.max_tokens_spin.setSingleStep(1024)
        form.addRow(translate("FreeCADAIPrefs", "Max tokens:"),
                    self.max_tokens_spin)
        self.enable_tools_check = QtWidgets.QCheckBox()
        form.addRow(translate("FreeCADAIPrefs", "Enable tool calling:"),
                    self.enable_tools_check)
        layout.addWidget(behavior)

        hint = QtWidgets.QLabel(translate(
            "FreeCADAIPrefs",
            "For MCP servers, tool reranking, viewport capture, model "
            "parameters, and more, open the workbench's full settings "
            "dialog (gear icon in the chat panel)."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")
        layout.addWidget(hint)
        layout.addStretch()

        self._behavior_baseline = {}

    def loadSettings(self):  # noqa: N802 — FreeCAD's name
        self._load(label=None)

    def _load(self, label):
        cfg = get_config()
        self.section.load(cfg, label=label)
        # A value these combos cannot show displays as the first entry and
        # is written back only if the user changes that field (see
        # saveSettings), never as a side effect of another edit.
        self.mode_combo.setCurrentIndex(
            _MODES.index(cfg.mode) if cfg.mode in _MODES else 0)
        self.thinking_combo.setCurrentIndex(
            _THINKING.index(cfg.thinking) if cfg.thinking in _THINKING else 0)
        self.max_tokens_spin.setValue(int(cfg.max_tokens))
        self.enable_tools_check.setChecked(bool(cfg.enable_tools))
        self._behavior_baseline = self._behavior_values()

    def _behavior_values(self):
        return {
            "mode": _MODES[self.mode_combo.currentIndex()],
            "thinking": _THINKING[self.thinking_combo.currentIndex()],
            "max_tokens": self.max_tokens_spin.value(),
            "enable_tools": self.enable_tools_check.isChecked(),
        }

    def saveSettings(self):  # noqa: N802 — FreeCAD's name
        changed = {key: value
                   for key, value in self._behavior_values().items()
                   if self._behavior_baseline.get(key) != value}
        if not changed and not self.section.is_dirty():
            return
        cfg = get_config()
        self.section.apply_to(cfg)
        for key, value in changed.items():
            setattr(cfg, key, value)
        save_current_config()
        notify_config_changed()
        # Re-baseline, so a second Apply writes nothing; stay on the
        # profile being edited rather than jumping back to the active one.
        self._load(label=self.section.current_label())
        self._warn_unfilled_placeholders(cfg)

    def _warn_unfilled_placeholders(self, cfg):
        """saveSettings() cannot veto FreeCAD's OK, so warn after saving."""
        from ..i18n import translate
        from .provider_section import ProviderSection
        unfilled = ProviderSection._profiles_with_url_placeholder(cfg.profiles)
        if unfilled:
            _warn(self.form,
                  translate("FreeCADAIPrefs", "Profile cannot be used as set up"),
                  translate(
                      "FreeCADAIPrefs",
                      "The Base URL for %s still contains a placeholder such "
                      "as {ACCOUNT_ID}. Replace it with the value from your "
                      "provider account.") % ", ".join(unfilled))
