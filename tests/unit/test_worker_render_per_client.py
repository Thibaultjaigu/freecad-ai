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


def _with_fallback(primary, fallback):
    """Configure chat -> b as the fallback chain and route create_client to
    the two scripted clients. Returns the patch and the profiles created."""
    from freecad_ai.config import ProviderConfig, get_config
    cfg = get_config()
    cfg.profiles = {
        "chat": ProviderConfig(name="ollama", tools_detected=True),
        "b": ProviderConfig(name="ollama", tools_detected=True),
    }
    cfg.active_profile = "chat"
    cfg.fallback_profiles = ["b"]
    made = []

    def fake_create(cfg_, *, profile=None, cache_key="", **k):
        made.append(profile)
        return fallback if profile == "b" else primary

    return patch("freecad_ai.llm.fallback.create_client", fake_create), made


def _refused():
    """A round that fails before its first event, as a dead host does."""
    raise LLMError("Connection error: refused", kind="unreachable")
    yield  # pragma: no cover -- makes this a generator


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
    """Detached mid-stream, the worker still runs the turn to its end (the
    thread is not running, so requestInterruption is a no-op here), and
    nothing from the stream or the tool path reaches the widget."""
    got = []
    worker = _LLMWorker(Conversation(messages=[
        {"role": "user", "content": "hi"}]), "S", registry=_registry())

    def detach_after_first_token():
        yield text("Making.")
        worker.detach()
        yield thinking("plan it")
        yield call("c1", size=1)
        yield DONE

    for name in ("token_received", "thinking_received", "tool_call_started",
                 "tool_call_finished", "response_finished", "error_occurred",
                 "vision_note", "fallback_note"):
        getattr(worker, name).connect(
            lambda *args, name=name: got.append((name,) + args))
    run_worker(worker, ScriptedClient(
        [detach_after_first_token(), [text(" Done."), DONE]]))
    assert got == [("token_received", "Making.")]
    # The run did go on: a tool round and the final answer happened.
    assert worker._tool_results and worker._full_response == "Making. Done."


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
    """Once the first event has arrived the profile has answered: an error
    after it ends the turn and never falls back, even with a fallback
    profile configured."""
    class _Dropping(ScriptedClient):
        def stream_with_tools(self, messages, system="", tools=None):
            yield text("par")
            raise LLMError("Request failed: reset", kind="unreachable")

    spare = ScriptedClient([[text("from b"), DONE]])
    errors, tokens = [], []
    worker = _LLMWorker(Conversation(messages=[
        {"role": "user", "content": "hi"}]), "S")
    worker.error_occurred.connect(errors.append)
    worker.token_received.connect(tokens.append)
    patcher, made = _with_fallback(_Dropping([]), spare)
    with patcher:
        worker.run()
    assert errors == ["Request failed: reset"]
    assert made == [None]            # only the chat profile was built
    assert spare.sent == []
    assert [a["outcome"] for a in worker.fallback_attempts] == ["answered"]
    assert tokens == ["par"]


def test_a_vendor_switch_mid_turn_renders_each_round_for_its_client(
        qapp, tmp_config_dir):
    """The heart of #104: round 0 answered by an OpenAI-style profile,
    round 1 refused there and answered by an Anthropic-style one. Each
    request is rendered from the working copy for the client receiving it."""
    primary = ScriptedClient(
        [[thinking("plan it"), text("Making."), call("c1", size=1), DONE],
         _refused()], api_style="openai")
    spare = ScriptedClient([[text("Done."), DONE]], api_style="anthropic")
    notes = []
    worker = _LLMWorker(Conversation(messages=[
        {"role": "user", "content": "hi"}]), "S", registry=_registry())
    worker.fallback_note.connect(notes.append)
    worker._execute_tool_on_main_thread = (
        lambda name, args: {"success": True, "output": "made", "error": ""})
    patcher, made = _with_fallback(primary, spare)
    with patcher, patch("freecad_ai.hooks.fire_hook", return_value={}):
        worker.run()

    # Round 1 as the OpenAI client was sent it (and refused it).
    oai = primary.sent[1]
    assert "function" in oai["tools"][0]
    assert oai["messages"][1]["role"] == "assistant"
    assert oai["messages"][1]["tool_calls"][0]["function"]["name"] == "make_box"
    assert oai["messages"][1]["reasoning_content"] == "plan it"
    assert oai["messages"][2] == {"role": "tool", "tool_call_id": "c1",
                                  "content": "made"}

    # The same round as the Anthropic client was sent it.
    ant = spare.sent[0]
    assert "input_schema" in ant["tools"][0]
    assert [m["role"] for m in ant["messages"]] == ["user", "assistant", "user"]
    assert ant["messages"][1]["content"] == [
        {"type": "text", "text": "Making."},
        {"type": "tool_use", "id": "c1", "name": "make_box",
         "input": {"size": 1}}]
    assert ant["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "c1", "content": "made"}]

    assert made == [None, "b"]
    assert [(a["round"], a["profile"], a["outcome"])
            for a in worker.fallback_attempts] == [
        (0, "chat", "answered"), (1, "chat", "failed"), (1, "b", "answered")]
    assert worker.api_style == "anthropic"
    assert len(notes) == 1 and "answered by b" in notes[0]
    assert worker._full_response == "Making.Done."


def test_the_answering_clients_cap_is_recorded(qapp, tmp_config_dir):
    from types import SimpleNamespace
    worker = _LLMWorker(Conversation(), "S")
    worker._apply_client(SimpleNamespace(model="qwen3:8b", max_tokens=16000,
                                         api_style="anthropic"))
    assert worker._response_max_tokens == 16000
    assert worker.api_style == "anthropic"
