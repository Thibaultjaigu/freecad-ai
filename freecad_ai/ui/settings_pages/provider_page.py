"""Provider page: the provider section, its parameter table, and the two
connection probes (Test Connection, Test Reranker) (#101)."""

from ..compat import QtWidgets, QtCore
from ...i18n import translate
from ...config import get_config, PROVIDER_PRESETS
from ..provider_section import ProviderSection
from .base import SettingsPage

QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QGroupBox = QtWidgets.QGroupBox
QPushButton = QtWidgets.QPushButton
QLabel = QtWidgets.QLabel
Signal = QtCore.Signal
QThread = QtCore.QThread
QTableWidget = QtWidgets.QTableWidget
QTableWidgetItem = QtWidgets.QTableWidgetItem
QHeaderView = QtWidgets.QHeaderView


class _TestConnectionThread(QThread):
    """Background thread for testing LLM connection and detecting capabilities.

    Takes everything it needs as arguments rather than reading config — so
    the user can test before saving, and so a profile that isn't
    cfg.active_profile can't have its values smuggled into the active one
    through the singleton (see _TestRerankerThread).

    max_tokens and thinking are the saved values: those widgets live on the
    Behavior page, and a probe reading another page's widgets would link
    pages (#101). temperature is the saved value: the model-params table
    carries the dialog's own, and LLMClient lets it win over this fallback.
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
            from ...llm.client import LLMClient
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
            from ...llm.client import LLMClient
            from ...tools.reranker import rerank_tools_llm
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


class ProviderPage(SettingsPage):
    """LLM Provider + Utility models, the Model Parameters table, and the
    two connection probes (Test Connection, Test Reranker)."""

    busyChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_model_name = ""  # track model name for param save/load
        self._cfg = None
        # What the table showed right after the last load — which, for a
        # profile with no params of its own, is a *preview* of what
        # resolve_params() would send (provider defaults or the global
        # temperature), not the profile's own state. Comparing against it
        # is how commit tells an actual edit from that unedited preview.
        self._loaded_params = {}
        # The running probes, held until their last signal (see
        # _release_thread).
        self._test_thread = None
        self._rerank_test_thread = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # LLM Provider + Utility models: shared with Edit → Preferences
        # (#99). The page-only parts ride on its signals.
        self.section = ProviderSection()
        self.section.profileShown.connect(self._on_profile_shown)
        self.section.aboutToCommit.connect(self._on_about_to_commit)
        self.section.presetApplied.connect(self._on_preset_applied)
        self.section.modelChanged.connect(self._on_model_changed)
        layout.addWidget(self.section)

        # Test Reranker — appended to the section's own Utility models
        # group, directly under the rerank dropdown it probes (#101).
        rerank_row = QWidget()
        rerank_row_layout = QHBoxLayout(rerank_row)
        rerank_row_layout.setContentsMargins(0, 0, 0, 0)
        self._rerank_test_btn = QPushButton(
            translate("SettingsDialog", "Test Reranker"))
        self._rerank_test_btn.setToolTip(
            translate("SettingsDialog",
                      "Send a small test prompt to the reranker LLM using\n"
                      "its resolved profile. Reports success or the exact\n"
                      "error from the provider — useful for diagnosing 4xx\n"
                      "errors, timeouts, or unparseable responses."))
        self._rerank_test_btn.clicked.connect(self._test_reranker)
        rerank_row_layout.addWidget(self._rerank_test_btn)
        self._rerank_test_status = QLabel()
        self._rerank_test_status.setWordWrap(True)
        self._rerank_test_status.setStyleSheet("color: #666;")
        rerank_row_layout.addWidget(self._rerank_test_status, 1)
        self.section.utility_group.layout().addRow("", rerank_row)

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

        # Test Connection
        test_layout = QHBoxLayout()
        self.test_btn = QPushButton(translate("SettingsDialog", "Test Connection"))
        self.test_btn.clicked.connect(self._test_connection)
        test_layout.addWidget(self.test_btn)

        self.test_status = QLabel()
        self.test_status.setWordWrap(True)
        test_layout.addWidget(self.test_status, 1)

        layout.addLayout(test_layout)

    def load(self, cfg, label=None):
        self._cfg = cfg
        super().load(cfg, label)

    def _show(self, cfg, label):
        # Profile edits stay in the section's own copy until save (see
        # ProviderSection.load for why the live singleton must not see them).
        self.section.load(cfg, label=label)

    # The section keeps its own baseline (profiles, active, utilities); the
    # params table is part of the profile and commits through aboutToCommit.
    def is_dirty(self):
        return self._baseline is not None and self.section.is_dirty()

    def apply_to(self, cfg):
        if not self.is_dirty():
            return
        self.section.apply_to(cfg)
        # cfg.temperature is still the job-level fallback create_client
        # passes when a profile states no temperature; keep it in step.
        if self.section.model_edit.text().strip():
            cfg.temperature = self._read_model_params_table().get(
                "temperature", cfg.temperature)

    def view_label(self):
        return self.section.current_label()

    # ── Connection profiles ─────────────────────────────────────

    def _on_profile_shown(self, prof):
        """The section showed a profile: load its params into the table."""
        self._load_model_params_table(prof.model, self._cfg, prof)

    def _on_about_to_commit(self, prof):
        """The section is committing a profile: the table is its params.

        A straight write-back when the table has actually changed since it
        was loaded, so a removed row is a removed parameter. Do not
        reintroduce a merge with cfg.model_params here: that shared layer
        is legacy and unread, and layering it back in would make Remove a
        no-op again. When nothing changed, leave prof.params alone — for a
        profile with none of its own, the table was only ever previewing
        provider defaults or the global temperature, and committing that
        untouched preview would silently promote it into an explicit
        per-profile override.
        """
        current = self._read_model_params_table()
        if current != self._loaded_params:
            prof.params = current

    def _on_preset_applied(self, preset):
        """A user provider switch: reload the params table from the working copy.

        The working-copy profile, not the singleton: a vendor switch keeps
        the parameters this profile already states, and falls back to the
        new preset's default_params only when it states none.
        """
        section = self.section
        self._load_model_params_table(
            section.model_edit.text(), self._cfg, section.current_profile())

    # ── Model Parameters table helpers ─────────────────────────

    def _on_model_changed(self, new_model: str):
        """Stash the edited table on the working-copy profile, load the new model's."""
        if new_model == self._last_model_name or not new_model:
            return
        # Stash current table on the working-copy profile (never the live
        # singleton — cfg.model_params is read-only from this dialog).
        prof = self.section.current_profile()
        if self._last_model_name and prof is not None:
            params = self._read_model_params_table()
            if params != self._loaded_params:
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
            profile = self.section.current_profile()

        params = dict(profile.params) if profile is not None else {}

        if not params:
            # No saved params — try provider defaults
            provider_name = self.section.current_provider_name()
            preset = PROVIDER_PRESETS.get(provider_name, {})
            params = dict(preset.get("default_params", {}))
        if not params:
            # Fallback: just temperature from global config
            params = {"temperature": cfg.temperature}

        self._last_model_name = model_name
        self._loaded_params = params
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
        provider_name = self.section.current_provider_name()
        preset = PROVIDER_PRESETS.get(provider_name, {})
        params = dict(preset.get("default_params", {}))
        if not params:
            params = {"temperature": 0.3}
        self._populate_model_params_table(params)

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
        section = self.section
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

        from ...llm.client import resolve_params
        base_url = profile.base_url
        # Matches create_client()'s fallback exactly (see _test_connection).
        api_key = profile.api_key or get_config().provider_keys.get(
            profile.name, "")
        model = profile.model
        model_params = resolve_params(get_config(), profile)

        self._rerank_test_btn.setEnabled(False)
        self._rerank_test_status.setText(
            self._probe_running_text(label))
        self._rerank_test_status.setStyleSheet("color: #666;")

        self._rerank_test_thread = _TestRerankerThread(
            profile.name, base_url, api_key, model, model_params,
            parent=QtWidgets.QApplication.instance(),
        )
        self._rerank_test_thread.finished.connect(
            self._on_rerank_test_finished)
        self._rerank_test_thread.start()

    def _release_thread(self, attr):
        """Free a probe thread once its last signal has arrived.

        Called only from the slot for the last emit in run(), so wait()
        covers just run()'s return. The page's reference is cleared last:
        it is what keeps the Python wrapper, and so the thread, alive
        until then. Each click otherwise leaked the thread (parented to
        the QApplication) and the LLM client it held.
        """
        thread = getattr(self, attr)
        if thread is None:
            return
        thread.wait()
        thread.deleteLater()
        setattr(self, attr, None)

    def _on_rerank_test_finished(self, success: bool, message: str):
        """Render the reranker test outcome in the status label."""
        # Every path through _TestRerankerThread.run() ends in this emit.
        self._release_thread("_rerank_test_thread")
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
        section = self.section
        section.commit()
        profile = section.current_profile()
        provider_name = profile.name
        base_url = profile.base_url
        # Match create_client()'s fallback: an explicit key on the profile
        # wins, else the vendor-wide default in provider_keys.
        api_key = profile.api_key or \
            get_config().provider_keys.get(provider_name, "")
        model = profile.model
        model_params = dict(profile.params)

        self.test_btn.setEnabled(False)
        self.busyChanged.emit(True)
        # Captured now, so switching profiles mid-probe cannot relabel the
        # result that comes back.
        self._test_profile_label = section.current_label()
        self.test_status.setText(
            self._probe_running_text(self._test_profile_label))
        self.test_status.setStyleSheet("color: #666;")

        self._test_thread = _TestConnectionThread(
            provider_name, base_url, api_key, model, model_params,
            max_tokens=get_config().max_tokens,
            # The params table supplies the dialog's own temperature and
            # outranks this inside LLMClient; cfg is only the fallback.
            temperature=get_config().temperature,
            thinking=get_config().thinking,
            parent=QtWidgets.QApplication.instance(),
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
            # A failure is run()'s last emit; on success the capability
            # probe follows, and _on_capabilities_detected releases it.
            self._release_thread("_test_thread")
            # No vision probe on failure — re-enable buttons now
            self.test_btn.setEnabled(True)
            self.busyChanged.emit(False)
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
        self.busyChanged.emit(False)
        self.section.set_probe_result(
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
        # The last emit of a successful _TestConnectionThread.run().
        self._release_thread("_test_thread")
        self.section.set_probe_result(
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
