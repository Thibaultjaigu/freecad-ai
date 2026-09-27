from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# The dialog imports through ui/compat.py, which needs PySide6 or PySide2.
try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.ui.settings_pages.mcp_page import _AddMCPServerDialog, McpPage


class TestApplyTransportVisibility:
    def test_stdio_shows_stdio_hides_url(self):
        d = SimpleNamespace(_stdio_widget=MagicMock(), _url_widget=MagicMock())
        _AddMCPServerDialog._apply_transport_visibility(d, "stdio")
        d._stdio_widget.setVisible.assert_called_with(True)
        d._url_widget.setVisible.assert_called_with(False)

    def test_sse_shows_url_hides_stdio(self):
        d = SimpleNamespace(_stdio_widget=MagicMock(), _url_widget=MagicMock())
        _AddMCPServerDialog._apply_transport_visibility(d, "sse")
        d._stdio_widget.setVisible.assert_called_with(False)
        d._url_widget.setVisible.assert_called_with(True)


class TestCollectHeaders:
    def _table(self, rows):
        table = MagicMock()
        table.rowCount.return_value = len(rows)
        def item(r, c):
            key, val = rows[r]
            text = key if c == 0 else val
            if text is None:
                return None
            cell = MagicMock()
            cell.text.return_value = text
            return cell
        table.item.side_effect = item
        return table

    def test_collects_nonempty_keys(self):
        d = SimpleNamespace(headers_table=self._table(
            [("Authorization", "Bearer x"), ("", "ignored"), ("X-Api-Key", "k")]))
        result = _AddMCPServerDialog._collect_headers(d)
        assert result == {"Authorization": "Bearer x", "X-Api-Key": "k"}


class TestGetConfig:
    def _dialog(self, transport, **fields):
        combo = MagicMock()
        combo.currentData.return_value = transport
        return SimpleNamespace(
            transport_combo=combo,
            name_edit=MagicMock(**{"text.return_value": fields.get("name", "s")}),
            command_edit=MagicMock(**{"text.return_value": fields.get("command", "")}),
            args_edit=MagicMock(**{"text.return_value": fields.get("args", "")}),
            url_edit=MagicMock(**{"text.return_value": fields.get("url", "")}),
            ca_edit=MagicMock(**{"text.return_value": fields.get("ca", "")}),
            cert_edit=MagicMock(**{"text.return_value": fields.get("cert", "")}),
            key_edit=MagicMock(**{"text.return_value": fields.get("key", "")}),
            enabled_check=MagicMock(**{"isChecked.return_value": True}),
            deferred_check=MagicMock(**{"isChecked.return_value": True}),
            timeout_spin=MagicMock(**{"value.return_value": 600}),
            _collect_headers=lambda: fields.get("headers", {}),
        )

    def test_stdio_shape(self):
        d = self._dialog("stdio", command="npx", args="-y srv /tmp")
        cfg = _AddMCPServerDialog.get_config(d)
        assert cfg["transport"] == "stdio"
        assert cfg["command"] == "npx"
        assert cfg["args"] == ["-y", "srv", "/tmp"]
        assert "url" not in cfg

    def test_sse_shape_with_headers_and_tls(self):
        d = self._dialog("sse", url="https://h/sse",
                         headers={"Authorization": "Bearer x"}, ca="/ca.pem")
        cfg = _AddMCPServerDialog.get_config(d)
        assert cfg["transport"] == "sse"
        assert cfg["url"] == "https://h/sse"
        assert cfg["headers"] == {"Authorization": "Bearer x"}
        assert cfg["ca_bundle"] == "/ca.pem"
        assert "client_cert" not in cfg  # empty TLS paths omitted
        assert "command" not in cfg


class TestMcpListLabel:
    def test_url_transport_shows_url(self):
        label = McpPage._mcp_list_label(
            {"name": "remote", "transport": "sse", "url": "https://h/sse"})
        assert "https://h/sse" in label
        assert "[sse]" in label

    def test_stdio_transport_shows_command(self):
        label = McpPage._mcp_list_label(
            {"name": "fs", "command": "npx", "args": ["-y", "srv"]})
        assert "npx" in label


class TestUrlValidationOnAccept:
    def test_url_error_message_empty(self):
        assert "required" in _AddMCPServerDialog._url_error_message("").lower()

    def test_url_error_message_remote_http_rejected(self):
        msg = _AddMCPServerDialog._url_error_message("http://example.com/sse")
        assert msg  # non-empty error
        assert "localhost" in msg.lower() or "https" in msg.lower()

    def test_url_error_message_https_ok(self):
        assert _AddMCPServerDialog._url_error_message("https://host/sse") == ""

    def test_url_error_message_http_loopback_ok(self):
        assert _AddMCPServerDialog._url_error_message("http://localhost:3000/sse") == ""

    def test_on_accept_blocks_invalid_url(self):
        combo = MagicMock()
        combo.currentData.return_value = "sse"
        d = SimpleNamespace(
            transport_combo=combo,
            url_edit=MagicMock(**{"text.return_value": "http://example.com/sse"}),
            _url_warning=MagicMock(),
            accept=MagicMock(),
        )
        _AddMCPServerDialog._on_accept(d)
        d._url_warning.setVisible.assert_called_with(True)
        d.accept.assert_not_called()

    def test_on_accept_allows_valid_url(self):
        combo = MagicMock()
        combo.currentData.return_value = "http"
        d = SimpleNamespace(
            transport_combo=combo,
            url_edit=MagicMock(**{"text.return_value": "https://host/mcp"}),
            _url_warning=MagicMock(),
            accept=MagicMock(),
        )
        _AddMCPServerDialog._on_accept(d)
        d.accept.assert_called_once()

    def test_on_accept_stdio_skips_url_check(self):
        combo = MagicMock()
        combo.currentData.return_value = "stdio"
        d = SimpleNamespace(
            transport_combo=combo,
            url_edit=MagicMock(**{"text.return_value": ""}),
            _url_warning=MagicMock(),
            accept=MagicMock(),
        )
        _AddMCPServerDialog._on_accept(d)
        d.accept.assert_called_once()
