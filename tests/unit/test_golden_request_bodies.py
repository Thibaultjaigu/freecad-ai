"""Request bodies of a two-round tool turn must not change (#104).

The fallback refactor moves vendor rendering from _tool_loop's hand-built
in-flight messages to Conversation.get_messages_for_api on a working copy.
Any byte that moves invalidates every provider's prefix cache (#47). This
file was written against the pre-refactor worker; its fixtures are the
contract. Never regenerate them after the refactor starts. The driver
now runs the refactored worker; the fixtures are still the pre-refactor
bytes.

Regenerate (only before the refactor):
    FREECAD_AI_WRITE_GOLDEN=1 env PYTHONPATH= .venv/bin/pytest \
        tests/unit/test_golden_request_bodies.py
"""

import copy
import json
import os
from unittest.mock import patch

import pytest

try:
    from PySide6 import QtWidgets
except ImportError:
    try:
        from PySide2 import QtWidgets
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.core.conversation import Conversation  # noqa: E402
from freecad_ai.llm.client import LLMClient  # noqa: E402
from freecad_ai.tools.registry import (  # noqa: E402
    ToolDefinition, ToolParam, ToolRegistry, ToolResult)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

_OPENAI_ROUNDS = [
    [
        {"choices": [{"delta": {"reasoning_content": "plan it"},
                      "finish_reason": None}]},
        {"choices": [{"delta": {"content": "Making boxes."},
                      "finish_reason": None}]},
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "call_1", "type": "function",
            "function": {"name": "make_box", "arguments": "{\"size\": 10}"}}]},
            "finish_reason": None}]},
        {"choices": [{"delta": {"tool_calls": [{
            "index": 1, "id": "call_2", "type": "function",
            "function": {"name": "make_box", "arguments": "{\"size\": 20}"}}]},
            "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ],
    [
        {"choices": [{"delta": {"content": "Done."}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ],
]

_ANTHROPIC_ROUNDS = [
    [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "plan it"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "text_delta", "text": "Making boxes."}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2,
         "content_block": {"type": "tool_use", "id": "toolu_1",
                           "name": "make_box", "input": {}}},
        {"type": "content_block_delta", "index": 2,
         "delta": {"type": "input_json_delta",
                   "partial_json": "{\"size\": 10}"}},
        {"type": "content_block_stop", "index": 2},
        {"type": "content_block_start", "index": 3,
         "content_block": {"type": "tool_use", "id": "toolu_2",
                           "name": "make_box", "input": {}}},
        {"type": "content_block_delta", "index": 3,
         "delta": {"type": "input_json_delta",
                   "partial_json": "{\"size\": 20}"}},
        {"type": "content_block_stop", "index": 3},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        {"type": "message_stop"},
    ],
    [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "Done."}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        {"type": "message_stop"},
    ],
]


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    return app or QtWidgets.QApplication([])


def _client(style):
    if style == "openai":
        return LLMClient(provider_name="openai",
                         base_url="https://api.openai.com/v1", api_key="k",
                         model="gpt-4o", prompt_caching=True,
                         cache_key="conv_x")
    return LLMClient(provider_name="anthropic",
                     base_url="https://api.anthropic.com", api_key="k",
                     model="claude-x", prompt_caching=True,
                     cache_key="conv_x")


def _conversation():
    conv = Conversation(conversation_id="conv_x")
    conv.add_user_message("hi")
    conv.add_assistant_message("hello", reasoning_content="greeting")
    conv.add_user_message("make a box")
    return conv


def _registry():
    reg = ToolRegistry()
    reg.register(ToolDefinition(
        name="make_box", description="Make a box",
        parameters=[ToolParam("size", "number", "Edge length")],
        handler=lambda **kw: ToolResult(True, "ok")))
    return reg


def _capture(client, rounds):
    """Patch the transport; return the list the bodies land in."""
    bodies, scripts = [], iter(rounds)

    def capture(*args, **kwargs):
        body = kwargs.get("body", args[-1])
        bodies.append(json.dumps(copy.deepcopy(body)))
        return iter(next(scripts))

    patcher = patch.object(client, "_http_stream", side_effect=capture)
    patcher.start()
    return bodies, patcher


def _drive(style, client, conv, reg):
    """Run one tool turn through the worker."""
    from freecad_ai.ui.chat_widget import _LLMWorker
    worker = _LLMWorker(conv, "SYSTEM", registry=reg,
                        start_index=conv.window_start())
    worker._execute_tool_on_main_thread = (
        lambda n, a: {"success": True, "output": "ok", "error": ""})
    with patch("freecad_ai.llm.fallback.create_client", return_value=client), \
         patch("freecad_ai.hooks.fire_hook", return_value={}):
        worker.run()
    return worker


def _bodies(style):
    client = _client(style)
    rounds = _ANTHROPIC_ROUNDS if style == "anthropic" else _OPENAI_ROUNDS
    bodies, patcher = _capture(client, rounds)
    try:
        _drive(style, client, _conversation(), _registry())
    finally:
        patcher.stop()
    return bodies


@pytest.mark.parametrize("style", ["openai", "anthropic"])
def test_request_bodies_match_the_golden_fixture(style, qapp, tmp_config_dir):
    bodies = _bodies(style)
    path = os.path.join(FIXTURES, f"golden_{style}_bodies.json")
    if os.environ.get("FREECAD_AI_WRITE_GOLDEN") == "1":
        os.makedirs(FIXTURES, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(bodies, f, indent=1, ensure_ascii=False)
            f.write("\n")
    with open(path, encoding="utf-8") as f:
        assert bodies == json.load(f)


@pytest.mark.parametrize("style,second_id", [("openai", "call_2"),
                                             ("anthropic", "toolu_2")])
def test_the_script_reaches_round_two(style, second_id, qapp, tmp_config_dir):
    """Guards the scripts themselves: a golden file of one body would
    pin nothing about how a round is carried into the next request."""
    bodies = _bodies(style)
    assert len(bodies) == 2
    assert second_id in bodies[1]
    assert "Making boxes." in bodies[1]
