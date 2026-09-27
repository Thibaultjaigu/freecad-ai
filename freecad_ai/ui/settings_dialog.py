"""Settings dialog for FreeCAD AI.

Provides a GUI for configuring:
  - LLM provider (Anthropic, OpenAI, Ollama, Gemini, OpenRouter, Moonshot,
    DeepSeek, Qwen, Groq, Mistral, Together, Fireworks, xAI, Cohere,
    SambaNova, MiniMax, Custom)
  - API key, base URL, model name
  - Max tokens, temperature
  - Auto-execute toggle
  - User extension tools
  - Test connection button
"""

from .compat import QtWidgets, QtGui
from ..i18n import translate

QDialog = QtWidgets.QDialog
QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QPushButton = QtWidgets.QPushButton
QDoubleValidator = QtGui.QDoubleValidator

QMessageBox = QtWidgets.QMessageBox

from ..config import get_config, notify_config_changed, save_current_config
from .provider_section import ProviderSection
from .settings_pages.behavior_page import BehaviorPage
from .settings_pages.mcp_page import McpPage
from .settings_pages.provider_page import ProviderPage
from .settings_pages.tools_page import ToolsPage


class SettingsDialog(QDialog):
    """Configuration dialog for FreeCAD AI."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(translate("SettingsDialog", "FreeCAD AI Settings"))
        self.setMinimumHeight(400)
        self._test_thread = None
        self._cfg = get_config()
        self._build_ui()
        # Width comes from the built layout, never a constant. The profile
        # row (combo + New/Rename/Delete) is the widest thing on the form
        # and its buttons are translated, so a hardcoded width clips the
        # rightmost one in some locale — which is how a 540 predating that
        # row came to hide Delete behind a horizontal scrollbar. Must run
        # after _build_ui(): sizeHint() before it describes an empty dialog.
        # Height stays fixed; the content is taller than most screens and
        # is meant to scroll — which is also why the vertical scrollbar is
        # always there, and why its width has to be added on: sizeHint()
        # does not reserve room for it, leaving the form overflowing by
        # exactly one scrollbar and growing a horizontal one to say so.
        width = self.sizeHint().width() + self.style().pixelMetric(
            QtWidgets.QStyle.PM_ScrollBarExtent)
        self.setMinimumWidth(width)
        self.resize(width, 700)
        self._load_from_config()

    def _build_ui(self):
        outer_layout = QVBoxLayout(self)

        # Scrollable content area
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll_widget = QtWidgets.QWidget()
        layout = QVBoxLayout(scroll_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(scroll_widget)
        outer_layout.addWidget(scroll, 1)  # stretch factor 1 — takes available space

        # LLM Provider + Utility models, Model Parameters table, and the
        # two connection probes: shared with Edit → Preferences (#99, #101).
        self.provider_page = ProviderPage()
        # Kept as a name: tests and the #78 geometry check reach the
        # profile row through it.
        self.provider_section = self.provider_page.section
        self.provider_page.busyChanged.connect(
            lambda busy: (self.save_btn.setEnabled(not busy),
                          self.cancel_btn.setEnabled(not busy)))
        layout.addWidget(self.provider_page)

        self.behavior_page = BehaviorPage()
        layout.addWidget(self.behavior_page)

        self.tools_page = ToolsPage()
        layout.addWidget(self.tools_page)
        self.tools_page.closeHostRequested.connect(self._on_close_requested)

        self.mcp_page = McpPage()
        layout.addWidget(self.mcp_page)

        # Dialog buttons (outside scroll area)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.save_btn = QPushButton(translate("SettingsDialog", "Save"))
        self.save_btn.setStyleSheet(
            "QPushButton { padding: 6px 24px; font-weight: bold; }"
        )
        self.save_btn.clicked.connect(self._save)
        btn_layout.addWidget(self.save_btn)

        self.cancel_btn = QPushButton(translate("SettingsDialog", "Cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        outer_layout.addLayout(btn_layout)

    def _load_from_config(self):
        """Populate fields from the current config."""
        cfg = self._cfg = get_config()

        # Profile edits stay in the section's own copy until OK (see
        # ProviderSection.load for why the live singleton must not see them).
        self.provider_page.load(cfg)

        self.behavior_page.load(cfg)

        self.mcp_page.load(cfg)
        self.tools_page.load(cfg)

    @staticmethod
    def _profiles_missing_base_url(profiles) -> list:
        """Sorted labels of profiles with no Base URL, which cannot work.

        LLMClient builds every request URL as base_url + a path, so a blank
        one yields a relative path and a network error nowhere near the
        cause. Resolution deliberately does not substitute the provider
        preset here — a silent substitution is issue #75's shape — so the
        blank has to be surfaced instead.
        """
        return sorted(
            label for label, prof in profiles.items()
            if not (getattr(prof, "base_url", "") or "").strip())

    def _confirm_incomplete_profiles(self, profiles) -> bool:
        """Ask before saving a profile that cannot work. True to proceed.

        Covers both ways a Base URL is unusable — blank, or still carrying a
        preset placeholder — in a single question, so a config with one of
        each is not two dialogs deep before it can be saved.

        A question rather than a refusal: a config may already carry a
        half-filled profile the user never selects, and blocking OK on it
        would strand every unrelated setting in this dialog.
        """
        problems = []
        blank = self._profiles_missing_base_url(profiles)
        if blank:
            problems.append(translate(
                "SettingsDialog",
                "No Base URL is set for: %s.") % ", ".join(blank))
        unfilled = ProviderSection._profiles_with_url_placeholder(profiles)
        if unfilled:
            problems.append(translate(
                "SettingsDialog",
                "The Base URL for %s still contains a placeholder such as "
                "{ACCOUNT_ID}. Replace it with the value from your provider "
                "account.") % ", ".join(unfilled))
        if not problems:
            return True
        return QMessageBox.question(
            self,
            translate("SettingsDialog", "Profile cannot be used as set up"),
            "\n\n".join(problems) + "\n\n" + translate(
                "SettingsDialog",
                "Requests made with such a profile fail with a connection "
                "error rather than a clear message. Save anyway?"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No) == QMessageBox.Yes

    def _on_close_requested(self, save):
        """A page's editor prompt: Save → the OK path, Discard → Cancel."""
        if save:
            self._save()
        else:
            self.reject()

    def _save(self):
        """Save settings to config and close."""
        cfg = get_config()

        # Profile edits (add/rename/delete/field changes) have lived in the
        # section's working copy since _load_from_config. OK is the only
        # point where they land in the real config — commit the visible
        # widgets into the currently-shown profile first. The working copy
        # itself is written back near the end, via provider_page.apply_to(cfg),
        # after the other pages below so its #10 factory-default check
        # sees this save's own values.
        section = self.provider_section
        section.commit()
        if not self._confirm_incomplete_profiles(section.profiles()):
            return

        self.behavior_page.apply_to(cfg)
        self.mcp_page.apply_to(cfg)
        self.tools_page.apply_to(cfg)

        # After the widget writes above, so the section's #10 factory-
        # default check (in apply_to) sees this save's own rerank values.
        self.provider_page.apply_to(cfg)

        save_current_config()

        # The chat panel (and anything else showing config-derived state)
        # refreshes from this, whichever window saved (#99).
        notify_config_changed()

        self.behavior_page.after_save(cfg)

        self.accept()

