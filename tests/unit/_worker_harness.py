"""Helpers for running _LLMWorker methods without a thread or a provider."""

from unittest.mock import patch

from freecad_ai.core.conversation import Conversation
from freecad_ai.llm.client import LLMStreamEvent, ToolCall


class OneClientWalker:
    """A FallbackWalker stand-in that always opens on one client."""

    def __init__(self, client):
        self.client = client
        self.attempts = []

    def open(self, round_no, request):
        return self.client, iter(request(self.client))


def add_seams(fake, tools=None):
    """Give a fake worker the seams the streaming methods now call."""
    fake._work = Conversation()
    fake._detached = False
    fake._apply_client = lambda client: None
    fake._request = lambda client: client.stream_with_tools(
        [], system=getattr(fake, "system_prompt", ""), tools=tools)
    fake._emit = lambda signal, *args: signal.emit(*args)
    return fake


def text(t):
    return LLMStreamEvent(type="text_delta", text=t)


def thinking(t):
    return LLMStreamEvent(type="thinking_delta", text=t)


def call(call_id, name="make_box", **arguments):
    return LLMStreamEvent(type="tool_call_end",
                          tool_call=ToolCall(id=call_id, name=name,
                                             arguments=arguments))


DONE = LLMStreamEvent(type="done")


class ScriptedClient:
    """Answers each stream_with_tools call with the next scripted round,
    and records what it was sent."""

    def __init__(self, rounds, api_style="openai", model="m"):
        self._rounds = iter(rounds)
        self.api_style = api_style
        self.model = model
        self.max_tokens = 4096
        self.max_retries = 5
        self.response_truncated = False
        self.sent = []

    def stream_with_tools(self, messages, system="", tools=None):
        self.sent.append({"messages": messages, "tools": tools})
        yield from next(self._rounds)


def run_worker(worker, client, tool_output="ok"):
    """Run a real _LLMWorker on this thread against one client."""
    worker._execute_tool_on_main_thread = (
        lambda name, args: {"success": True, "output": tool_output,
                            "error": ""})
    with patch("freecad_ai.llm.fallback.create_client", return_value=client), \
         patch("freecad_ai.hooks.fire_hook", return_value={}):
        worker.run()
