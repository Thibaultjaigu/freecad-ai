"""LLMError says whether the next profile is worth trying (#104)."""

import socket
import urllib.error
from unittest.mock import patch

import pytest

from freecad_ai.llm.client import LLMClient, LLMError


def _client():
    return LLMClient(provider_name="openai", base_url="http://127.0.0.1:9/v1",
                     api_key="k", model="m")


def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "reason", {}, None)


def _raise_from_stream(exc, client=None):
    client = client or _client()
    with patch("urllib.request.urlopen", side_effect=exc):
        with pytest.raises(LLMError) as info:
            next(client._http_stream("http://x", {}, {}))
    return info.value


def _raise_from_post(exc):
    with patch("urllib.request.urlopen", side_effect=exc):
        with pytest.raises(LLMError) as info:
            _client()._http_post("http://x", {}, {})
    return info.value


@pytest.mark.parametrize("exc", [
    urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
    urllib.error.URLError(socket.gaierror(-2, "Name or service not known")),
    socket.timeout("timed out"),
])
def test_transport_failures_are_unreachable(exc):
    for raise_it in (_raise_from_stream, _raise_from_post):
        err = raise_it(exc)
        assert err.kind == "unreachable"
        assert err.status is None


@pytest.mark.parametrize("code", [500, 503])
def test_server_errors_are_unreachable_and_keep_the_status(code):
    err = _raise_from_stream(_http_error(code))
    assert (err.kind, err.status) == ("unreachable", code)
    assert str(err).startswith(f"HTTP {code}: reason")


def test_429_is_unreachable_and_raises_at_once_with_no_retries():
    client = _client()
    client.max_retries = 0
    with patch("freecad_ai.llm.client.time.sleep") as sleep:
        err = _raise_from_stream(_http_error(429), client)
    assert (err.kind, err.status) == ("unreachable", 429)
    sleep.assert_not_called()


def test_429_still_backs_off_by_default():
    client = _client()
    with patch("freecad_ai.llm.client.time.sleep") as sleep:
        _raise_from_stream(_http_error(429), client)
    assert sleep.call_count == 5


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_client_errors_are_config_and_keep_the_status(code):
    for raise_it in (_raise_from_stream, _raise_from_post):
        err = raise_it(_http_error(code))
        assert (err.kind, err.status) == ("config", code)


def test_a_read_that_drops_mid_stream_is_unreachable():
    class _Resp:
        def __iter__(self):
            raise socket.timeout("timed out")

        def close(self):
            pass

    with patch("urllib.request.urlopen", return_value=_Resp()):
        with pytest.raises(LLMError) as info:
            next(_client()._http_stream("http://x", {}, {}))
    assert info.value.kind == "unreachable"
    assert str(info.value) == "Request failed: timed out"


def test_the_default_kind_is_config():
    assert LLMError("x").kind == "config"
    assert LLMError("x").status is None


def _styled_client(api_style):
    """A client whose parser we exercise directly, independent of provider_name."""
    client = _client()
    client.api_style = api_style
    return client


class TestInStreamErrorChunks:
    """An error sent after HTTP 200 must fail the request, not read as an answer.

    Anthropic streams an ``event: error`` chunk on overload; OpenAI-compatible
    servers put a top-level ``error`` key on a chunk. Left unhandled, the parser
    falls through to a synthesized ``done`` and the fallback walker (#104)
    counts it as answered.
    """

    def test_anthropic_overload_is_unreachable_and_keeps_the_message(self):
        client = _styled_client("anthropic")
        chunks = [{"type": "error",
                  "error": {"type": "overloaded_error", "message": "Overloaded"}}]
        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            with pytest.raises(LLMError) as info:
                list(client._stream_anthropic_tools([], "", tools=[]))
        assert info.value.kind == "unreachable"
        assert "Overloaded" in str(info.value)

    @pytest.mark.parametrize("err_type", [
        "api_error", "rate_limit_error", "some_future_type"])
    def test_anthropic_other_types_are_unreachable(self, err_type):
        client = _styled_client("anthropic")
        chunks = [{"type": "error", "error": {"type": err_type, "message": "x"}}]
        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            with pytest.raises(LLMError) as info:
                list(client._stream_anthropic_tools([], "", tools=[]))
        assert info.value.kind == "unreachable"

    @pytest.mark.parametrize("err_type", [
        "invalid_request_error", "authentication_error",
        "permission_error", "not_found_error"])
    def test_anthropic_request_side_types_are_config(self, err_type):
        client = _styled_client("anthropic")
        chunks = [{"type": "error", "error": {"type": err_type, "message": "bad key"}}]
        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            with pytest.raises(LLMError) as info:
                list(client._stream_anthropic_tools([], "", tools=[]))
        assert info.value.kind == "config"
        assert "bad key" in str(info.value)

    def test_openai_style_error_chunk_is_unreachable_by_default(self):
        client = _styled_client("openai")
        chunks = [{"error": {"message": "Overloaded", "type": "overloaded_error"}}]
        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            with pytest.raises(LLMError) as info:
                list(client._stream_openai_tools([], "", tools=[]))
        assert info.value.kind == "unreachable"
        assert "Overloaded" in str(info.value)

    @pytest.mark.parametrize("code", [400, 401, 403, 404])
    def test_openai_style_http_like_codes_are_config(self, code):
        client = _styled_client("openai")
        chunks = [{"error": {"message": "bad request", "code": code}}]
        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            with pytest.raises(LLMError) as info:
                list(client._stream_openai_tools([], "", tools=[]))
        assert info.value.kind == "config"

    def test_openai_style_invalid_request_error_type_is_config(self):
        client = _styled_client("openai")
        chunks = [{"error": {"message": "bad", "type": "invalid_request_error"}}]
        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            with pytest.raises(LLMError) as info:
                list(client._stream_openai_tools([], "", tools=[]))
        assert info.value.kind == "config"

    def test_openai_style_error_after_content_is_still_raised(self):
        """The error can arrive after other chunks, not only as chunk 1."""
        client = _styled_client("openai")
        chunks = [
            {"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]},
            {"error": {"message": "overloaded", "type": "overloaded_error"}},
        ]
        with patch.object(client, "_http_stream", return_value=iter(chunks)):
            with pytest.raises(LLMError) as info:
                list(client._stream_openai_tools([], "", tools=[]))
        assert info.value.kind == "unreachable"
