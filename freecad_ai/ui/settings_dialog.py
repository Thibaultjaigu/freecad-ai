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

from .compat import QtWidgets, QtCore, QtGui
from ..i18n import translate

QDialog = QtWidgets.QDialog
QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QGroupBox = QtWidgets.QGroupBox
QPushButton = QtWidgets.QPushButton
QLabel = QtWidgets.QLabel
Signal = QtCore.Signal
QThread = QtCore.QThread
QDoubleValidator = QtGui.QDoubleValidator
QTableWidget = QtWidgets.QTableWidget
QTableWidgetItem = QtWidgets.QTableWidgetItem
QHeaderView = QtWidgets.QHeaderView

QMessageBox = QtWidgets.QMessageBox

from ..config import (get_config, notify_config_changed, save_current_config,
                      PROVIDER_PRESETS)
from .provider_section import ProviderSection
from .settings_pages.behavior_page import BehaviorPage
from .settings_pages.mcp_page import McpPage
from .settings_pages.tools_page import ToolsPage


class _TestConnectionThread(QThread):
    """Background thread for testing LLM connection and detecting capabilities.

    Takes everything it needs as arguments rather than reading config — so
    the user can test before saving, and so a profile that isn't
    cfg.active_profile can't have its values smuggled into the active one
    through the singleton (see _TestRerankerThread).

    max_tokens/thinking used to arrive the long way round: the dialog wrote
    the widget values into the singleton (_save_temp) purely so run() could
    read them back off it. Nothing undid that on Cancel, and an unrelated
    save_current_config() elsewhere then flushed cancelled edits to disk
    (#76). temperature is the saved value: the model-params table carries
    the dialog's own, and LLMClient lets it win over this fallback.
    """
    finished = Signal(bool, str)        # success, message
    vision_result = Signal(bool)        # vision probe result
    capabilities_result = Signal(dict)  # full caps dict (Ollama: vision/tools/thinking)

    def __init__(self, provider_name, base_url, api_key, model,
                 model_params, max_tokens, temperature, thinking,
                 parent=None):
        super().__init__(parent)
        self._provider = provider_name
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._model_params = dict(model_params or {})
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._thinking = thinking

    def run(self):
        try:
            from ..llm.client import LLMClient
            client = LLMClient(
                provider_name=self._provider,
                base_url=self._base_url,
                api_key=self._api_key,
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                thinking=self._thinking,
                model_params=self._model_params,
            )
            response = client.test_connection()
            self.finished.emit(True, translate("SettingsDialog", "Connected! Response: ") + response)

            caps = client.detect_capabilities()
            self.vision_result.emit(bool(caps.get("vision", False)))
            self.capabilities_result.emit(caps)
        except Exception as e:
            self.finished.emit(False, str(e))


class _TestRerankerThread(QThread):
    """Background thread for testing the LLM reranker with current dialog values.

    Takes provider/URL/key/model/model_params as arguments rather than
    reading config — so the user can test before saving.
    """
    finished = Signal(bool, str)  # success, message

    def __init__(self, provider_name, base_url, api_key, model,
                 model_params, parent=None):
        super().__init__(parent)
        self._provider = provider_name
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._model_params = dict(model_params or {})

    def run(self):
        try:
            from ..llm.client import LLMClient
            from ..tools.reranker import rerank_tools_llm
            client = LLMClient(
                provider_name=self._provider,
                base_url=self._base_url,
                api_key=self._api_key,
                model=self._model,
                max_tokens=1024,
                temperature=self._model_params.get("temperature", 0.0),
                thinking="off",
                model_params=self._model_params,
            )
            # Small canonical probe set — if reranker is working, the LLM
            # should trivially pick create_sketch and pad_sketch.
            sample = [
                ("create_sketch", "Create a new sketch with geometry"),
                ("pad_sketch", "Extrude a sketch into a solid pad"),
                ("fillet_edges", "Round selected edges with a fillet"),
                ("list_objects", "List all objects in the active document"),
                ("export_stl", "Export an object as an STL mesh file"),
            ]
            messages = []

            def report(m):
                messages.append(m)

            result = rerank_tools_llm(
                sample, "extrude a new sketch into a solid",
                top_n=2, llm_client=client, report=report,
            )
            # Look for explicit failure markers in the diagnostic stream
            failure = next(
                (m for m in messages if "call failed" in m),
                None,
            )
            if failure:
                self.finished.emit(False, failure)
                return

            # Extract the parsed-count and raw-response lines for the report
            parsed_count = 0
            raw_preview = ""
            for m in messages:
                if "parsed" in m and "valid names" in m:
                    # Format: "LLM reranker: parsed N valid names ..."
                    for tok in m.split():
                        if tok.isdigit():
                            parsed_count = int(tok)
                            break
                if "raw response" in m:
                    raw_preview = m

            # An LLM that returned zero valid names (all slots filled by
            # keyword top-up) is effectively not working, even though the
            # HTTP call succeeded. Flag it as an error so the user knows
            # the reranker is doing nothing useful.
            if parsed_count == 0:
                detail = (
                    "LLM returned 0 valid tool names — all picks came from "
                    "keyword fallback. The LLM is responding but not "
                    "producing usable output for reranking. "
                    "Try a more capable or better-suited model."
                )
                if raw_preview:
                    detail += "\n" + raw_preview
                self.finished.emit(False, detail)
                return

            # Partial success: some names from LLM, rest from top-up.
            # Still a green light — LLM is contributing, just not fully.
            topup_count = len(result) - parsed_count
            detail = "Picked: {}".format(", ".join(result))
            detail += " ({} from LLM".format(parsed_count)
            if topup_count > 0:
                detail += ", {} from keyword top-up".format(topup_count)
            detail += ")"
            if raw_preview:
                detail += "\n" + raw_preview
            self.finished.emit(True, detail)
        except Exception as e:
            self.finished.emit(False, "{}: {}".format(type(e).__name__, e))


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

        # LLM Provider + Utility models: shared with Edit → Preferences
        # (#99). The dialog-only parts ride on its signals.
        self.provider_section = ProviderSection()
        self.provider_section.profileShown.connect(self._on_profile_shown)
        self.provider_section.aboutToCommit.connect(self._on_about_to_commit)
        self.provider_section.presetApplied.connect(self._on_preset_applied)
        self.provider_section.modelChanged.connect(self._on_model_changed)
        layout.addWidget(self.provider_section)
        self._last_model_name = ""  # track model name for param save/load

        # Model Parameters group — freeform key-value table
        model_params_group = QGroupBox(translate("SettingsDialog", "Model Parameters"))
        model_params_layout = QVBoxLayout()

        # Freeform sampling parameters table (saved per model name)
        model_params_layout.addWidget(QLabel(
            translate("SettingsDialog",
                      "Sampling parameters sent with each request (saved per model):")
        ))

        self.model_params_table = QTableWidget(0, 2)
        self.model_params_table.setHorizontalHeaderLabels([
            translate("SettingsDialog", "Parameter"),
            translate("SettingsDialog", "Value"),
        ])
        self.model_params_table.horizontalHeader().setStretchLastSection(True)
        self.model_params_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Interactive)
        self.model_params_table.setColumnWidth(0, 160)
        self.model_params_table.setMaximumHeight(140)
        self.model_params_table.setToolTip(
            translate("SettingsDialog",
                      "Parameters are merged into the API request body.\n"
                      "Common: temperature, top_p, top_k, n,\n"
                      "presence_penalty, frequency_penalty, repetition_penalty.\n"
                      "Values are auto-detected as number or string.")
        )
        model_params_layout.addWidget(self.model_params_table)

        mp_btn_layout = QHBoxLayout()
        mp_add_btn = QPushButton(translate("SettingsDialog", "Add"))
        mp_add_btn.clicked.connect(self._add_model_param)
        mp_btn_layout.addWidget(mp_add_btn)

        mp_remove_btn = QPushButton(translate("SettingsDialog", "Remove"))
        mp_remove_btn.clicked.connect(self._remove_model_param)
        mp_btn_layout.addWidget(mp_remove_btn)

        mp_defaults_btn = QPushButton(translate("SettingsDialog", "Load Defaults"))
        mp_defaults_btn.setToolTip(
            translate("SettingsDialog",
                      "Load recommended parameters for the current provider"))
        mp_defaults_btn.clicked.connect(self._load_default_model_params)
        mp_btn_layout.addWidget(mp_defaults_btn)

        mp_btn_layout.addStretch()
        model_params_layout.addLayout(mp_btn_layout)

        model_params_group.setLayout(model_params_layout)
        layout.addWidget(model_params_group)

        self.behavior_page = BehaviorPage()
        layout.addWidget(self.behavior_page)

        self.tools_page = ToolsPage()
        layout.addWidget(self.tools_page)
        self.tools_page.closeHostRequested.connect(self._on_close_requested)

        # Test button — probes the reranker's resolved profile (the rerank
        # utility dropdown's selection, or the active profile when it is
        # left on "inherit") without waiting for the user to send a real
        # message. The reranker's connection is a profile now, not a
        # bespoke override group — see the Utility models group above.
        # Stays here, directly under the page, until Task 7 moves it to
        # ProviderPage alongside Test Connection.
        rerank_test_layout = QHBoxLayout()
        self._rerank_test_btn = QPushButton(
            translate("SettingsDialog", "Test Reranker"))
        self._rerank_test_btn.setToolTip(
            translate("SettingsDialog",
                      "Send a small test prompt to the reranker LLM using\n"
                      "its resolved profile. Reports success or the exact\n"
                      "error from the provider — useful for diagnosing 4xx\n"
                      "errors, timeouts, or unparseable responses."))
        self._rerank_test_btn.clicked.connect(self._test_reranker)
        rerank_test_layout.addWidget(self._rerank_test_btn)
        self._rerank_test_status = QLabel()
        self._rerank_test_status.setWordWrap(True)
        self._rerank_test_status.setStyleSheet("color: #666;")
        rerank_test_layout.addWidget(self._rerank_test_status, 1)
        layout.addLayout(rerank_test_layout)

        self.mcp_page = McpPage()
        layout.addWidget(self.mcp_page)

        # Test connection (outside scroll area)
        test_layout = QHBoxLayout()
        self.test_btn = QPushButton(translate("SettingsDialog", "Test Connection"))
        self.test_btn.clicked.connect(self._test_connection)
        test_layout.addWidget(self.test_btn)

        self.test_status = QLabel()
        self.test_status.setWordWrap(True)
        test_layout.addWidget(self.test_status, 1)

        outer_layout.addLayout(test_layout)

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
        self.provider_section.load(cfg)

        self.behavior_page.load(cfg)

        self.mcp_page.load(cfg)
        self.tools_page.load(cfg)

    # ── Connection profiles ─────────────────────────────────────

    def _on_profile_shown(self, prof):
        """The section showed a profile: load its params into the table."""
        self._load_model_params_table(prof.model, self._cfg, prof)

    def _on_about_to_commit(self, prof):
        """The section is committing a profile: the table is its params.

        A straight write-back, so a removed row is a removed parameter. Do
        not reintroduce a merge with cfg.model_params here: that shared
        layer is legacy and unread, and layering it back in would make
        Remove a no-op again.
        """
        prof.params = self._read_model_params_table()

    def _on_preset_applied(self, preset):
        """A user provider switch: reload the params table from the working copy.

        The working-copy profile, not the singleton: a vendor switch keeps
        the parameters this profile already states, and falls back to the
        new preset's default_params only when it states none.
        """
        section = self.provider_section
        self._load_model_params_table(
            section.model_edit.text(), self._cfg, section.current_profile())

    # ── Model Parameters table helpers ─────────────────────────

    def _on_model_changed(self, new_model: str):
        """Stash the edited table on the working-copy profile, load the new model's."""
        if new_model == self._last_model_name or not new_model:
            return
        # Stash current table on the working-copy profile (never the live
        # singleton — cfg.model_params is read-only from this dialog).
        prof = self.provider_section.current_profile()
        if self._last_model_name and prof is not None:
            params = self._read_model_params_table()
            if params:
                prof.params = params
        self._load_model_params_table(new_model, self._cfg, prof)

    def _load_model_params_table(self, model_name: str, cfg=None, profile=None):
        """Populate the params table with what resolve_params() will send.

        That is the profile's own params and nothing else — the legacy
        cfg.model_params dict is not read here or in create_client, so a
        row removed from this table stays removed. When the profile states
        nothing, seed the table from the provider preset's default_params,
        and failing that from the global temperature.
        """
        if cfg is None:
            cfg = get_config()
        if profile is None:
            profile = self.provider_section.current_profile()

        params = dict(profile.params) if profile is not None else {}

        if not params:
            # No saved params — try provider defaults
            provider_name = self.provider_section.current_provider_name()
            preset = PROVIDER_PRESETS.get(provider_name, {})
            params = dict(preset.get("default_params", {}))
        if not params:
            # Fallback: just temperature from global config
            params = {"temperature": cfg.temperature}

        self._last_model_name = model_name
        self._populate_model_params_table(params)

    def _populate_model_params_table(self, params: dict):
        """Fill the table widget from a params dict."""
        self.model_params_table.setRowCount(0)
        for key, value in params.items():
            row = self.model_params_table.rowCount()
            self.model_params_table.insertRow(row)
            self.model_params_table.setItem(row, 0, QTableWidgetItem(str(key)))
            self.model_params_table.setItem(row, 1, QTableWidgetItem(str(value)))

    def _read_model_params_table(self) -> dict:
        """Read the current params table into a dict, auto-casting values."""
        params = {}
        for row in range(self.model_params_table.rowCount()):
            key_item = self.model_params_table.item(row, 0)
            val_item = self.model_params_table.item(row, 1)
            if not key_item or not val_item:
                continue
            key = key_item.text().strip()
            val_str = val_item.text().strip()
            if not key:
                continue
            # Auto-cast value: try int, then float, then keep as string
            try:
                # Distinguish int from float: "64" → int, "0.95" → float
                if "." in val_str or "e" in val_str.lower():
                    params[key] = float(val_str)
                else:
                    params[key] = int(val_str)
            except ValueError:
                # Boolean or string
                if val_str.lower() in ("true", "false"):
                    params[key] = val_str.lower() == "true"
                else:
                    params[key] = val_str
        return params

    def _add_model_param(self):
        """Add an empty row to the model params table."""
        row = self.model_params_table.rowCount()
        self.model_params_table.insertRow(row)
        self.model_params_table.setItem(row, 0, QTableWidgetItem(""))
        self.model_params_table.setItem(row, 1, QTableWidgetItem(""))
        self.model_params_table.editItem(self.model_params_table.item(row, 0))

    def _remove_model_param(self):
        """Remove the selected row from the model params table."""
        row = self.model_params_table.currentRow()
        if row >= 0:
            self.model_params_table.removeRow(row)

    def _load_default_model_params(self):
        """Reset the params table to provider defaults."""
        provider_name = self.provider_section.current_provider_name()
        preset = PROVIDER_PRESETS.get(provider_name, {})
        params = dict(preset.get("default_params", {}))
        if not params:
            params = {"temperature": 0.3}
        self._populate_model_params_table(params)

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
        # itself is written back near the end, via section.apply_to(cfg),
        # after the rerank widgets below so its #10 factory-default check
        # sees this save's own values.
        section = self.provider_section
        section.commit()
        if not self._confirm_incomplete_profiles(section.profiles()):
            return

        # Model params reach the profile via section.commit() above (its
        # aboutToCommit slot sets prof.params = the table, in full) —
        # cfg.model_params is legacy and is neither read nor written here.
        model_name = section.model_edit.text().strip()
        if model_name:
            params = self._read_model_params_table()
            # Keep global temperature in sync for backward compat —
            # cfg.temperature is still the job-level fallback create_client
            # passes when a profile states no temperature.
            cfg.temperature = params.get("temperature", cfg.temperature)

        self.behavior_page.apply_to(cfg)
        self.mcp_page.apply_to(cfg)
        self.tools_page.apply_to(cfg)

        # After the widget writes above, so the section's #10 factory-
        # default check (in apply_to) sees this save's own rerank values.
        section.apply_to(cfg)

        save_current_config()

        # The chat panel (and anything else showing config-derived state)
        # refreshes from this, whichever window saved (#99).
        notify_config_changed()

        self.behavior_page.after_save(cfg)

        self.accept()

    @staticmethod
    def _probe_running_text(label: str) -> str:
        """Status text while a probe runs, naming the profile under test.

        Both probes routinely test a profile that is not the active one —
        Test Connection tests whatever is on screen, Test Reranker follows
        the rerank utility dropdown — so the row has to say which.
        """
        if not label:
            return translate("SettingsDialog", "Testing...")
        return translate("SettingsDialog", 'Testing "{}"...').format(label)

    @staticmethod
    def _probe_result_text(label: str, message: str) -> str:
        """Prefix a finished probe's message with the profile it tested."""
        if not label:
            return message
        return '"{}": {}'.format(label, message)

    def _test_reranker(self):
        """Send a small probe prompt to the reranker LLM using its resolved
        profile — the rerank utility dropdown's selection, falling back to
        the active profile when it is left on "inherit".

        Surfaces success or the exact error so the user can debug a broken
        reranker config (HTTP 4xx, auth failure, timeouts, hallucinations)
        without sending a real chat message and parsing the Report View.

        Mirrors _test_connection: resolve through the same helpers runtime
        uses (create_client's own resolve_profile/resolve_params), so this
        probe cannot drift from what Act mode will actually build.
        """
        # An in-progress edit on the visible profile should be what gets
        # probed, not whatever was last committed.
        section = self.provider_section
        section.commit()
        label = section.utility_selection("rerank")
        if label not in section.profiles():
            label = section.active_label()
        profile = section.profiles().get(label)
        # Captured now, so switching profiles mid-probe cannot relabel the
        # result that comes back.
        self._rerank_test_profile_label = label
        if profile is None:
            self._rerank_test_status.setText(translate(
                "SettingsDialog", "No profile configured"))
            self._rerank_test_status.setStyleSheet("color: #c62828;")
            return

        from ..llm.client import resolve_params
        base_url = profile.base_url
        # Matches create_client()'s fallback exactly (see _test_connection).
        api_key = profile.api_key or self._cfg.provider_keys.get(
            profile.name, "")
        model = profile.model
        model_params = resolve_params(self._cfg, profile)

        self._rerank_test_btn.setEnabled(False)
        self._rerank_test_status.setText(
            self._probe_running_text(label))
        self._rerank_test_status.setStyleSheet("color: #666;")

        self._rerank_test_thread = _TestRerankerThread(
            profile.name, base_url, api_key, model, model_params, self,
        )
        self._rerank_test_thread.finished.connect(
            self._on_rerank_test_finished)
        self._rerank_test_thread.start()

    def _on_rerank_test_finished(self, success: bool, message: str):
        """Render the reranker test outcome in the status label."""
        self._rerank_test_btn.setEnabled(True)
        label = getattr(self, "_rerank_test_profile_label", "")
        if success:
            self._rerank_test_status.setText(self._probe_result_text(
                label, translate("SettingsDialog", "OK") + " — " + message))
            self._rerank_test_status.setStyleSheet("color: #2e7d32;")
        else:
            self._rerank_test_status.setText(self._probe_result_text(
                label, translate("SettingsDialog", "Error") + ": " + message))
            self._rerank_test_status.setStyleSheet("color: #c62828;")

    def _test_connection(self):
        """Test the LLM connection in a background thread."""
        # Never through cfg: the visible profile may not be
        # cfg.active_profile (e.g. a profile added but not yet saved), and
        # writing it into the singleton would smuggle it into the wrong
        # profile. The Behavior-tab values below travel the same way, for
        # the second half of the same reason: nothing rolls a singleton
        # write back when the user hits Cancel (#76).
        # Commit first, then probe the committed profile: an edit in
        # progress is what gets tested, and nothing outside the section's
        # own copy is written (#76). Committing first also keeps the probe
        # result: a commit *after* it would see the retyped model and
        # drop the answer as stale.
        section = self.provider_section
        section.commit()
        profile = section.current_profile()
        provider_name = profile.name
        base_url = profile.base_url
        # Match create_client()'s fallback: an explicit key on the profile
        # wins, else the vendor-wide default in provider_keys.
        api_key = profile.api_key or \
            self._cfg.provider_keys.get(provider_name, "")
        model = profile.model
        model_params = dict(profile.params)

        self.test_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        # Captured now, so switching profiles mid-probe cannot relabel the
        # result that comes back.
        self._test_profile_label = section.current_label()
        self.test_status.setText(
            self._probe_running_text(self._test_profile_label))
        self.test_status.setStyleSheet("color: #666;")

        self._test_thread = _TestConnectionThread(
            provider_name, base_url, api_key, model, model_params,
            max_tokens=self._cfg.max_tokens,
            # The params table supplies the dialog's own temperature and
            # outranks this inside LLMClient; cfg is only the fallback.
            temperature=self._cfg.temperature,
            thinking=self._cfg.thinking,
            parent=self,
        )
        self._test_thread.finished.connect(self._on_test_finished)
        self._test_thread.vision_result.connect(self._on_vision_probed)
        self._test_thread.capabilities_result.connect(self._on_capabilities_detected)
        self._test_thread.start()

    def _on_test_finished(self, success, message):
        """Handle test connection result."""
        label = getattr(self, "_test_profile_label", "")
        if success:
            # Keep buttons disabled — vision probe is still running
            self.test_status.setText(self._probe_result_text(label, message))
            self.test_status.setStyleSheet("color: #2e7d32;")
        else:
            # No vision probe on failure — re-enable buttons now
            self.test_btn.setEnabled(True)
            self.save_btn.setEnabled(True)
            self.cancel_btn.setEnabled(True)
            self.test_status.setText(self._probe_result_text(
                label, translate("SettingsDialog", "Failed: ") + message))
            self.test_status.setStyleSheet("color: #c62828;")

    def _on_vision_probed(self, supports_vision: bool):
        """Handle vision probe result — records it on the probed profile.

        Test Connection probes whichever profile is on screen, which need
        not be the active one, so the answer belongs to that profile and
        nowhere else. It lands in the ProviderSection's working copy like
        every other profile field and reaches the config on OK.
        """
        self.test_btn.setEnabled(True)
        self.save_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.provider_section.set_probe_result(
            getattr(self, "_test_profile_label", None),
            vision=supports_vision)
        # Append vision status to test output
        current = self.test_status.text()
        if supports_vision:
            vision_msg = translate("SettingsDialog", "Vision: supported")
        else:
            vision_msg = translate("SettingsDialog", "Vision: not supported")
        self.test_status.setText(current + "\n" + vision_msg)
        # Log to FreeCAD console
        try:
            import FreeCAD
            FreeCAD.Console.PrintMessage(f"FreeCAD AI: {vision_msg}\n")
        except ImportError:
            pass

    def _on_capabilities_detected(self, caps: dict):
        """Handle full capabilities dict (Ollama only emits tools/thinking).

        Records tools_detected/thinking_detected on the probed profile and
        appends a readable summary to the test status. Non-Ollama providers
        emit only "vision" — tools/thinking stay None to keep falling back
        to the provider-wide static flag.
        """
        self.provider_section.set_probe_result(
            getattr(self, "_test_profile_label", None),
            **{k: bool(caps[k]) for k in ("tools", "thinking") if k in caps})

        # Build a single-line summary for the status label
        parts = []
        if "tools" in caps:
            parts.append(f"tools: {'yes' if caps['tools'] else 'no'}")
        if "thinking" in caps:
            parts.append(f"thinking: {'yes' if caps['thinking'] else 'no'}")
        if parts:
            line = translate("SettingsDialog", "Capabilities: ") + ", ".join(parts)
            self.test_status.setText(self.test_status.text() + "\n" + line)
            try:
                import FreeCAD
                FreeCAD.Console.PrintMessage(f"FreeCAD AI: {line}\n")
            except ImportError:
                pass

