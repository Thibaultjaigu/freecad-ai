"""Tests for the MCP server address fields in McpPage.

Only the pure parsing helper is tested. Building the page needs a
QApplication; extracting the normalisation into a staticmethod keeps the part
with actual logic testable without one.
"""

import pytest

try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.mcp.gui_server import DEFAULT_HOST, DEFAULT_PORT  # noqa: E402
from freecad_ai.ui.settings_pages.mcp_page import McpPage  # noqa: E402

parse = McpPage._parse_server_address


def test_parses_a_normal_address():
    assert parse("192.168.1.50", "8080") == ("192.168.1.50", 8080)


def test_strips_surrounding_whitespace():
    assert parse("  127.0.0.1  ", "  3000 ") == ("127.0.0.1", 3000)


def test_empty_host_falls_back_to_the_default():
    assert parse("", "8080") == (DEFAULT_HOST, 8080)


def test_empty_port_falls_back_to_the_default():
    assert parse("127.0.0.1", "") == ("127.0.0.1", DEFAULT_PORT)


def test_non_numeric_port_falls_back_to_the_default():
    assert parse("127.0.0.1", "abc") == ("127.0.0.1", DEFAULT_PORT)


def test_out_of_range_port_falls_back_to_the_default():
    assert parse("127.0.0.1", "70000") == ("127.0.0.1", DEFAULT_PORT)
    assert parse("127.0.0.1", "0") == ("127.0.0.1", DEFAULT_PORT)


def test_privileged_ports_are_allowed():
    """No 1024 floor -- the GUI reaches exactly the ports MCP_PORT does.

    Binding one unprivileged fails with PermissionError, which surfaces
    through the same modal as any other bind failure.
    """
    assert parse("127.0.0.1", "80") == ("127.0.0.1", 80)


# --- allowed Host headers --------------------------------------------------
#
# Same contract as the address fields: the dialog must always be closable, so
# unusable input is dropped rather than raised on. The env-var path
# (resolve_allowed_hosts) refuses a "*" loudly instead, because there is no
# dialog there and a traceback is the only feedback available.

parse_hosts = McpPage._parse_allowed_hosts


def test_allowed_hosts_empty_field_means_the_transport_default():
    assert parse_hosts("") == []
    assert parse_hosts("   ") == []


def test_allowed_hosts_splits_and_strips():
    assert parse_hosts("fileserver.local, 192.168.1.50") == [
        "fileserver.local", "192.168.1.50"]


def test_allowed_hosts_drops_blank_entries():
    assert parse_hosts("box.lan, ,, 10.0.0.7,") == ["box.lan", "10.0.0.7"]


def test_allowed_hosts_drops_a_wildcard_entry_keeping_the_rest():
    # Dropped, not raised on: the dialog must stay closable. The warning
    # under the field is what tells the user why "*" is not honoured.
    assert parse_hosts("box.lan, *") == ["box.lan"]


def test_allowed_hosts_a_lone_wildcard_falls_back_to_the_default():
    assert parse_hosts("*") == []
