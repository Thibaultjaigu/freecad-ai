"""MCP page: servers the workbench connects to, and its own MCP server."""

import secrets

from ..compat import QtWidgets, QtGui
from ...i18n import translate
from .base import SettingsPage

QDialog = QtWidgets.QDialog
QWidget = QtWidgets.QWidget
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QFormLayout = QtWidgets.QFormLayout
QGroupBox = QtWidgets.QGroupBox
QComboBox = QtWidgets.QComboBox
QLineEdit = QtWidgets.QLineEdit
QSpinBox = QtWidgets.QSpinBox
QCheckBox = QtWidgets.QCheckBox
QPushButton = QtWidgets.QPushButton
QLabel = QtWidgets.QLabel
QIntValidator = QtGui.QIntValidator
QTableWidget = QtWidgets.QTableWidget
QTableWidgetItem = QtWidgets.QTableWidgetItem

QListWidget = QtWidgets.QListWidget
QListWidgetItem = QtWidgets.QListWidgetItem


class McpPage(SettingsPage):
    """MCP servers the workbench connects to, and the built-in MCP server."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mcp_configs = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # MCP Servers group
        mcp_group = QGroupBox(translate("SettingsDialog", "MCP Servers"))
        mcp_layout = QVBoxLayout()

        self.mcp_list = QListWidget()
        self.mcp_list.setMaximumHeight(100)
        mcp_layout.addWidget(self.mcp_list)

        self.mcp_list.itemDoubleClicked.connect(self._edit_mcp_server)

        mcp_btn_layout = QHBoxLayout()
        add_mcp_btn = QPushButton(translate("SettingsDialog", "Add..."))
        add_mcp_btn.clicked.connect(self._add_mcp_server)
        mcp_btn_layout.addWidget(add_mcp_btn)

        edit_mcp_btn = QPushButton(translate("SettingsDialog", "Edit..."))
        edit_mcp_btn.clicked.connect(self._edit_mcp_server)
        mcp_btn_layout.addWidget(edit_mcp_btn)

        remove_mcp_btn = QPushButton(translate("SettingsDialog", "Remove"))
        remove_mcp_btn.clicked.connect(self._remove_mcp_server)
        mcp_btn_layout.addWidget(remove_mcp_btn)

        mcp_btn_layout.addStretch()
        mcp_layout.addLayout(mcp_btn_layout)

        mcp_group.setLayout(mcp_layout)
        layout.addWidget(mcp_group)

        # Built-in MCP Server group
        server_group = QGroupBox(translate("SettingsDialog", "Built-in MCP Server"))
        server_group_layout = QVBoxLayout()

        # Address this addon listens on when acting AS an MCP server.
        # A plain numeric entry, not a spinbox: no artificial 1024 floor, so
        # the GUI reaches exactly the ports MCP_PORT does. A privileged port
        # fails at bind time with a real message instead of being unreachable.
        server_form = QFormLayout()

        self.mcp_server_host_edit = QLineEdit()
        self.mcp_server_host_edit.setPlaceholderText("127.0.0.1")
        server_form.addRow(
            translate("SettingsDialog", "Server host:"),
            self.mcp_server_host_edit)

        self.mcp_server_port_edit = QLineEdit()
        self.mcp_server_port_edit.setValidator(QIntValidator(1, 65535, self))
        self.mcp_server_port_edit.setPlaceholderText("3000")
        server_form.addRow(
            translate("SettingsDialog", "Server port:"),
            self.mcp_server_port_edit)

        # Host headers the server answers to. Empty means the transport's own
        # loopback default, which is also what keeps a wildcard bind refused
        # rather than silently 403-ing every client (#60). Naming hosts here
        # is the opt-in that makes a non-loopback bind reachable.
        self.mcp_server_allowed_hosts_edit = QLineEdit()
        self.mcp_server_allowed_hosts_edit.setPlaceholderText(
            "127.0.0.1, localhost, ::1")
        server_form.addRow(
            translate("SettingsDialog", "Allowed Host headers:"),
            self.mcp_server_allowed_hosts_edit)

        # Optional bearer token (#59). Empty (the default) leaves the server
        # unauthenticated, exactly as before this field existed. "Generate"
        # fills in a fresh secrets.token_urlsafe(32) value on demand.
        auth_token_row = QWidget()
        auth_token_row_layout = QHBoxLayout()
        auth_token_row_layout.setContentsMargins(0, 0, 0, 0)
        self.mcp_server_auth_token_edit = QLineEdit()
        self.mcp_server_auth_token_edit.setPlaceholderText(
            translate("SettingsDialog", "none — authentication disabled"))
        auth_token_row_layout.addWidget(self.mcp_server_auth_token_edit)
        generate_token_btn = QPushButton(translate("SettingsDialog", "Generate"))
        generate_token_btn.clicked.connect(self._generate_mcp_auth_token)
        auth_token_row_layout.addWidget(generate_token_btn)
        clear_token_btn = QPushButton(translate("SettingsDialog", "Clear"))
        clear_token_btn.clicked.connect(
            lambda: self.mcp_server_auth_token_edit.setText(""))
        auth_token_row_layout.addWidget(clear_token_btn)
        auth_token_row.setLayout(auth_token_row_layout)
        server_form.addRow(
            translate("SettingsDialog", "Bearer token:"), auth_token_row)

        server_group_layout.addLayout(server_form)

        # Unconditional, not shown only for non-loopback values: the loopback
        # default is already reachable by every local process, so hiding the
        # warning there would imply the default is authenticated. It is not,
        # unless a bearer token above is set.
        mcp_server_warning = QLabel(translate(
            "SettingsDialog",
            "Without a bearer token, anything that can reach this address "
            "can run FreeCAD tools, including arbitrary Python. Keep it on "
            "127.0.0.1 unless you understand the exposure, or set a bearer "
            "token above.\n\n"
            "Leave Allowed Host headers empty for loopback-only access. "
            "Listing hosts is what makes a non-loopback bind reachable, so "
            "name only the addresses clients actually dial. \"*\" is not "
            "accepted — without a token, this list is the only thing "
            "limiting who can reach the server.\n\n"
            "Host, port, allowed hosts, and the bearer token only take "
            "effect the next time the MCP server starts. Saving here does "
            "not reconfigure one that is already running."))
        mcp_server_warning.setWordWrap(True)
        server_group_layout.addWidget(mcp_server_warning)

        server_group.setLayout(server_group_layout)
        layout.addWidget(server_group)

    def _show(self, cfg, label):
        self.mcp_list.clear()
        self._mcp_configs = [dict(e) for e in cfg.mcp_servers]
        for entry in self._mcp_configs:
            self.mcp_list.addItem(self._mcp_list_label(entry))
        self.mcp_server_host_edit.setText(cfg.mcp_server_host)
        self.mcp_server_port_edit.setText(str(cfg.mcp_server_port))
        self.mcp_server_allowed_hosts_edit.setText(
            ", ".join(cfg.mcp_server_allowed_hosts or []))
        self.mcp_server_auth_token_edit.setText(cfg.mcp_server_auth_token or "")

    def _values(self):
        # Parsed, so re-typing the same host is not a change.
        host, port = self._parse_server_address(
            self.mcp_server_host_edit.text(), self.mcp_server_port_edit.text())
        return {
            "mcp_servers": [dict(e) for e in self._mcp_configs],
            "mcp_server_host": host,
            "mcp_server_port": port,
            "mcp_server_allowed_hosts": self._parse_allowed_hosts(
                self.mcp_server_allowed_hosts_edit.text()),
            "mcp_server_auth_token":
                self.mcp_server_auth_token_edit.text().strip(),
        }

    @staticmethod
    def _mcp_list_label(entry: dict) -> str:
        """Build display label for an MCP server entry."""
        tags = []
        if not entry.get("enabled", True):
            tags.append("disabled")
        if entry.get("deferred", True):
            tags.append("deferred")
        timeout = int(entry.get("timeout", 600))
        if timeout != 600:
            tags.append(f"{timeout}s")
        prefix = f"({', '.join(tags)}) " if tags else ""
        transport = entry.get("transport", "stdio")
        if transport in ("sse", "http"):
            target = f"[{transport}] {entry.get('url', '')}"
        else:
            args = " ".join(entry.get("args", []))
            target = f"{entry.get('command', '')} {args}".strip()
        return f"{prefix}{entry.get('name', '?')} — {target}"

    @staticmethod
    def _parse_allowed_hosts(hosts_text):
        """Normalise the allowed-Host-headers field into a list.

        Empty means "let the transport pick its own default" — loopback, and
        with it the wildcard-bind rejection that keeps #60 from returning.

        A "*" entry is dropped rather than raised on, because the dialog must
        always be closable. The warning under the field is what explains why;
        the env-var path (resolve_allowed_hosts) refuses it loudly instead,
        since a traceback is the only feedback available there.
        """
        return [h.strip() for h in (hosts_text or "").split(",")
                if h.strip() and h.strip() != "*"]

    @staticmethod
    def _parse_server_address(host_text, port_text):
        """Normalise the MCP server host/port fields into (host, port).

        Anything unusable falls back to the default rather than raising: the
        dialog must always be closable. The validator already blocks
        out-of-range typing, so this is the belt to its braces.
        """
        from ...mcp.gui_server import DEFAULT_HOST, DEFAULT_PORT
        host = (host_text or "").strip() or DEFAULT_HOST
        try:
            port = int((port_text or "").strip())
        except ValueError:
            return host, DEFAULT_PORT
        if not 1 <= port <= 65535:
            return host, DEFAULT_PORT
        return host, port

    def _generate_mcp_auth_token(self):
        """Fill the bearer-token field with a fresh random token.

        Same shape the issue proposes (``secrets.token_urlsafe(32)``), run on
        demand rather than automatically on every server start: an
        auto-generated token would change on each restart with no stable
        place for the user to read it back from, whereas this field is that
        place: it stays whatever the user last set until they change it.
        """
        self.mcp_server_auth_token_edit.setText(secrets.token_urlsafe(32))

    def _add_mcp_server(self):
        """Show a dialog to add a new MCP server configuration."""
        dlg = _AddMCPServerDialog(self)
        if dlg.exec():
            entry = dlg.get_config()
            if not hasattr(self, "_mcp_configs"):
                self._mcp_configs = []
            self._mcp_configs.append(entry)
            self.mcp_list.addItem(self._mcp_list_label(entry))

    def _edit_mcp_server(self):
        """Edit the selected MCP server configuration."""
        row = self.mcp_list.currentRow()
        if row < 0 or not hasattr(self, "_mcp_configs") or row >= len(self._mcp_configs):
            return
        existing = self._mcp_configs[row]
        dlg = _AddMCPServerDialog(self, existing=existing)
        if dlg.exec():
            updated = dlg.get_config()
            self._mcp_configs[row] = updated
            self.mcp_list.item(row).setText(self._mcp_list_label(updated))

    def _remove_mcp_server(self):
        """Remove the selected MCP server from the list."""
        row = self.mcp_list.currentRow()
        if row >= 0 and hasattr(self, "_mcp_configs"):
            self.mcp_list.takeItem(row)
            if row < len(self._mcp_configs):
                self._mcp_configs.pop(row)


class _AddMCPServerDialog(QDialog):
    """Dialog for adding or editing an MCP server configuration."""

    def __init__(self, parent=None, existing: dict | None = None):
        super().__init__(parent)
        editing = existing is not None
        self.setWindowTitle(
            translate("AddMCPServerDialog", "Edit MCP Server") if editing
            else translate("AddMCPServerDialog", "Add MCP Server")
        )
        self.setMinimumWidth(400)
        self._build_ui(editing)
        if existing:
            self._populate(existing)

    def _build_ui(self, editing=False):
        layout = QFormLayout(self)

        self.transport_combo = QComboBox()
        self.transport_combo.addItem(
            translate("AddMCPServerDialog", "Command (stdio)"), "stdio")
        self.transport_combo.addItem(
            translate("AddMCPServerDialog", "SSE (URL)"), "sse")
        self.transport_combo.addItem(
            translate("AddMCPServerDialog", "Streamable HTTP (URL)"), "http")
        self.transport_combo.currentIndexChanged.connect(
            lambda _=0: self._apply_transport_visibility(
                self.transport_combo.currentData()))
        layout.addRow(translate("AddMCPServerDialog", "Transport:"),
                      self.transport_combo)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "e.g. filesystem"))
        layout.addRow(translate("AddMCPServerDialog", "Name:"), self.name_edit)

        # --- stdio group ---
        self._stdio_widget = QWidget()
        stdio_form = QFormLayout(self._stdio_widget)
        stdio_form.setContentsMargins(0, 0, 0, 0)
        self.command_edit = QLineEdit()
        self.command_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "e.g. npx"))
        stdio_form.addRow(translate("AddMCPServerDialog", "Command:"),
                          self.command_edit)
        self.args_edit = QLineEdit()
        self.args_edit.setPlaceholderText(translate(
            "AddMCPServerDialog", "e.g. -y @modelcontextprotocol/server-filesystem /tmp"))
        self.args_edit.setToolTip(
            translate("AddMCPServerDialog", "Space-separated arguments"))
        stdio_form.addRow(translate("AddMCPServerDialog", "Args:"), self.args_edit)
        layout.addRow(self._stdio_widget)

        # --- url group (sse / http) ---
        self._url_widget = QWidget()
        url_form = QFormLayout(self._url_widget)
        url_form.setContentsMargins(0, 0, 0, 0)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "e.g. https://host/sse"))
        url_form.addRow(translate("AddMCPServerDialog", "URL:"), self.url_edit)

        self.headers_table = QTableWidget(0, 2)
        self.headers_table.setHorizontalHeaderLabels([
            translate("AddMCPServerDialog", "Header"),
            translate("AddMCPServerDialog", "Value")])
        self.headers_table.setMaximumHeight(100)
        url_form.addRow(translate("AddMCPServerDialog", "Headers:"),
                        self.headers_table)
        headers_btns = QHBoxLayout()
        add_hdr = QPushButton(translate("AddMCPServerDialog", "Add header"))
        add_hdr.clicked.connect(
            lambda: self.headers_table.insertRow(self.headers_table.rowCount()))
        del_hdr = QPushButton(translate("AddMCPServerDialog", "Remove header"))
        del_hdr.clicked.connect(
            lambda: self.headers_table.removeRow(self.headers_table.currentRow()))
        headers_btns.addWidget(add_hdr)
        headers_btns.addWidget(del_hdr)
        headers_btns.addStretch()
        headers_wrap = QWidget()
        headers_wrap.setLayout(headers_btns)
        url_form.addRow("", headers_wrap)

        self.ca_edit = QLineEdit()
        self.ca_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "optional CA bundle path (.pem)"))
        url_form.addRow(translate("AddMCPServerDialog", "CA bundle:"), self.ca_edit)
        self.cert_edit = QLineEdit()
        self.cert_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "optional client cert path (.pem)"))
        url_form.addRow(translate("AddMCPServerDialog", "Client cert:"), self.cert_edit)
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText(
            translate("AddMCPServerDialog", "optional client key path"))
        url_form.addRow(translate("AddMCPServerDialog", "Client key:"), self.key_edit)

        self._url_warning = QLabel()
        self._url_warning.setStyleSheet("color: red;")
        self._url_warning.setWordWrap(True)
        self._url_warning.setVisible(False)
        url_form.addRow("", self._url_warning)
        layout.addRow(self._url_widget)

        # --- shared rows ---
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 3600)
        self.timeout_spin.setValue(600)
        self.timeout_spin.setSuffix(translate("AddMCPServerDialog", " s"))
        self.timeout_spin.setToolTip(
            translate("AddMCPServerDialog",
                      "Maximum time to wait for a tool call to complete.\n"
                      "Raise for slow tools (vision models, large builds).\n"
                      "Lower for fast tools where you want to fail quickly.")
        )
        layout.addRow(translate("AddMCPServerDialog", "Tool call timeout:"), self.timeout_spin)

        self.deferred_check = QCheckBox(translate("AddMCPServerDialog", "Deferred tool loading"))
        self.deferred_check.setChecked(True)
        self.deferred_check.setToolTip(
            translate("AddMCPServerDialog",
                      "Load tool schemas lazily on first use instead of\n"
                      "fetching all schemas eagerly on connect.\n"
                      "Faster startup when the server exposes many tools.")
        )
        layout.addRow("", self.deferred_check)

        self.enabled_check = QCheckBox(translate("AddMCPServerDialog", "Enabled"))
        self.enabled_check.setChecked(True)
        layout.addRow("", self.enabled_check)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        ok_label = translate("AddMCPServerDialog", "Save") if editing \
            else translate("AddMCPServerDialog", "Add")
        ok_btn = QPushButton(ok_label)
        ok_btn.clicked.connect(self._on_accept)
        btn_layout.addWidget(ok_btn)

        cancel_btn = QPushButton(translate("AddMCPServerDialog", "Cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addRow(btn_layout)

        self._apply_transport_visibility(self.transport_combo.currentData())

    def _apply_transport_visibility(self, transport):
        is_stdio = (transport == "stdio")
        self._stdio_widget.setVisible(is_stdio)
        self._url_widget.setVisible(not is_stdio)

    @staticmethod
    def _url_error_message(url):
        """Return an error string if the URL is invalid for a URL transport, else ''."""
        if not url:
            return translate("AddMCPServerDialog", "URL is required.")
        from ...mcp.client import _validate_url
        try:
            _validate_url(url)
        except ValueError as e:
            return str(e)
        return ""

    def _on_accept(self):
        """Validate a URL-transport server's URL before accepting the dialog."""
        transport = self.transport_combo.currentData()
        if transport in ("sse", "http"):
            msg = _AddMCPServerDialog._url_error_message(self.url_edit.text().strip())
            if msg:
                self._url_warning.setText(msg)
                self._url_warning.setVisible(True)
                return
        self.accept()

    def _collect_headers(self):
        headers = {}
        for row in range(self.headers_table.rowCount()):
            key_item = self.headers_table.item(row, 0)
            val_item = self.headers_table.item(row, 1)
            key = key_item.text().strip() if key_item else ""
            val = val_item.text().strip() if val_item else ""
            if key:
                headers[key] = val
        return headers

    def _populate_headers(self, headers):
        self.headers_table.setRowCount(0)
        for key, value in (headers or {}).items():
            row = self.headers_table.rowCount()
            self.headers_table.insertRow(row)
            self.headers_table.setItem(row, 0, QTableWidgetItem(str(key)))
            self.headers_table.setItem(row, 1, QTableWidgetItem(str(value)))

    def _populate(self, entry: dict):
        """Pre-populate fields from an existing MCP server config."""
        self.name_edit.setText(entry.get("name", ""))
        transport = entry.get("transport", "stdio")
        idx = self.transport_combo.findData(transport)
        if idx >= 0:
            self.transport_combo.setCurrentIndex(idx)
        self.command_edit.setText(entry.get("command", ""))
        self.args_edit.setText(" ".join(entry.get("args", [])))
        self.url_edit.setText(entry.get("url", ""))
        self._populate_headers(entry.get("headers", {}))
        self.ca_edit.setText(entry.get("ca_bundle", ""))
        self.cert_edit.setText(entry.get("client_cert", ""))
        self.key_edit.setText(entry.get("client_key", ""))
        self.deferred_check.setChecked(entry.get("deferred", True))
        self.enabled_check.setChecked(entry.get("enabled", True))
        self.timeout_spin.setValue(int(entry.get("timeout", 600)))
        self._apply_transport_visibility(transport)

    def get_config(self) -> dict:
        transport = self.transport_combo.currentData()
        cfg = {
            "name": self.name_edit.text().strip(),
            "transport": transport,
            "enabled": self.enabled_check.isChecked(),
            "deferred": self.deferred_check.isChecked(),
            "timeout": self.timeout_spin.value(),
        }
        if transport == "stdio":
            args_text = self.args_edit.text().strip()
            cfg["command"] = self.command_edit.text().strip()
            cfg["args"] = args_text.split() if args_text else []
            cfg["env"] = {}
        else:
            cfg["url"] = self.url_edit.text().strip()
            cfg["headers"] = self._collect_headers()
            ca = self.ca_edit.text().strip()
            cert = self.cert_edit.text().strip()
            key = self.key_edit.text().strip()
            if ca:
                cfg["ca_bundle"] = ca
            if cert:
                cfg["client_cert"] = cert
            if key:
                cfg["client_key"] = key
        return cfg
