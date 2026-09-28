"""The fallback walk: one pass, forward-only, first event = success (#104)."""

import logging
from unittest.mock import patch

import pytest

from freecad_ai.config import AppConfig, ProviderConfig
from freecad_ai.llm.client import LLMError
from freecad_ai.llm.fallback import FallbackWalker, fallback_chain, short_reason


def _cfg(fallback=(), **caps):
    c = AppConfig()
    c.profiles = {
        "chat": ProviderConfig(name="ollama", tools_detected=True,
                               vision_override=True),
        "b": ProviderConfig(name="ollama", tools_detected=True,
                            vision_override=True),
        "c": ProviderConfig(name="ollama", tools_detected=True,
                            vision_override=True),
    }
    for label, (tools, vision) in caps.items():
        c.profiles[label].tools_detected = tools
        c.profiles[label].vision_override = vision
    c.active_profile = "chat"
    c.fallback_profiles = list(fallback)
    return c


class _Client:
    def __init__(self, label):
        self.label = label
        self.max_retries = 5


def _walker(cfg, **kw):
    made = []

    def fake_create(cfg_, *, profile=None, cache_key="", **_):
        made.append(profile)
        return _Client(profile or cfg_.active_profile)

    patcher = patch("freecad_ai.llm.fallback.create_client", fake_create)
    patcher.start()
    return FallbackWalker(cfg, **kw), made, patcher


def _request(behaviour):
    """behaviour: label -> an exception to raise at the first event, or a
    list of events to yield."""
    def request(client):
        outcome = behaviour[client.label]

        def gen():
            if isinstance(outcome, Exception):
                raise outcome
            yield from outcome
        return gen()
    return request


REFUSED = LLMError("Connection error: [Errno 111] Connection refused",
                   kind="unreachable")


class TestChain:
    def test_active_first_then_the_list_in_order(self):
        assert fallback_chain(_cfg(["c", "b"])) == ["chat", "c", "b"]

    def test_the_active_profile_is_not_repeated(self):
        assert fallback_chain(_cfg(["chat", "b"])) == ["chat", "b"]

    def test_unknown_labels_are_skipped(self):
        assert fallback_chain(_cfg(["gone", "b"])) == ["chat", "b"]

    def test_empty_list_is_the_chat_profile_alone(self):
        assert fallback_chain(_cfg()) == ["chat"]


class TestShortReason:
    def test_status_wins(self):
        assert short_reason(LLMError("HTTP 503: x\nbody", status=503)) == "HTTP 503"

    def test_refused(self):
        assert short_reason(REFUSED) == "connection refused"

    def test_dns(self):
        err = LLMError("Connection error: [Errno -2] Name or service not known")
        assert short_reason(err) == "name or service not known"

    def test_timeout(self):
        assert short_reason(LLMError("Request failed: timed out")) == "timed out"


class TestWalk:
    def test_empty_list_makes_one_client_and_reraises_unchanged(self, caplog):
        walker, made, p = _walker(_cfg())
        try:
            with caplog.at_level(logging.WARNING):
                with pytest.raises(LLMError) as info:
                    walker.open(0, _request({"chat": REFUSED}))
        finally:
            p.stop()
        assert info.value is REFUSED
        assert made == [None]
        assert "FreeCAD AI:" not in caplog.text
        assert walker.attempts == []

    def test_empty_list_keeps_todays_retries(self):
        walker, made, p = _walker(_cfg())
        try:
            client, _ = walker.open(0, _request({"chat": ["e"]}))
        finally:
            p.stop()
        assert client.max_retries == 5

    def test_failover_answers_from_the_next_profile(self, caplog):
        notes = []
        walker, made, p = _walker(_cfg(["b"]), on_note=notes.append)
        try:
            with caplog.at_level(logging.WARNING):
                client, events = walker.open(
                    0, _request({"chat": REFUSED, "b": ["e1", "e2"]}))
        finally:
            p.stop()
        assert client.label == "b"
        assert list(events) == ["e1", "e2"]
        assert made == [None, "b"]
        assert "FreeCAD AI: chat failed — connection refused; trying b" in caplog.text
        assert "FreeCAD AI: answered by b (fallback 1 of 1)" in caplog.text
        assert notes == ["⚠ chat couldn't be reached (connection refused) — answered by b"]

    def test_only_the_last_candidate_retries_429(self):
        walker, made, p = _walker(_cfg(["b"]))
        try:
            first = walker._client(0)
            last = walker._client(1)
        finally:
            p.stop()
        assert (first.max_retries, last.max_retries) == (0, 5)

    def test_the_cursor_never_goes_back(self):
        walker, made, p = _walker(_cfg(["b", "c"]))
        behaviour = {"chat": REFUSED, "b": ["r1"], "c": ["never"]}
        try:
            walker.open(0, _request(behaviour))
            behaviour["b"] = ["r2"]
            client, events = walker.open(1, _request(behaviour))
        finally:
            p.stop()
        assert client.label == "b"
        assert list(events) == ["r2"]
        assert made == [None, "b"]          # chat is not retried, c not needed

    def test_a_refusal_reads_as_refused(self):
        notes = []
        walker, made, p = _walker(_cfg(["b"]), on_note=notes.append)
        try:
            walker.open(0, _request({
                "chat": LLMError("HTTP 401: Unauthorized\n", status=401),
                "b": ["e"]}))
        finally:
            p.stop()
        assert notes == ["⚠ chat was refused (HTTP 401) — answered by b"]

    def test_a_candidate_without_vision_is_skipped(self, caplog):
        walker, made, p = _walker(_cfg(["b", "c"], b=(True, False)),
                                  needs_vision=True)
        try:
            with caplog.at_level(logging.WARNING):
                client, _ = walker.open(
                    0, _request({"chat": REFUSED, "c": ["e"]}))
        finally:
            p.stop()
        assert client.label == "c"
        assert "b" not in made
        assert "FreeCAD AI: b skipped — no vision" in caplog.text

    def test_a_candidate_without_tools_is_skipped(self):
        walker, made, p = _walker(_cfg(["b", "c"], b=(False, True)),
                                  needs_tools=True)
        try:
            client, _ = walker.open(0, _request({"chat": REFUSED, "c": ["e"]}))
        finally:
            p.stop()
        assert client.label == "c"

    def test_the_chat_profile_is_never_skipped(self):
        walker, made, p = _walker(_cfg(["b"], chat=(False, False)),
                                  needs_tools=True, needs_vision=True)
        try:
            client, _ = walker.open(0, _request({"chat": ["e"]}))
        finally:
            p.stop()
        assert client.label == "chat"

    def test_a_skipped_last_profile_leaves_the_retries_on_the_eligible_one(self):
        walker, made, p = _walker(_cfg(["b", "c"], c=(True, False)),
                                  needs_vision=True)
        try:
            assert walker._client(1).max_retries == 5
        finally:
            p.stop()

    def test_every_profile_failing_names_each_attempt(self):
        walker, made, p = _walker(_cfg(["b", "c"], c=(True, False)),
                                  needs_vision=True)
        try:
            with pytest.raises(LLMError) as info:
                walker.open(0, _request({
                    "chat": REFUSED,
                    "b": LLMError("HTTP 503: x\n", status=503,
                                  kind="unreachable")}))
        finally:
            p.stop()
        assert str(info.value) == ("No profile could answer — chat: connection "
                                   "refused · b: HTTP 503 · c: skipped (no vision)")
        assert [a["outcome"] for a in walker.attempts] == [
            "failed", "failed", "skipped"]

    def test_attempts_record_each_try(self):
        walker, made, p = _walker(_cfg(["b"]))
        try:
            walker.open(2, _request({"chat": REFUSED, "b": ["e"]}))
        finally:
            p.stop()
        assert walker.attempts == [
            {"round": 2, "profile": "chat", "outcome": "failed",
             "error": "connection refused"},
            {"round": 2, "profile": "b", "outcome": "answered", "error": ""},
        ]

    def test_an_interrupt_before_an_attempt_returns_none(self):
        walker, made, p = _walker(_cfg(["b"]), is_interrupted=lambda: True)
        try:
            assert walker.open(0, _request({"chat": ["e"]})) is None
        finally:
            p.stop()
        assert made == []

    def test_an_error_after_the_first_event_is_not_a_fallback(self):
        def request(client):
            def gen():
                yield "first"
                raise LLMError("Request failed: reset", kind="unreachable")
            return gen()

        walker, made, p = _walker(_cfg(["b"]))
        try:
            client, events = walker.open(0, request)
            with pytest.raises(LLMError):
                list(events)
        finally:
            p.stop()
        assert made == [None]

    def test_a_non_llm_error_ends_the_turn(self):
        walker, made, p = _walker(_cfg(["b"]))
        try:
            with pytest.raises(TypeError):
                walker.open(0, _request({"chat": TypeError("bug")}))
        finally:
            p.stop()
        assert made == [None]

    def test_an_empty_stream_is_an_answer(self):
        walker, made, p = _walker(_cfg(["b"]))
        try:
            client, events = walker.open(0, _request({"chat": []}))
        finally:
            p.stop()
        assert client.label == "chat"
        assert list(events) == []

    def test_an_overload_reported_inside_the_stream_moves_to_the_fallback(self):
        """The client-side fix (#104 finding A) raises this before any event
        is yielded, so the walker's existing failover path must catch it."""
        overload = LLMError("overloaded_error: Overloaded", kind="unreachable")
        walker, made, p = _walker(_cfg(["b"]))
        try:
            client, events = walker.open(
                0, _request({"chat": overload, "b": ["e"]}))
        finally:
            p.stop()
        assert client.label == "b"
        assert list(events) == ["e"]
        assert [a["outcome"] for a in walker.attempts] == ["failed", "answered"]

