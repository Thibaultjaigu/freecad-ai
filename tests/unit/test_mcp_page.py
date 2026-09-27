"""McpPage: the MCP client list and the built-in server settings (#101)."""

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
from freecad_ai.ui.settings_pages.mcp_page import McpPage  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture
def page(qapp):
    p = McpPage()
    yield p
    p.deleteLater()


def _cfg():
    c = AppConfig()
    c.mcp_servers = [{"name": "a", "command": "x", "args": []}]
    c.mcp_server_host = "127.0.0.1"
    c.mcp_server_port = 30000
    c.mcp_server_allowed_hosts = ["localhost"]
    c.mcp_server_auth_token = "tok"
    return c


def test_two_group_boxes(page):
    titles = [g.title() for g in page.findChildren(QtWidgets.QGroupBox)]
    assert titles == ["MCP Servers", "Built-in MCP Server"]


def test_untouched_load_writes_nothing(page):
    page.load(_cfg())
    target = _cfg()
    target.mcp_server_port = 4242     # a newer value from elsewhere
    page.apply_to(target)
    assert target.mcp_server_port == 4242
    assert page.is_dirty() is False


def test_retyping_the_same_host_is_not_a_change(page):
    page.load(_cfg())
    page.mcp_server_host_edit.setText("  127.0.0.1 ")
    page.mcp_server_allowed_hosts_edit.setText("localhost ,")
    assert page.is_dirty() is False


def test_a_port_edit_writes_only_the_port(page):
    page.load(_cfg())
    page.mcp_server_port_edit.setText("31000")
    target = _cfg()
    target.mcp_server_auth_token = "other"
    page.apply_to(target)
    assert target.mcp_server_port == 31000
    assert target.mcp_server_auth_token == "other"


def test_removing_a_server_is_a_change(page):
    page.load(_cfg())
    page.mcp_list.setCurrentRow(0)
    page._remove_mcp_server()
    target = _cfg()
    page.apply_to(target)
    assert target.mcp_servers == []


def test_an_empty_allowed_hosts_field_saves_an_empty_list(page):
    """Empty must stay empty (the transport default), not the loopback list."""
    page.load(_cfg())
    page.mcp_server_allowed_hosts_edit.setText("")
    target = _cfg()
    page.apply_to(target)
    assert target.mcp_server_allowed_hosts == []
