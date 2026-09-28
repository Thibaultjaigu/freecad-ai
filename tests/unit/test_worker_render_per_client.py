"""Each request is rendered for the client about to answer it (#104)."""

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
from freecad_ai.llm.client import LLMError  # noqa: E402
from freecad_ai.tools.registry import (  # noqa: E402
    ToolDefinition, ToolParam, ToolRegistry, ToolResult)
from freecad_ai.ui.chat_widget import _LLMWorker  # noqa: E402
from tests.unit._worker_harness import (  # noqa: E402
    DONE, OneClientWalker, ScriptedClient, call, run_worker, text, thinking)


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance()
    return app or QtWidgets.QApplication([])


def _registry():
    reg = ToolRegistry()
    reg.register(ToolDefinition(
        name="make_box", description="Make a box",
        parameters=[ToolParam("size", "number", "Edge length")],
        handler=lambda **kw: ToolResult(True, "ok")))
    return reg


def _with_tool_history():
    conv = Conversation()
    conv.add_user_message("first")
    conv.add_assistant_message("", tool_calls=[
        {"id": "t1", "name": "make_box", "arguments": {}}])
    conv.add_tool_result("t1", "ok")
    conv.add_user_message("next")
    return conv


def _two_rounds():
    return [[thinking("plan it"), text("Making."), call("c1", size=1), DONE],
            [text("Done."), DONE]]


def test_plan_mode_on_an_anthropic_client_renders_anthropic(qapp, tmp_config_dir):
    """Bug 3: Plan mode rendered OpenAI style for every profile."""
    client = ScriptedClient([[text("ok"), DONE]], api_style="anthropic")
    run_worker(_LLMWorker(_with_tool_history(), "S"), client)
    roles = [m["role"] for m in client.sent[0]["messages"]]
    assert "tool" not in roles
    assert client.sent[0]["tools"] is None


def test_the_tool_schema_follows_the_client(qapp, tmp_config_dir):
    client = ScriptedClient([[text("ok"), DONE]], api_style="anthropic")
    run_worker(_LLMWorker(Conversation(messages=[
        {"role": "user", "content": "hi"}]), "S", registry=_registry()), client)
    assert "input_schema" in client.sent[0]["tools"][0]


def test_the_cache_key_is_sent_without_a_describe_fn(qapp, tmp_config_dir):
    """Bug 1: the conversation only reached the worker with describe_fn."""
    conv = Conversation(messages=[{"role": "user", "content": "hi"}])
    seen = {}

    def fake_create(cfg, *, profile=None, cache_key="", **k):
        seen["cache_key"] = cache_key
        return ScriptedClient([[text("ok"), DONE]])

    with patch("freecad_ai.llm.fallback.create_client", fake_create):
        _LLMWorker(conv, "S").run()
    assert seen["cache_key"] == conv.conversation_id


def test_round_two_echoes_reasoning_even_when_not_preserved(qapp, tmp_config_dir):
    """The in-flight echo never depended on preserve_reasoning_history;
    only what is stored at the end of the turn does."""
    from freecad_ai.config import get_config
    get_config().preserve_reasoning_history = False
    client = ScriptedClient(_two_rounds())
    run_worker(_LLMWorker(Conversation(messages=[
        {"role": "user", "content": "hi"}]), "S", registry=_registry()), client)
    assistant = [m for m in client.sent[1]["messages"]
                 if m["role"] == "assistant"][-1]
    assert assistant["reasoning_content"] == "plan it"


def test_the_window_stays_pinned_across_rounds(qapp, tmp_config_dir):
    conv = _with_tool_history()
    worker = _LLMWorker(conv, "S", registry=_registry(), start_index=3)
    seen = []
    real = worker._work.get_messages_for_api

    def spy(**kw):
        seen.append(kw["start_index"])
        return real(**kw)

    worker._work.get_messages_for_api = spy
    run_worker(worker, ScriptedClient(_two_rounds()))
    assert seen == [3, 3]


def test_each_image_is_described_once(qapp, tmp_config_dir):
    conv = Conversation()
    conv.add_user_message("look", images=[{"type": "image", "data": "AAAA",
                                           "mime_type": "image/png"}])
    calls, notes = [], []

    def describe(data_url):
        calls.append(data_url)
        return "a cube"

    worker = _LLMWorker(conv, "S", registry=_registry(), describe_fn=describe)
    worker.vision_note.connect(notes.append)
    run_worker(worker, ScriptedClient(_two_rounds()))
    assert calls == ["data:image/png;base64,AAAA"]
    assert len(notes) == 1


def test_a_failed_description_is_not_retried(qapp, tmp_config_dir):
    conv = Conversation()
    conv.add_user_message("look", images=[{"type": "image", "data": "AAAA",
                                           "mime_type": "image/png"}])
    calls = []

    def describe(data_url):
        calls.append(1)
        raise RuntimeError("mcp down")

    client = ScriptedClient(_two_rounds())
    run_worker(_LLMWorker(conv, "S", registry=_registry(),
                          describe_fn=describe), client)
    assert calls == [1]
    assert "mcp down" in str(client.sent[1]["messages"])


def test_the_real_conversation_is_not_written(qapp, tmp_config_dir):
    conv = Conversation(messages=[{"role": "user", "content": "hi"}])
    run_worker(_LLMWorker(conv, "S", registry=_registry()),
               ScriptedClient(_two_rounds()))
    assert len(conv.messages) == 1


def test_the_round_is_recorded_for_the_widget(qapp, tmp_config_dir):
    worker = _LLMWorker(Conversation(messages=[
        {"role": "user", "content": "hi"}]), "S", registry=_registry())
    run_worker(worker, ScriptedClient(_two_rounds()), tool_output="made")
    assert worker._tool_results[0]["results"] == [
        {"tool_call_id": "c1", "content": "made"}]


def test_a_detached_worker_emits_nothing(qapp, tmp_config_dir):
    worker = _LLMWorker(Conversation(), "S")
    got = []
    worker.token_received.connect(got.append)
    worker.detach()
    worker._emit(worker.token_received, "late")
    assert got == []


def test_stop_before_any_answer_ends_as_stopped(qapp, tmp_config_dir):
    worker = _LLMWorker(Conversation(), "S")
    finished = []
    worker.response_finished.connect(finished.append)

    class _Stopped:
        attempts = []

        def open(self, round_no, request):
            return None

    worker._simple_stream(_Stopped())
    assert finished == ["\n\n_⏹ Stopped by user._"]


def test_a_mid_stream_error_ends_the_turn(qapp, tmp_config_dir):
    class _Dropping(ScriptedClient):
        def stream_with_tools(self, messages, system="", tools=None):
            yield text("par")
            raise LLMError("Request failed: reset", kind="unreachable")

    errors = []
    worker = _LLMWorker(Conversation(messages=[
        {"role": "user", "content": "hi"}]), "S")
    worker.error_occurred.connect(errors.append)
    run_worker(worker, _Dropping([]))
    assert errors == ["Request failed: reset"]


def test_the_answering_clients_cap_is_recorded(qapp, tmp_config_dir):
    from types import SimpleNamespace
    worker = _LLMWorker(Conversation(), "S")
    worker._apply_client(SimpleNamespace(model="qwen3:8b", max_tokens=16000,
                                         api_style="anthropic"))
    assert worker._response_max_tokens == 16000
    assert worker.api_style == "anthropic"
