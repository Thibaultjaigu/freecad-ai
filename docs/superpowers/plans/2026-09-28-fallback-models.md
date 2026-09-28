# Fallback Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the chat model can't be reached, a turn walks an ordered, user-chosen list of other profiles once, and each request is rendered for the profile about to answer it.

**Architecture:** A new `freecad_ai/llm/fallback.py` holds `fallback_chain(cfg)` and a `FallbackWalker` with a forward-only cursor. The walker opens each request lazily and counts it a success once the first stream event arrives. `LLMError` gains `status` and `kind`, so the walker can tell "unreachable" from "refused". `_LLMWorker` stops receiving pre-rendered, vendor-shaped messages. It gets the conversation instead, records each tool round on a `fork_for_turn()` copy, and renders the history and the tool schema per client just before each call, with the truncation window pinned by `start_index`. The widget starts every worker through one `_start_worker` helper, and it detaches a worker that ignores Stop for 2 s. The shared `ProviderSection` gains the list editor.

**Tech Stack:** Python 3.11, PySide6/PySide2 via `freecad_ai/ui/compat.py`, pytest with offscreen Qt.

**Spec:** `docs/superpowers/specs/2026-09-28-fallback-models-design.md`

## Global Constraints

- Test command: `env PYTHONPATH= .venv/bin/pytest tests/unit -q --ignore=tests/unit/test_document_attach.py`.
  - A full run takes about 130 s. The baseline is 2043 passed.
  - The shell's `PYTHONPATH` breaks pluggy, hence `env PYTHONPATH=`.
  - `test_document_attach.py` segfaults under Qt, even on clean master, hence the `--ignore`.
- Qt imports go only through `freecad_ai/ui/compat.py`. Never hard-import PySide2 in production code. Use flat enum forms only (`QTextCursor.End`).
- Every commit ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Work on branch `feat/104-fallback-models`, never on master.
- Run the full suite before every commit.
- Never touch the maintainer's real FreeCAD or FreeCAD AI config.
  - Tests that read or write config use the `tmp_config_dir` fixture, and patch `freecad_ai.hooks.fire_hook` wherever a worker runs tools.
  - Live probes use an isolated HOME / XDG / `FREECAD_AI_CONFIG_DIR` under the scratchpad (`probe99/run.sh`).
- The golden fixtures written in Task 1 are **never regenerated** after Task 1. If a golden test fails later, the code is wrong, not the fixture.
- Exact strings. Copy these verbatim:
  - Group title: `Fallback when the chat model can't be reached`
  - Tooltip: `Tried once, in this order, after the chat profile fails to answer. A paid profile in this list is used without asking.`
  - Log lines:
    - `FreeCAD AI: %s failed — %s; trying %s`
    - `FreeCAD AI: %s failed — %s; no profile left`
    - `FreeCAD AI: %s skipped — no %s`
    - `FreeCAD AI: answered by %s (fallback %d of %d)`
  - Chat note: `⚠ <segments joined by "; "> — answered by <label>`. A segment reads:
    - `<label> couldn't be reached (<reason>)` for kind `unreachable`;
    - `<label> was refused (<reason>)` for kind `config`;
    - `<label> skipped (no <cap>)` for a capability skip.
  - Summary error: `No profile could answer — ` followed by the entries `<label>: <reason>` or `<label>: skipped (no <cap>)`, joined by ` · `.
  - Load warning: `fallback profile "%s" dropped — no such profile`.
  - Detach note: `⏹ Stopped`.

## Review Focus

1. **The active profile also listed in `fallback_profiles`.** It is kept on load, never tried twice in one turn, and survives switching the active profile. Pinned in Task 3 (`test_the_active_profile_is_kept`) and Task 4 (`test_the_active_profile_is_not_repeated`).
2. **Stop pressed while a request hangs in connect or read.** The input comes back within about 2 s. The stuck worker is kept alive until it ends and never writes to the chat or the conversation. Pinned in Task 6 (`test_a_detached_worker_emits_nothing`, `test_stop_before_any_answer_ends_as_stopped`) and Task 7 (`test_a_stuck_worker_is_detached`).
3. **An error after the first stream event.** No fallback is tried: the turn ends with that error, as today. Pinned in Task 4 (`test_an_error_after_the_first_event_is_not_a_fallback`) and Task 6 (`test_a_mid_stream_error_ends_the_turn`).
4. **A turn with images on a vision chat profile, with a non-vision fallback.** The fallback is skipped with a logged reason, never sent raw images. Pinned in Task 4 (`test_a_candidate_without_vision_is_skipped`) and Task 7 (`test_needs_vision_comes_from_the_rendered_history`).
5. **Deleting or renaming a listed profile.** Its entries follow in the editor. A hand-edited config naming a missing profile loads without it. Pinned in Task 8 (`test_delete_removes_the_fallback_entry`, `test_rename_carries_the_fallback_entry`) and Task 3 (`test_unknown_labels_are_dropped_with_a_warning`).

---

### Task 1: Golden request bodies (capture today's bytes)

**Files:**
- Create: `tests/unit/test_golden_request_bodies.py`
- Create: `tests/unit/fixtures/golden_openai_bodies.json`, `tests/unit/fixtures/golden_anthropic_bodies.json` (written by the test, then committed)

**Interfaces:**
- Consumes: today's `_LLMWorker(messages, system_prompt, tools=..., registry=..., api_style=..., conversation=None)`.
- Produces: two fixture files that Task 6 must match byte for byte, and a `_drive(style, client, conv, reg)` helper that Task 6 rewrites for the new worker.

- [ ] **Step 1: Write the test and its capture mode**

```python
"""Request bodies of a two-round tool turn must not change (#104).

The fallback refactor moves vendor rendering from _tool_loop's hand-built
in-flight messages to Conversation.get_messages_for_api on a working copy.
Any byte that moves invalidates every provider's prefix cache (#47). This
file was written against the pre-refactor worker; its fixtures are the
contract. Never regenerate them after the refactor starts.

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
    """Run one tool turn through the worker as it exists today."""
    from freecad_ai.ui.chat_widget import _LLMWorker
    schema = (reg.to_anthropic_schema() if style == "anthropic"
              else reg.to_openai_schema())
    worker = _LLMWorker(conv.get_messages_for_api(api_style=style), "SYSTEM",
                        tools=schema, registry=reg, api_style=style,
                        conversation=None)
    worker._execute_tool_on_main_thread = (
        lambda n, a: {"success": True, "output": "ok", "error": ""})
    with patch("freecad_ai.llm.client.create_client_from_config",
               return_value=client), \
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
```

- [ ] **Step 2: Run it without fixtures to verify it fails**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_golden_request_bodies.py -q`
Expected:
- `test_request_bodies_match_the_golden_fixture` FAILs with `FileNotFoundError` for `golden_*_bodies.json`.
- `test_the_script_reaches_round_two` PASSes, for both styles. If it fails, fix the event script (field names in the SSE chunks), not the worker.

- [ ] **Step 3: Write the fixtures against today's code**

Run: `FREECAD_AI_WRITE_GOLDEN=1 env PYTHONPATH= .venv/bin/pytest tests/unit/test_golden_request_bodies.py -q`
Expected: 4 passed. Open both fixture files and confirm by eye that each holds 2 bodies:
- the OpenAI round-2 body has an assistant message with `"reasoning_content": "plan it"` and two `"role": "tool"` messages;
- the Anthropic round-2 body has a `tool_use` pair and two `tool_result` user messages.

- [ ] **Step 4: Run again without the env var**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_golden_request_bodies.py -q`
Expected: 4 passed.

- [ ] **Step 5: Full suite, then commit**

Run: the full test command. Expected: 2047 passed.

```bash
git add tests/unit/test_golden_request_bodies.py tests/unit/fixtures/golden_openai_bodies.json tests/unit/fixtures/golden_anthropic_bodies.json
git commit -m "test: golden request bodies for a two-round tool turn (#104)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `LLMError` kinds, per-client retries, and `create_client(profile=)`

**Files:**
- Modify: `freecad_ai/llm/client.py`:
  - `LLMError` (~line 65);
  - `_MAX_RETRIES` (~line 950);
  - `_http_post` (~972) and `_http_stream` (~1006);
  - `create_client` (~1162).
- Test: `tests/unit/test_llm_error_kinds.py` (new), `tests/unit/test_create_client.py`

**Interfaces:**
- Produces:
  - `LLMError(message, status: int | None = None, kind: str = "config")`, with attributes `.status` and `.kind` (`"unreachable"` or `"config"`);
  - `LLMClient.max_retries: int`, a class default of 5 that one client can override;
  - `create_client(cfg=None, utility=None, *, ..., cache_key="", profile: str | None = None)`.

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_llm_error_kinds.py`:

```python
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
```

Append to `tests/unit/test_create_client.py`:

```python
class TestCreateClientForAProfile:
    """#104: the fallback walker asks for a profile by label."""

    def _cfg(self):
        from freecad_ai.config import AppConfig, ProviderConfig
        c = AppConfig()
        c.profiles = {
            "chat": ProviderConfig(name="anthropic", model="m-chat",
                                   base_url="https://api.anthropic.com"),
            "backup": ProviderConfig(name="ollama", model="m-backup",
                                     base_url="http://localhost:11434/v1",
                                     params={"max_tokens": 1234}),
        }
        c.active_profile = "chat"
        return c

    def test_profile_builds_that_profile(self):
        from freecad_ai.llm.client import create_client
        client = create_client(self._cfg(), profile="backup",
                               cache_key="conv_1")
        assert (client.provider_name, client.model) == ("ollama", "m-backup")
        assert client.max_tokens == 1234
        assert client.cache_key == "conv_1"

    def test_no_profile_is_todays_chat_client(self):
        from freecad_ai.llm.client import create_client
        assert create_client(self._cfg()).model == "m-chat"

    def test_an_unknown_profile_falls_back_to_the_active_one(self):
        from freecad_ai.llm.client import create_client
        assert create_client(self._cfg(), profile="gone").model == "m-chat"
```

- [ ] **Step 2: Run to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_llm_error_kinds.py tests/unit/test_create_client.py -q`
Expected: FAIL:
- `AttributeError: 'LLMError' object has no attribute 'kind'`;
- `TypeError: create_client() got an unexpected keyword argument 'profile'`.

- [ ] **Step 3: Implement `LLMError`.** Replace the class:

```python
class LLMError(Exception):
    """Error communicating with the LLM provider.

    ``kind`` tells the fallback walker (#104) whether another profile is
    worth trying: ``"unreachable"`` (no connection, timeout, 5xx, 429) or
    ``"config"`` (the provider answered and refused: 4xx, bad setup).
    ``status`` is the HTTP code when there was one.
    """

    def __init__(self, message, status=None, kind="config"):
        super().__init__(message)
        self.status = status
        self.kind = kind
```

- [ ] **Step 4: Implement the retry budget and the classification.** Directly under `_BASE_BACKOFF = 2  # seconds`, add:

```python
    # A client that is not the last fallback candidate sets this to 0, so
    # a rate-limited profile hands over at once instead of backing off
    # for minutes (#104).
    max_retries = _MAX_RETRIES
```

In **both** `_http_post` and `_http_stream`, make these changes:

- Replace `for attempt in range(self._MAX_RETRIES + 1):` with `for attempt in range(self.max_retries + 1):`.
- In the 429 branch, replace `attempt < self._MAX_RETRIES` with `attempt < self.max_retries`, and replace the log argument `self._MAX_RETRIES` with `self.max_retries`.
- Replace the three `raise` lines with:

```python
                raise LLMError(
                    f"HTTP {e.code}: {e.reason}\n{error_body}", status=e.code,
                    kind="unreachable" if e.code >= 500 or e.code == 429
                    else "config")
            except urllib.error.URLError as e:
                raise LLMError(f"Connection error: {e.reason}",
                               kind="unreachable")
            except Exception as e:
                # A timeout is an OSError; anything else is a local fault.
                raise LLMError(f"Request failed: {e}",
                               kind="unreachable" if isinstance(e, OSError)
                               else "config")
```

In `_http_stream`, replace `for raw_line in resp:` with `for raw_line in self._read_lines(resp):`, and add this method right after `_http_stream`:

```python
    @staticmethod
    def _read_lines(resp):
        """Yield the response's lines; a dropped read is an LLMError.

        A server that sends headers and then stalls times out here, before
        the first event, which is exactly when the fallback walker still
        switches profiles (#104).
        """
        try:
            yield from resp
        except OSError as e:
            raise LLMError(f"Request failed: {e}", kind="unreachable") from e
```

- [ ] **Step 5: Implement `create_client(profile=)`.** Add `profile: str | None = None` after `cache_key: str = ""` in the signature. Add this paragraph to the docstring:

```
    ``profile`` names a profile by label and wins over ``utility``; the
    fallback walker uses it (#104). An unknown label means the same as
    None, so a profile deleted mid-turn never breaks the chat.
```

Replace `profile = resolve_profile(cfg, utility)` and each later use of the local name `profile` in the function:

```python
    chosen = (cfg.profiles[profile] if profile in cfg.profiles
              else resolve_profile(cfg, utility))

    params = resolve_params(cfg, chosen)
    row_cap = take_max_tokens_row(params, _profile_label(cfg, chosen))
```

Also change the `LLMClient(...)` arguments: `provider_name=chosen.name`, `base_url=chosen.base_url`, `api_key=chosen.api_key or cfg.provider_keys.get(chosen.name, "")` and `model=chosen.model`.

- [ ] **Step 6: Run the new tests**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_llm_error_kinds.py tests/unit/test_create_client.py -q`
Expected: PASS.

- [ ] **Step 7: Full suite, then commit**

Run: the full test command. Expected: all pass.

```bash
git add freecad_ai/llm/client.py tests/unit/test_llm_error_kinds.py tests/unit/test_create_client.py
git commit -m "feat(llm): classify LLMError and build a client by profile (#104)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `fallback_profiles` config field and profile capability helpers

**Files:**
- Modify: `freecad_ai/config.py`:
  - `AppConfig` fields (~line 532, after `utility_profiles`);
  - `from_dict` (before its final `return cfg`);
  - `supports_vision` / `supports_tools` (~731–753).
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces:
  - `AppConfig.fallback_profiles: list[str]`, sanitized on load;
  - `profile_supports_vision(profile) -> bool` and `profile_supports_tools(profile) -> bool`, module functions in `freecad_ai.config`.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_config.py`:

```python
class TestFallbackProfiles:
    """#104: one global ordered list of profiles to try."""

    def _data(self, fallback):
        return {
            "profiles": {"chat": {"name": "anthropic"},
                         "local": {"name": "ollama"},
                         "cloud": {"name": "openai"}},
            "active_profile": "chat",
            "fallback_profiles": fallback,
        }

    def test_defaults_to_empty(self):
        assert AppConfig().fallback_profiles == []

    def test_order_survives_a_save(self, tmp_config_dir):
        c = AppConfig.from_dict(self._data(["cloud", "local"]))
        save_config(c)
        assert load_config().fallback_profiles == ["cloud", "local"]

    def test_unknown_labels_are_dropped_with_a_warning(self, caplog):
        c = AppConfig.from_dict(self._data(["gone", "local"]))
        assert c.fallback_profiles == ["local"]
        assert 'fallback profile "gone" dropped — no such profile' in caplog.text

    def test_duplicates_keep_the_first(self):
        c = AppConfig.from_dict(self._data(["local", "cloud", "local"]))
        assert c.fallback_profiles == ["local", "cloud"]

    def test_the_active_profile_is_kept(self):
        c = AppConfig.from_dict(self._data(["chat", "local"]))
        assert c.fallback_profiles == ["chat", "local"]

    @pytest.mark.parametrize("bad", ["local", None, {"a": 1}, [["x"]]])
    def test_a_malformed_value_loads_as_empty_or_filtered(self, bad):
        assert AppConfig.from_dict(self._data(bad)).fallback_profiles == []


class TestProfileCapabilities:
    def test_vision_override_beats_detection(self):
        from freecad_ai.config import profile_supports_vision
        p = ProviderConfig(name="ollama", vision_detected=True,
                           vision_override=False)
        assert profile_supports_vision(p) is False

    def test_tools_detection_beats_the_provider_flag(self):
        from freecad_ai.config import profile_supports_tools
        p = ProviderConfig(name="anthropic", tools_detected=False)
        assert profile_supports_tools(p) is False

    def test_the_properties_still_read_the_active_profile(self):
        c = AppConfig()
        c.profiles = {"a": ProviderConfig(name="ollama", vision_override=True)}
        c.active_profile = "a"
        assert c.supports_vision is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_config.py -q -k "FallbackProfiles or ProfileCapabilities"`
Expected: FAIL. `AppConfig` has no attribute `fallback_profiles`, and `cannot import name 'profile_supports_vision'`.

- [ ] **Step 3: Implement the field.** In `AppConfig`, right after the `utility_profiles` field:

```python
    # Profiles to try, in order, when the chat profile can't answer (#104).
    # Walked once per turn. Empty = off. The active profile may appear
    # here; it is skipped at run time, so switching the active profile
    # never rewrites this list.
    fallback_profiles: list = field(default_factory=list)
```

- [ ] **Step 4: Implement the load sanitizing.** Add this module function right before `class AppConfig`:

```python
def _clean_fallback_profiles(labels, profiles) -> list:
    """Known labels only, first occurrence kept, order preserved (#104)."""
    if not isinstance(labels, list):
        return []
    clean = []
    for label in labels:
        if not isinstance(label, str) or label not in profiles:
            logger.warning('fallback profile "%s" dropped — no such profile',
                           label)
        elif label not in clean:
            clean.append(label)
    return clean
```

In `from_dict`, between `cls._adopt_legacy_capabilities(cfg, data)` and `return cfg`, add:

```python
        cfg.fallback_profiles = _clean_fallback_profiles(
            cfg.fallback_profiles, cfg.profiles)
```

- [ ] **Step 5: Implement the capability helpers.** Add them right after `_clean_fallback_profiles`, moving the bodies out of the two properties:

```python
def profile_supports_vision(profile) -> bool:
    """Whether ``profile``'s model takes images: override, then detection."""
    if profile.vision_override is not None:
        return profile.vision_override
    if profile.vision_detected is not None:
        return profile.vision_detected
    return False


def profile_supports_tools(profile) -> bool:
    """Whether ``profile``'s model calls tools.

    Detected capability (from Ollama /api/show) takes precedence — it
    catches the case where someone picks an embedding/reranker model
    as the main model on a provider that the static table marks as
    tool-capable. Otherwise fall back to the provider-wide flag.
    """
    if profile.tools_detected is not None:
        return profile.tools_detected
    from .llm.providers import supports_tools as _provider_supports_tools
    return _provider_supports_tools(profile.name)
```

Replace the two property bodies:

```python
    @property
    def supports_vision(self) -> bool:
        """Whether the active profile's LLM supports vision."""
        return profile_supports_vision(self.provider)

    @property
    def supports_tools(self) -> bool:
        """Whether the active profile's LLM supports tool calling."""
        return profile_supports_tools(self.provider)
```

- [ ] **Step 6: Run the new tests**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_config.py -q`
Expected: PASS.

- [ ] **Step 7: Full suite, then commit**

Run: the full test command. Expected: all pass.

```bash
git add freecad_ai/config.py tests/unit/test_config.py
git commit -m "feat(config): fallback_profiles list and per-profile capability helpers (#104)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `fallback.py` — the chain and the walker

**Files:**
- Create: `freecad_ai/llm/fallback.py`
- Test: `tests/unit/test_fallback.py` (new)

**Interfaces:**
- Consumes:
  - `LLMError.kind` / `.status` and `create_client(cfg, profile=..., cache_key=...)` (Task 2);
  - `profile_supports_tools` / `profile_supports_vision` and `AppConfig.fallback_profiles` (Task 3).
- Produces:
  - `fallback_chain(cfg) -> list[str]`;
  - `short_reason(exc) -> str`;
  - `FallbackWalker(cfg, *, cache_key="", needs_tools=False, needs_vision=False, is_interrupted=callable, on_note=callable)`;
  - `.open(round_no: int, request: Callable[[client], Iterable]) -> tuple[client, Iterator] | None`;
  - `.attempts: list[dict]`, each entry having the keys `round`, `profile`, `outcome` and `error`.
  - `open` returns None only when interrupted. It raises `LLMError` when every profile has failed; with an empty list, the original error is raised unchanged.

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_fallback.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_fallback.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'freecad_ai.llm.fallback'`.

- [ ] **Step 3: Implement** — create `freecad_ai/llm/fallback.py`:

```python
"""Fallback profiles: try the next profile when one can't answer (#104).

One pass per turn over [active profile] + cfg.fallback_profiles, with a
forward-only cursor: each request starts at the profile that answered
last, a profile that failed is not tried again this turn, and the next
turn starts over at the chat profile.

A request has succeeded once its first stream event has arrived. The
client's generators are lazy -- the connection is only made at the first
next() -- so the walker pulls that event itself and hands the caller an
iterator that yields it again. An error after it ends the turn as it
always has.
"""

import itertools
import logging
import re

from ..config import profile_supports_tools, profile_supports_vision
from .client import LLMError, create_client

logger = logging.getLogger(__name__)


def fallback_chain(cfg) -> list:
    """The labels a turn may try, in order: the chat profile first."""
    cfg.provider  # ensures active_profile names a real profile
    chain = [cfg.active_profile]
    for label in cfg.fallback_profiles:
        if label in cfg.profiles and label not in chain:
            chain.append(label)
    return chain


def short_reason(exc) -> str:
    """A few words for a log line or a chat note."""
    status = getattr(exc, "status", None)
    if status:
        return f"HTTP {status}"
    text = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    for prefix in ("Connection error: ", "Request failed: "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = re.sub(r"^\[Errno -?\d+\]\s*", "", text)
    return text[:1].lower() + text[1:]


class FallbackWalker:
    """Opens each request of one turn on the first profile that answers."""

    def __init__(self, cfg, *, cache_key="", needs_tools=False,
                 needs_vision=False, is_interrupted=lambda: False,
                 on_note=lambda text: None):
        self.cfg = cfg
        self.cache_key = cache_key
        self.chain = fallback_chain(cfg)
        self.cursor = 0
        self.attempts = []
        self.active = len(self.chain) > 1
        self._needs = {"tools": needs_tools, "vision": needs_vision}
        self._is_interrupted = is_interrupted
        self._on_note = on_note
        self._segments = []
        self._failures = []
        self._clients = {}
        # Only the last profile that can actually be tried keeps the 429
        # backoff; the others hand over at once.
        self._last_eligible = max(
            i for i in range(len(self.chain)) if self._missing(i) is None)

    def _missing(self, i):
        """The capability profile ``i`` lacks for this turn, or None."""
        if i == 0:
            return None          # the turn was prepared for it
        profile = self.cfg.profiles[self.chain[i]]
        if self._needs["tools"] and not profile_supports_tools(profile):
            return "tools"
        if self._needs["vision"] and not profile_supports_vision(profile):
            return "vision"
        return None

    def _client(self, i):
        if i not in self._clients:
            client = create_client(
                self.cfg, profile=None if i == 0 else self.chain[i],
                cache_key=self.cache_key)
            if i < self._last_eligible:
                client.max_retries = 0
            self._clients[i] = client
        return self._clients[i]

    def _record(self, round_no, label, outcome, error=""):
        if self.active:
            self.attempts.append({"round": round_no, "profile": label,
                                  "outcome": outcome, "error": error})

    def open(self, round_no, request):
        """Return (client, events) from the first profile that answers.

        None means Stop was pressed before an answer arrived.
        """
        while self.cursor < len(self.chain):
            if self._is_interrupted():
                return None
            i, label = self.cursor, self.chain[self.cursor]
            cap = self._missing(i)
            if cap:
                logger.warning("FreeCAD AI: %s skipped — no %s", label, cap)
                self._record(round_no, label, "skipped", f"no {cap}")
                self._segments.append(f"{label} skipped (no {cap})")
                self._failures.append(f"{label}: skipped (no {cap})")
                self.cursor += 1
                continue
            client = self._client(i)
            try:
                events = iter(request(client))
                head = [next(events)]
            except StopIteration:
                head = []
            except LLMError as exc:
                if not self.active:
                    raise
                reason = short_reason(exc)
                if i + 1 < len(self.chain):
                    logger.warning("FreeCAD AI: %s failed — %s; trying %s",
                                   label, reason, self.chain[i + 1])
                else:
                    logger.warning("FreeCAD AI: %s failed — %s; no profile left",
                                   label, reason)
                self._record(round_no, label, "failed", reason)
                if getattr(exc, "kind", "config") == "unreachable":
                    self._segments.append(f"{label} couldn't be reached ({reason})")
                else:
                    self._segments.append(f"{label} was refused ({reason})")
                self._failures.append(f"{label}: {reason}")
                self.cursor += 1
                continue
            self._record(round_no, label, "answered")
            if self._segments:
                logger.warning("FreeCAD AI: answered by %s (fallback %d of %d)",
                               label, i, len(self.chain) - 1)
                self._on_note("⚠ " + "; ".join(self._segments)
                              + f" — answered by {label}")
                self._segments = []
            return client, itertools.chain(head, events)
        raise LLMError("No profile could answer — " + " · ".join(self._failures))
```

`events` is still bound after a `StopIteration`, because `iter(...)` ran before `next(...)`.

- [ ] **Step 4: Run the tests**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_fallback.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite, then commit**

Run: the full test command. Expected: all pass.

```bash
git add freecad_ai/llm/fallback.py tests/unit/test_fallback.py
git commit -m "feat(llm): fallback walker with a forward-only cursor (#104)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Conversation — pinned window, working copy, image check

**Files:**
- Modify: `freecad_ai/core/conversation.py` (`get_messages_for_api` ~line 174; add three methods next to it)
- Test: `tests/unit/test_conversation_window.py` (new)

**Interfaces:**
- Produces:
  - `Conversation.window_start(max_chars=100000) -> int`, the index of the first message kept;
  - `get_messages_for_api(..., start_index: int | None = None)`;
  - `Conversation.fork_for_turn() -> Conversation`;
  - `Conversation.has_images(start_index=0) -> bool`.

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_conversation_window.py`:

```python
"""A turn pins its truncation window and records rounds on a copy (#104)."""

from freecad_ai.core.conversation import Conversation


def _long(n=6, size=40):
    conv = Conversation()
    for i in range(n):
        conv.add_user_message(f"u{i}" + "x" * size)
        conv.add_assistant_message(f"a{i}" + "y" * size)
    return conv


def test_default_output_is_unchanged():
    conv = _long()
    assert conv.get_messages_for_api(max_chars=200) == \
        conv.get_messages_for_api(max_chars=200,
                                  start_index=conv.window_start(200))


def test_window_start_is_the_first_kept_message():
    conv = _long()
    start = conv.window_start(200)
    rendered = conv.get_messages_for_api(max_chars=200)
    assert rendered[0]["content"] == conv.messages[start]["content"]


def test_a_pinned_start_ignores_later_growth():
    conv = _long()
    start = conv.window_start(200)
    before = conv.get_messages_for_api(start_index=start)
    conv.add_user_message("z" * 500)
    after = conv.get_messages_for_api(start_index=start)
    assert after[:len(before)] == before


def test_a_pinned_start_still_opens_on_a_user_message():
    conv = _long()
    rendered = conv.get_messages_for_api(start_index=1)   # an assistant
    assert rendered[0]["role"] == "user"


def test_the_fork_shares_history_but_not_the_list():
    conv = _long(1)
    fork = conv.fork_for_turn()
    fork.add_tool_result("t1", "ok")
    assert len(conv.messages) == 2
    assert len(fork.messages) == 3
    assert fork.conversation_id == conv.conversation_id


def test_has_images_looks_from_the_start_index():
    conv = Conversation()
    conv.add_user_message("look", images=[{"type": "image", "data": "AAAA",
                                           "mime_type": "image/png"}])
    conv.add_assistant_message("seen")
    conv.add_user_message("again")
    assert conv.has_images() is True
    assert conv.has_images(start_index=2) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_conversation_window.py -q`
Expected: FAIL with `AttributeError: 'Conversation' object has no attribute 'window_start'`.

- [ ] **Step 3: Implement.** Add `start_index: int | None = None` as the last parameter of `get_messages_for_api`, and add this to its docstring's Args:

```
            start_index: The first message to keep, from window_start().
                A turn computes it once so later rounds of the tool loop
                don't drop older messages from under the prompt cache
                (#104, #47). None = compute it now from max_chars.
```

Move the backward walk into a new method, right before `get_messages_for_api`:

```python
    def window_start(self, max_chars: int = 100000) -> int:
        """Index of the oldest message the max_chars window keeps.

        Walks backwards from the newest message and never splits a
        tool_call/tool_result pair.
        """
        total_chars = 0
        start = len(self.messages)
        i = len(self.messages) - 1
        while i >= 0:
            msg = self.messages[i]
            if msg["role"] == "tool_result":
                j = i - 1
                while j >= 0 and self.messages[j]["role"] == "tool_result":
                    j -= 1
                if j >= 0 and self.messages[j]["role"] == "assistant":
                    j -= 1
                group_chars = sum(self._content_chars(m.get("content", ""))
                                  for m in self.messages[j + 1:i + 1])
                if total_chars + group_chars > max_chars and start < len(self.messages):
                    break
                total_chars += group_chars
                start = j + 1
                i = j
                continue
            # The snapshot is billed like any other text, so it counts
            # against the budget even though it lives beside the content.
            msg_chars = (self._content_chars(msg.get("content", ""))
                         + len(msg.get("doc_context", "")))
            if total_chars + msg_chars > max_chars and start < len(self.messages):
                break
            total_chars += msg_chars
            start = i
            i -= 1
        return start
```

In `get_messages_for_api`, replace everything from the comment `# Walk backwards, collecting messages while respecting max_chars` down to and including the final `i -= 1` of that loop with:

```python
        if start_index is None:
            start_index = self.window_start(max_chars)
        result = list(self.messages[start_index:])
```

Keep the `while result and result[0]["role"] not in ("user",): result.pop(0)` loop and everything after it.

`start < len(self.messages)` plays the role of the old `and result` test: the newest message, or the newest group, is always kept.

Add right after `get_messages_for_api`:

```python
    def fork_for_turn(self) -> "Conversation":
        """A working copy for one turn's tool rounds (#104).

        The worker records each round here and renders every request from
        it; the real conversation is written once, at the end of the turn,
        by the widget. Message dicts are shared, never mutated.
        """
        return Conversation(messages=list(self.messages),
                            conversation_id=self.conversation_id,
                            created_at=self.created_at, model=self.model)

    def has_images(self, start_index: int = 0) -> bool:
        """Whether any message from start_index on carries an image block."""
        return any(
            isinstance(msg.get("content"), list)
            and any(b.get("type") == "image" for b in msg["content"])
            for msg in self.messages[start_index:])
```

- [ ] **Step 4: Run the new tests and the existing conversation tests**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_conversation_window.py tests/unit/test_conversation.py -q`
Expected: PASS. The existing truncation tests are the proof that the extracted walk matches the old one.

- [ ] **Step 5: Full suite, then commit**

Run: the full test command. Expected: all pass, including `test_golden_request_bodies.py`.

```bash
git add freecad_ai/core/conversation.py tests/unit/test_conversation_window.py
git commit -m "feat(conversation): pinned window start, turn fork, image check (#104)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The worker renders each request for its client

**Files:**
- Modify: `freecad_ai/ui/chat_widget.py`, `_LLMWorker` (lines 183–555)
- Create: `tests/unit/_worker_harness.py`, `tests/unit/test_worker_render_per_client.py`
- Modify tests:
  - `tests/unit/test_golden_request_bodies.py` (`_drive` only);
  - `tests/unit/test_act_truncation.py`;
  - `tests/unit/test_reasoning_on_text_only_turns.py`;
  - `tests/unit/test_worker_error_traceback.py`;
  - `tests/unit/test_prompt_cache_key.py`;
  - `tests/unit/test_preserve_reasoning_setting.py`;
  - the docstrings in `tests/unit/test_prompt_cache_prefix.py:290` and `tests/unit/test_reasoning_history_roundtrip.py:8`.

**Interfaces:**
- Consumes:
  - `FallbackWalker` / `.open` / `.attempts` (Task 4);
  - `fork_for_turn`, `get_messages_for_api(start_index=)` (Task 5).
- Produces:
  - `_LLMWorker(conversation, system_prompt, *, registry=None, filter_names=None, describe_fn=None, strip_images=False, start_index=0, needs_vision=False, parent=None)`;
  - the signal `fallback_note = Signal(str)`;
  - `detach()`;
  - the attribute `fallback_attempts: list`;
  - `_emit(signal, *args)`, `_request(client)`, `_apply_client(client)`;
  - `_simple_stream(walker)`, `_tool_loop(walker)`.

- [ ] **Step 1: Write the shared harness** — create `tests/unit/_worker_harness.py`:

```python
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
```

- [ ] **Step 2: Write the failing regression tests** — create `tests/unit/test_worker_render_per_client.py`:

```python
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
```

- [ ] **Step 3: Run to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_worker_render_per_client.py -q`
Expected: FAIL, mostly with `TypeError` from the old constructor (positional `messages`), plus `AttributeError: ... 'detach'` / `'_apply_client'`.

- [ ] **Step 4: Rewrite the worker's constructor, signals and helpers.** In `_LLMWorker`:

- add `fallback_note = Signal(str)  # a fallback profile answered (#104)` after `vision_note`;
- replace `__init__` and `run`, and replace `_wrap_describe_fn` with the methods below. Keep the class docstring, but change step 4 to "Record the round on the turn's working copy, loop back to step 1".

```python
    def __init__(self, conversation, system_prompt, *, registry=None,
                 filter_names=None, describe_fn=None, strip_images=False,
                 start_index=0, needs_vision=False, parent=None):
        super().__init__(parent)
        # The turn arrives neutral: no vendor format, no rendered messages.
        # Each request is rendered for the client about to answer it,
        # which after a fallback may be a different vendor (#104).
        self.conversation = conversation
        self._work = conversation.fork_for_turn()
        self.system_prompt = system_prompt
        self.registry = registry
        self.filter_names = filter_names
        self.describe_fn = describe_fn
        self.strip_images = strip_images
        self.start_index = start_index
        self._needs_vision = needs_vision
        self.api_style = "openai"  # the answering client's, set per request
        self._detached = False
        self.fallback_attempts = []
        self._describe_cache = {}
        self._full_response = ""
        self._thinking_text = ""
        self._tool_results = []
        self._tool_result_ready = QtCore.QMutex()
        self._tool_result_wait = QtCore.QWaitCondition()
        self._pending_result = None
        self._max_tool_turns = get_config().max_tool_turns  # 0 = endless
        self._strip_thinking = False  # resolved per client in _apply_client
        self._optimize_caching = False
        self._preserve_reasoning = True
        self._final_reasoning = ""  # thinking of the turn that ends the run (#84)
        self._tool_timeline = []  # timing data for summary visualization
        self._response_truncated = False  # response hit the output-token limit
        self._response_max_tokens = None  # the cap the client ran with (#103)

    def detach(self):
        """The widget gave up on this run (Stop, then 2 s of silence).
        From now on nothing leaves this thread (#104)."""
        self._detached = True
        self.requestInterruption()

    def _emit(self, signal, *args):
        if not self._detached:
            signal.emit(*args)

    def run(self):
        try:
            from ..config import get_config as _get_config
            from ..llm.fallback import FallbackWalker
            walker = FallbackWalker(
                _get_config(),
                # One cache key per conversation, whichever profile answers,
                # so the provider keeps routing to the cluster that holds
                # the prefix (#47). It used to reach the client only when a
                # describe_fn was set.
                cache_key=self.conversation.conversation_id,
                needs_tools=self.registry is not None,
                needs_vision=self._needs_vision,
                is_interrupted=self.isInterruptionRequested,
                on_note=lambda text: self._emit(self.fallback_note, text))
            self.fallback_attempts = walker.attempts
            if self.registry is None:
                self._simple_stream(walker)
            else:
                self._tool_loop(walker)
        except Exception as e:
            # The bubble gets the short form; the Report view gets the
            # stack. #89 arrived as the bare line "'NoneType' object is
            # not iterable" -- true, and useless: three lines in this
            # codebase could have produced it, and the reporter had no
            # way to tell us which. A turn that dies is a bug report
            # waiting to be written, so leave it something to quote.
            logger.exception("Chat turn failed: %s", e)
            self._emit(self.error_occurred, str(e))

    def _apply_client(self, client):
        """Take the per-request settings from the client that answered."""
        from ..config import get_config as _get_config
        from ..llm.client import should_strip_thinking
        self.api_style = client.api_style
        # The truncation warning quotes this: the cap may come from a
        # profile row, and cfg can change while the turn runs (#103).
        self._response_max_tokens = client.max_tokens
        self._strip_thinking = should_strip_thinking(
            client.model, _get_config().strip_thinking_history)
        self._optimize_caching = _get_config().optimize_prompt_caching
        self._preserve_reasoning = _get_config().preserve_reasoning_history

    def _request(self, client):
        """Render the turn for ``client`` and open its stream (lazily)."""
        from ..config import get_config as _get_config
        from ..llm.client import should_strip_thinking
        messages = self._work.get_messages_for_api(
            api_style=client.api_style,
            strip_thinking=should_strip_thinking(
                client.model, _get_config().strip_thinking_history),
            strip_images=self.strip_images,
            describe_fn=self._memo_describe if self.describe_fn else None,
            start_index=self.start_index)
        tools = None
        if self.registry is not None:
            tools = (self.registry.to_anthropic_schema(self.filter_names)
                     if client.api_style == "anthropic"
                     else self.registry.to_openai_schema(self.filter_names))
        return client.stream_with_tools(messages, system=self.system_prompt,
                                        tools=tools)

    def _memo_describe(self, data_url):
        """describe_fn, once per image per turn, with one status note.

        Every request re-renders the history, so without this an image
        would be sent to the vision tool once per round and per profile.
        A failure is remembered too: the next render gets the same
        placeholder instead of a second slow MCP call.
        """
        import hashlib
        key = hashlib.sha256(data_url.encode("utf-8")).hexdigest()
        if key not in self._describe_cache:
            try:
                self._describe_cache[key] = (True, self.describe_fn(data_url))
                self._emit(self.vision_note,
                           "Image auto-described by llm-vision-mcp")
            except Exception as e:
                self._describe_cache[key] = (False, str(e))
                self._emit(self.vision_note, f"Image description failed: {e}")
        ok, value = self._describe_cache[key]
        if not ok:
            raise RuntimeError(value)
        return value
```

- [ ] **Step 5: Rewrite `_simple_stream`.** Keep the docstring and add one sentence to it: "It opens through the fallback walker, so a Plan turn can fail over too (#104)."

```python
    def _simple_stream(self, walker):
        opened = walker.open(0, self._request)
        if opened is None:
            self._full_response += "\n\n_⏹ Stopped by user._"
            self._emit(self.response_finished, self._full_response)
            return
        client, events = opened
        self._apply_client(client)
        thinking_parts = []
        for event in events:
            if self.isInterruptionRequested():
                break
            if event.type == "text_delta":
                self._full_response += event.text
                self._emit(self.token_received, event.text)
            elif event.type == "thinking_delta":
                thinking_parts.append(event.text)
                self._thinking_text += event.text
                self._emit(self.thinking_received, event.text)
            elif event.type == "done":
                break
        # A Plan reply is the turn that ends the run, so it never reaches
        # _tool_results; _final_reasoning is how it travels to the writer.
        self._final_reasoning = reasoning_to_persist(
            "".join(thinking_parts), self._strip_thinking,
            self._optimize_caching, self.api_style, self._preserve_reasoning)
        self._response_truncated = client.response_truncated
        self._emit(self.response_finished, self._full_response)
```

- [ ] **Step 6: Rewrite `_tool_loop`.** Change the signature to `def _tool_loop(self, walker):` and make these edits in order:

  1. Delete `messages = list(self.messages)`.
  2. Replace the stream opening, `for event in client.stream_with_tools(messages, system=self.system_prompt, tools=self.tools):`, with:

```python
            opened = walker.open(turn, self._request)
            if opened is None:
                break          # Stop before any profile answered
            client, events = opened
            self._apply_client(client)

            for event in events:
```

  3. Inside the loop, replace every `self.<signal>.emit(...)` with `self._emit(self.<signal>, ...)`. This applies to `token_received`, `thinking_received`, `tool_call_started`, `tool_call_finished` and every `response_finished`, including the tail after the loop.
  4. Replace the whole block from `# Add assistant message to local messages for next turn` down to and including `messages.append(assistant_msg)` with:

```python
            # Record the round on the turn's working copy; the next request
            # renders it for whichever client answers that one (#104). The
            # echo follows the answering model's strip rule, never
            # preserve_reasoning_history -- that one only decides what is
            # stored at the end of the turn.
            self._work.add_assistant_message(
                turn_text, tool_calls=tc_dicts,
                reasoning_content=turn_thinking if not self._strip_thinking else "")
```

  5. Replace `tool_result_messages = []` with `results = []`.
  6. Replace the per-tool `if self.api_style == "anthropic": tool_result_messages.append(...) else: ...` block with:

```python
                self._work.add_tool_result(tc.id, result_text)
                results.append({"tool_call_id": tc.id, "content": result_text})
```

  7. Delete `messages.extend(tool_result_messages)`.
  8. In the `self._tool_results.append({...})` entry, replace the `"results": [...]` comprehension with `"results": results,`.
  9. In `_execute_tool_on_main_thread`, replace `self.tool_exec_requested.emit(tool_name, json.dumps(arguments))` with `self._emit(self.tool_exec_requested, tool_name, json.dumps(arguments))`.

  Leave the tail after the loop as it is. When `walker.open` returns None, the `if self.isInterruptionRequested():` check there produces "⏹ Stopped by user". Check that `json` is still used in the file (it is, by `_execute_tool_on_main_thread`).

- [ ] **Step 7: Migrate the golden driver.** In `tests/unit/test_golden_request_bodies.py`, replace `_drive` only (the fixtures and scripts stay):

```python
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
```

Update the module docstring's second paragraph to end: "The driver now runs the refactored worker; the fixtures are still the pre-refactor bytes."

- [ ] **Step 8: Migrate the other worker tests.**

`tests/unit/test_act_truncation.py`:
- add `from tests.unit._worker_harness import OneClientWalker, add_seams` to the imports;
- in `_fake_worker`, wrap the return: `return add_seams(SimpleNamespace(...))`, deleting the `messages=`, `tools=` and `conversation=None` entries;
- in `_run`, replace `_LLMWorker._tool_loop(worker, client)` with `_LLMWorker._tool_loop(worker, OneClientWalker(client))`;
- replace `test_run_records_the_clients_cap` with:

```python
    def test_the_answering_clients_cap_is_recorded(self, tmp_config_dir):
        from freecad_ai.ui.chat_widget import _LLMWorker
        worker = SimpleNamespace(_response_max_tokens=None)
        _LLMWorker._apply_client(worker, SimpleNamespace(
            model="qwen3:8b", max_tokens=16000, api_style="openai"))  # type: ignore[arg-type]
        assert worker._response_max_tokens == 16000
```

  Drop the `client_mod` monkeypatch from it.

`tests/unit/test_reasoning_on_text_only_turns.py`:
- import `from tests.unit._worker_harness import OneClientWalker, add_seams`;
- at the end of `_Worker.__init__`, call `add_seams(self)`;
- in `_run_simple`, replace `cw._LLMWorker._simple_stream(worker, client)` with `cw._LLMWorker._simple_stream(worker, OneClientWalker(client))`;
- if the file has a `_run_tools` helper, do the same with `_tool_loop`.

`add_seams` sets `_apply_client` to a no-op, so the `api_style` / `_strip_thinking` values that `_Worker` sets explicitly stay in force.

`tests/unit/test_worker_error_traceback.py`: replace `_Worker` and the test with:

```python
class _Worker:
    """The attributes ``run`` touches before it reaches its handler."""

    def __init__(self):
        from freecad_ai.core.conversation import Conversation
        self.conversation = Conversation()
        self.registry = None
        self._needs_vision = False
        self.fallback_note = _Signal()
        self.error_occurred = _Signal()
        self._simple_stream = lambda walker: walker.open(0, lambda c: iter(()))

    def isInterruptionRequested(self):
        return False

    def _emit(self, signal, *args):
        signal.emit(*args)


def test_run_logs_the_traceback_and_emits_the_short_message(caplog, tmp_config_dir):
    worker = _Worker()
    boom = TypeError("'NoneType' object is not iterable")

    with patch("freecad_ai.llm.fallback.create_client", side_effect=boom):
        with caplog.at_level(logging.ERROR, logger="freecad_ai.ui.chat_widget"):
            cw._LLMWorker.run(worker)
```

Keep the assertions below that line unchanged.

`tests/unit/test_prompt_cache_key.py`, class `TestSomethingActuallySuppliesIt`: replace `_run` and delete `test_a_worker_without_a_conversation_passes_nothing`, because a worker always has a conversation now:

```python
    def _run(self, conversation, monkeypatch, tmp_config_dir):
        from freecad_ai.ui import chat_widget

        seen = {}

        def fake_create(cfg, *, profile=None, cache_key="", **k):
            seen["cache_key"] = cache_key
            return _client(prompt_caching=True, cache_key=cache_key)

        monkeypatch.setattr("freecad_ai.llm.fallback.create_client",
                            fake_create)
        worker = chat_widget._LLMWorker(conversation, "S")
        worker._simple_stream = lambda walker: seen.setdefault(
            "client", walker.open(0, lambda c: iter(()))[0])
        worker.run()
        return seen

    def test_the_worker_passes_the_conversation_id(self, monkeypatch,
                                                   tmp_config_dir):
        conv = Conversation()

        seen = self._run(conv, monkeypatch, tmp_config_dir)

        assert seen["cache_key"] == conv.conversation_id
        assert seen["client"].cache_key == conv.conversation_id
```

  If the module has no PySide skip guard, add the same `try: import PySide6 ... pytest.skip(...)` guard that `test_worker_error_traceback.py` uses.

`tests/unit/test_preserve_reasoning_setting.py`: in `test_run_resolves_the_flag_from_the_config`, replace `inspect.getsource(cw._LLMWorker.run)` with `inspect.getsource(cw._LLMWorker._apply_client)`, and rename the test to `test_apply_client_resolves_the_flag_from_the_config`.

Docstrings:
- `test_prompt_cache_prefix.py:290`: replace the words that say the worker "appends vendor-shaped messages" or "builds in-flight messages" with "records each round on a working copy of the conversation and re-renders it per request (#104)".
- `test_reasoning_history_roundtrip.py:8`: make the same wording change.

Read each line before editing it, and change only the sentence that describes the old mechanism.

- [ ] **Step 9: Run the worker tests and the golden test**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_worker_render_per_client.py tests/unit/test_golden_request_bodies.py tests/unit/test_act_truncation.py tests/unit/test_reasoning_on_text_only_turns.py tests/unit/test_worker_error_traceback.py tests/unit/test_prompt_cache_key.py tests/unit/test_preserve_reasoning_setting.py -q`
Expected: PASS. A golden mismatch means the working-copy rendering differs from the old in-flight messages. Fix the worker (the reasoning echo, the text-block rule, the tool-result shape), never the fixture.

- [ ] **Step 10: Full suite, then commit**

Run: the full test command. Expected: all pass. The widget still calls the old constructor, but no unit test runs `_continue_send` end to end. If one does, fix it in Task 7, and until then mark it with `pytest.mark.xfail(reason="Task 7", strict=True)`.

```bash
git add freecad_ai/ui/chat_widget.py tests/unit/_worker_harness.py tests/unit/test_worker_render_per_client.py tests/unit/test_golden_request_bodies.py tests/unit/test_act_truncation.py tests/unit/test_reasoning_on_text_only_turns.py tests/unit/test_worker_error_traceback.py tests/unit/test_prompt_cache_key.py tests/unit/test_preserve_reasoning_setting.py tests/unit/test_prompt_cache_prefix.py tests/unit/test_reasoning_history_roundtrip.py
git commit -m "refactor(chat): render each request for the client that answers it (#104)" -m "The worker now gets the conversation instead of vendor-shaped messages, records tool rounds on a fork, and opens every request through the fallback walker. Fixes Plan mode rendering OpenAI style for Anthropic profiles and the cache key reaching the client only with a describe_fn." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: The widget — one start path, Stop that detaches, the note and the log

**Files:**
- Modify: `freecad_ai/ui/chat_widget.py`:
  - `ChatDockWidget.__init__` (next to `self._worker = None`, ~line 858);
  - `_send_message` (~1417);
  - `_continue_send` (~1970–2136);
  - `_save_session_log` (~2138);
  - `_on_vision_note` (~2517; add `_on_fallback_note` after it);
  - `_handle_execution_error` (~2580–2649).
- Test: `tests/unit/test_chat_widget_fallback.py` (new)

**Interfaces:**
- Consumes: the Task 6 constructor, `fallback_note`, `detach()`, `fallback_attempts`; Task 5's `window_start()` / `has_images()`.
- Produces:
  - `ChatDockWidget._WORKER_SLOTS`;
  - `_start_worker(conversation, system_prompt, **worker_kwargs)`;
  - `_schedule_detach(worker)`, `_detach_if_stuck(worker)`, `_release_detached(worker)`;
  - `_on_fallback_note(text)`;
  - the module constant `_DETACH_AFTER_MS = 2000`.

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_chat_widget_fallback.py`:

```python
"""Widget side of #104: one start path, Stop that frees the input."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.core.conversation import Conversation  # noqa: E402
from freecad_ai.ui import chat_widget as cw  # noqa: E402

W = cw.ChatDockWidget


def _running_worker():
    worker = MagicMock()
    worker.isRunning.return_value = True
    worker._full_response = "partial"
    return worker


def _detach_fake(worker):
    fake = SimpleNamespace(
        _worker=worker, _detached_workers=[],
        _store_tool_results=MagicMock(), conversation=MagicMock(),
        chat_display=MagicMock(), _set_loading=MagicMock(),
        _append_html=MagicMock(), _WORKER_SLOTS=W._WORKER_SLOTS)
    fake._release_detached = lambda w: W._release_detached(fake, w)
    return fake


def test_stop_interrupts_and_schedules_a_detach():
    worker = _running_worker()
    fake = SimpleNamespace(_worker=worker, _schedule_detach=MagicMock())
    W._send_message(fake)
    worker.requestInterruption.assert_called_once()
    fake._schedule_detach.assert_called_once_with(worker)


def test_a_stuck_worker_is_detached():
    worker = _running_worker()
    fake = _detach_fake(worker)
    W._detach_if_stuck(fake, worker)
    worker.detach.assert_called_once()
    worker.token_received.disconnect.assert_called()
    worker.fallback_note.disconnect.assert_called()
    fake._store_tool_results.assert_called_once_with("partial")
    fake.conversation.save.assert_called_once()
    assert fake._worker is None
    fake._set_loading.assert_called_once_with(False)
    assert fake._detached_workers == [worker]
    assert "⏹ Stopped" in fake._append_html.call_args[0][0]


def test_a_worker_that_finished_is_left_alone():
    worker = _running_worker()
    worker.isRunning.return_value = False
    fake = _detach_fake(worker)
    W._detach_if_stuck(fake, worker)
    worker.detach.assert_not_called()
    assert fake._worker is worker


def test_a_newer_worker_is_left_alone():
    old, new = _running_worker(), _running_worker()
    fake = _detach_fake(new)
    W._detach_if_stuck(fake, old)
    old.detach.assert_not_called()
    assert fake._worker is new


def test_a_detached_worker_is_released_when_it_ends():
    worker = _running_worker()
    fake = _detach_fake(worker)
    W._detach_if_stuck(fake, worker)
    finished_slot = worker.finished.connect.call_args[0][0]
    finished_slot()
    assert fake._detached_workers == []
    worker.deleteLater.assert_called_once()


def test_the_fallback_note_is_escaped():
    fake = SimpleNamespace(_append_html=MagicMock())
    W._on_fallback_note(fake, "⚠ a<b couldn't be reached — answered by c")
    html = fake._append_html.call_args[0][0]
    assert "a&lt;b" in html
    assert "color: #888" in html


def test_start_worker_connects_every_slot(monkeypatch):
    made = {}

    class _Fake:
        def __init__(self, conversation, system_prompt, **kw):
            made.update(kw, conversation=conversation, system=system_prompt)
            self.connected = []
            for signal, _ in W._WORKER_SLOTS:
                setattr(self, signal, SimpleNamespace(
                    connect=lambda slot, s=signal: self.connected.append(s)))

        def start(self):
            made["started"] = True

    monkeypatch.setattr(cw, "_LLMWorker", _Fake)
    fake = SimpleNamespace(_set_loading=MagicMock(), _append_html=MagicMock())
    for _, slot in W._WORKER_SLOTS:
        setattr(fake, slot, MagicMock())
    conv = Conversation()
    W._start_worker(fake, conv, "S", start_index=4)
    assert made["conversation"] is conv
    assert made["start_index"] == 4
    assert made["parent"] is fake
    assert made["started"] is True
    assert fake._worker.connected == [s for s, _ in W._WORKER_SLOTS]
    assert (fake._in_thinking, fake._tool_results_stored,
            fake._summary_rendered) == (False, False, False)


def test_the_retry_path_hands_over_the_conversation(monkeypatch, tmp_config_dir):
    """Bug 2: the retry rendered OpenAI style from cfg.provider.model."""
    monkeypatch.setattr("freecad_ai.core.system_prompt.build_system_prompt",
                        lambda **k: "SYS")
    from freecad_ai.config import get_config
    get_config().optimize_prompt_caching = False
    conv = Conversation(messages=[{"role": "user", "content": "go"}])
    started = {}
    fake = SimpleNamespace(
        _retry_count=0, conversation=conv, _capture_mode_override="off",
        _append_html=MagicMock(),
        mode_combo=SimpleNamespace(currentIndex=lambda: 1),
        _start_worker=lambda c, s, **kw: started.update(kw, conv=c, system=s))
    W._handle_execution_error(fake, SimpleNamespace(stderr="boom"))
    assert started["conv"] is conv
    assert started["system"] == "SYS"
    assert started["start_index"] == conv.window_start()
    assert "api_style" not in started


def test_needs_vision_comes_from_the_rendered_history():
    conv = Conversation()
    conv.add_user_message("look", images=[{"type": "image", "data": "AA",
                                           "mime_type": "image/png"}])
    conv.add_assistant_message("seen")
    conv.add_user_message("more")
    assert W._needs_vision(SimpleNamespace(supports_vision=True), conv, 0) is True
    assert W._needs_vision(SimpleNamespace(supports_vision=True), conv, 2) is False
    assert W._needs_vision(SimpleNamespace(supports_vision=False), conv, 0) is False


def test_the_session_log_carries_the_attempts(monkeypatch, tmp_config_dir):
    import json
    import os
    from freecad_ai import config as config_mod
    worker = SimpleNamespace(
        _tool_results=[{"assistant_text": "", "tool_calls": [], "results": []}],
        fallback_attempts=[{"round": 0, "profile": "a", "outcome": "failed",
                            "error": "HTTP 503"}])
    fake = SimpleNamespace(_worker=worker, conversation=Conversation(),
                           _append_html=MagicMock())
    # tmp_config_dir already points config_mod.LOGS_DIR at a temp dir;
    # chat_widget imported the real path by value, so follow it.
    monkeypatch.setattr(cw, "LOGS_DIR", config_mod.LOGS_DIR)
    W._save_session_log(fake)
    [name] = os.listdir(config_mod.LOGS_DIR)
    with open(os.path.join(config_mod.LOGS_DIR, name)) as f:
        assert json.load(f)["fallback_attempts"][0]["error"] == "HTTP 503"
```

- [ ] **Step 2: Run to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_chat_widget_fallback.py -q`
Expected: FAIL with `AttributeError: type object 'ChatDockWidget' has no attribute '_WORKER_SLOTS'` (and `_detach_if_stuck`, `_on_fallback_note`, `_needs_vision`, `_start_worker`).

- [ ] **Step 3: Add the start path.** Add this module constant near the other module constants at the top of `chat_widget.py`:

```python
# Stop, then this long with the worker still inside a request that
# ignores interruption: the widget stops waiting for it (#104).
_DETACH_AFTER_MS = 2000
```

Add `from html import escape as _html_escape` to the stdlib imports at the top (the file has local variables named `html`).

In `ChatDockWidget`, as class attributes right after the class docstring:

```python
    # Every worker signal and the slot it drives. _start_worker connects
    # these; a detach disconnects the same list (#104).
    _WORKER_SLOTS = (
        ("token_received", "_on_token"),
        ("thinking_received", "_on_thinking"),
        ("response_finished", "_on_response_finished"),
        ("error_occurred", "_on_error"),
        ("tool_call_started", "_on_tool_call_started"),
        ("tool_call_finished", "_on_tool_call_finished"),
        ("tool_exec_requested", "_execute_tool_call"),
        ("vision_note", "_on_vision_note"),
        ("fallback_note", "_on_fallback_note"),
    )
```

In `__init__`, right after `self._worker = None`, add `self._detached_workers = []  # stopped but still running (#104)`.

Add these methods right before `_save_session_log`:

```python
    @staticmethod
    def _needs_vision(cfg, conversation, start_index) -> bool:
        """Raw images go out only when the chat profile has vision; then
        a fallback without vision must be skipped (#104)."""
        return bool(cfg.supports_vision
                    and conversation.has_images(start_index))

    def _start_worker(self, conversation, system_prompt, **worker_kwargs):
        """Open the AI bubble and start a worker on ``conversation``."""
        self._set_loading(True)
        self._streaming_html = ""
        self._append_html(
            '<div style="margin: 8px 0; padding: 8px 12px; '
            'background-color: #f5f5f5; border-radius: 6px;">'
            '<div style="font-weight: bold; color: #2e7d32; margin-bottom: 4px;">AI</div>'
            '<div style="white-space: pre-wrap;">'
        )
        self._in_thinking = False
        self._tool_results_stored = False
        self._summary_rendered = False
        self._worker = _LLMWorker(conversation, system_prompt, parent=self,
                                  **worker_kwargs)
        for signal, slot in self._WORKER_SLOTS:
            getattr(self._worker, signal).connect(getattr(self, slot))
        self._worker.start()
```

- [ ] **Step 4: Use it from `_continue_send`.**
  - Move `filter_names = None` up next to `use_tools = ...` and delete it from inside `if use_tools:`.
  - Delete `tools_schema = None`, `api_style = "openai"`, `from ..llm.providers import get_api_style`, `api_style = get_api_style(cfg.provider.name)`, and the `if api_style == "anthropic": tools_schema = ... else: ...` block.
  - Delete `conversation_ref = None` and `conversation_ref = self.conversation`.
  - Delete `from ..llm.client import should_strip_thinking` and the `strip = should_strip_thinking(...)` statement.
  - In the #47 comment, replace `(see _LLMWorker.run)` with `(see _LLMWorker._request)`.
  - In the `strip_images` comment, replace `the worker rebuilds messages with descriptions` with `the worker renders descriptions per request`.
  - Replace everything from `messages = self.conversation.get_messages_for_api(` to the end of the method with:

```python
        # The window is fixed for the whole turn so later tool rounds don't
        # drop older messages from under the prompt cache (#104, #47).
        start_index = self.conversation.window_start()
        self._start_worker(
            self.conversation, system_prompt,
            registry=self._tool_registry, filter_names=filter_names,
            describe_fn=describe_fn, strip_images=strip_images,
            start_index=start_index,
            needs_vision=self._needs_vision(cfg, self.conversation,
                                            start_index))
```

- [ ] **Step 5: Use it from `_handle_execution_error`.** Delete `from ..llm.client import should_strip_thinking` and the `strip = ...` statement. Replace everything from the `# This retry attached a viewport snapshot above` comment to the end of the method with:

```python
        # This retry attached a viewport snapshot above; drop history images
        # for non-vision models so they aren't sent raw (issue #30). The
        # worker renders the turn for whichever profile answers, like the
        # main send path -- this used to render OpenAI style from
        # cfg.provider.model whatever the vendor (#104).
        start_index = self.conversation.window_start()
        self._start_worker(
            self.conversation, system_prompt,
            strip_images=not cfg.supports_vision, start_index=start_index,
            needs_vision=self._needs_vision(cfg, self.conversation,
                                            start_index))
```

- [ ] **Step 6: Stop that detaches.** In `_send_message`, replace the Stop branch body:

```python
            self._worker.requestInterruption()
            self._schedule_detach(self._worker)
            return
```

Add right after `_send_message`:

```python
    def _schedule_detach(self, worker):
        QtCore.QTimer.singleShot(_DETACH_AFTER_MS,
                                 lambda: self._detach_if_stuck(worker))

    def _detach_if_stuck(self, worker):
        """Stop was pressed and ``worker`` is still inside a request that
        ignores interruption -- a connect or read that only returns at its
        timeout, up to 300 s. Let it finish unheard and give the user the
        input back now (#104)."""
        if worker is not self._worker or not worker.isRunning():
            return
        for signal, _ in self._WORKER_SLOTS:
            try:
                getattr(worker, signal).disconnect()
            except (RuntimeError, TypeError):
                pass       # nothing connected
        worker.detach()
        # A running QThread must never be destroyed: hold it until it ends.
        self._detached_workers.append(worker)
        worker.finished.connect(lambda: self._release_detached(worker))
        self._store_tool_results(worker._full_response)
        self.conversation.save()
        self._worker = None
        self._set_loading(False)
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml("</div></div>")
        self._append_html(render_message(
            "system", translate("ChatDockWidget", "⏹ Stopped")))

    def _release_detached(self, worker):
        if worker in self._detached_workers:
            self._detached_workers.remove(worker)
        worker.deleteLater()
```

- [ ] **Step 7: The chat note and the session log.** Add right after `_on_vision_note`:

```python
    def _on_fallback_note(self, text: str):
        """Show which profile answered after a failover (#104). Display
        only: never stored, so it never reaches a model."""
        self._append_html(
            '<div style="color: #888; font-size: 9pt; margin: 2px 12px;">'
            f'{_html_escape(text)}</div>'
        )
```

In `_save_session_log`, right after the `if self._worker and hasattr(self._worker, "_tool_results") and self._worker._tool_results:` block that sets `log_data["tool_trace"]`, add:

```python
        if self._worker and getattr(self._worker, "fallback_attempts", None):
            log_data["fallback_attempts"] = self._worker.fallback_attempts
```

- [ ] **Step 8: Run the widget tests**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_chat_widget_fallback.py -q`
Expected: PASS.

- [ ] **Step 9: Full suite, then commit**

Run: the full test command. Expected: all pass. Remove any `xfail(reason="Task 7")` marker that Task 6 added; with `strict=True`, such a test now fails until the marker is removed.

```bash
git add freecad_ai/ui/chat_widget.py tests/unit/test_chat_widget_fallback.py
git commit -m "feat(chat): one worker start path, detach on a stuck Stop, fallback note (#104)" -m "The retry path now gets all nine signal connections and renders for the answering profile instead of OpenAI style from cfg.provider.model." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: The fallback list in the shared ProviderSection

**Files:**
- Modify: `freecad_ai/ui/provider_section.py`:
  - the Qt aliases (~line 20–33);
  - `__init__` (~88);
  - the utility group build (~223–239; add the group after it);
  - `load` (~243), `apply_to` (~263), `_state` (~346);
  - `_rename_profile` (~356), `_delete_profile` (~386);
  - `_refresh_profile_combo` (~403).
- Test: `tests/unit/test_provider_section.py`, `tests/unit/test_provider_page.py`, `tests/unit/test_prefs_page.py`
- Modify fakes: `tests/unit/test_profile_selector.py` (lines ~57, ~407, ~711)

**Interfaces:**
- Consumes: `AppConfig.fallback_profiles` (Task 3).
- Produces: `ProviderSection.fallback_group`, `fallback_list` (`QListWidget`), `fallback_picker` (`QComboBox`), `fallback_add_btn` / `fallback_remove_btn` / `fallback_up_btn` / `fallback_down_btn`, `_fallback_profiles: list`, `_refresh_fallback_widgets()`.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_provider_section.py`:

```python
class TestFallbackList:
    """#104: the ordered fallback list, edited in the shared section."""

    def _loaded(self, section, fallback=()):
        c = _cfg()
        c.profiles["spare"] = ProviderConfig(name="openai", model="m3")
        c.fallback_profiles = list(fallback)
        section.load(c)
        return c

    def _pick(self, section, label):
        section.fallback_picker.setCurrentIndex(
            section.fallback_picker.findData(label))

    def _listed(self, section):
        return [section.fallback_list.item(i).text()
                for i in range(section.fallback_list.count())]

    def test_the_group_title_and_tooltip(self, section):
        assert section.fallback_group.title() == \
            "Fallback when the chat model can't be reached"
        assert section.fallback_group.toolTip() == (
            "Tried once, in this order, after the chat profile fails to "
            "answer. A paid profile in this list is used without asking.")

    def test_load_shows_the_list(self, section):
        self._loaded(section, ["spare", "local"])
        assert self._listed(section) == ["spare", "local"]

    def test_the_picker_offers_only_unlisted_profiles(self, section):
        self._loaded(section, ["local"])
        offered = [section.fallback_picker.itemData(i)
                   for i in range(section.fallback_picker.count())]
        assert "local" not in offered
        assert "spare" in offered

    def test_add_appends_and_dirties(self, section):
        self._loaded(section)
        self._pick(section, "spare")
        section.fallback_add_btn.click()
        assert self._listed(section) == ["spare"]
        assert section.is_dirty() is True

    def test_remove_drops_the_selected_row(self, section):
        self._loaded(section, ["spare", "local"])
        section.fallback_list.setCurrentRow(0)
        section.fallback_remove_btn.click()
        assert self._listed(section) == ["local"]

    def test_up_and_down_reorder(self, section):
        self._loaded(section, ["spare", "local"])
        section.fallback_list.setCurrentRow(1)
        section.fallback_up_btn.click()
        assert self._listed(section) == ["local", "spare"]
        assert section.fallback_list.currentRow() == 0
        section.fallback_down_btn.click()
        assert self._listed(section) == ["spare", "local"]

    def test_up_on_the_first_row_does_nothing(self, section):
        self._loaded(section, ["spare", "local"])
        section.fallback_list.setCurrentRow(0)
        section.fallback_up_btn.click()
        assert self._listed(section) == ["spare", "local"]

    def test_apply_writes_the_list(self, section):
        self._loaded(section, ["local"])
        self._pick(section, "spare")
        section.fallback_add_btn.click()
        target = _cfg()
        section.apply_to(target)
        assert target.fallback_profiles == ["local", "spare"]

    def test_an_untouched_load_is_clean(self, section):
        self._loaded(section, ["local"])
        assert section.is_dirty() is False

    def test_rename_carries_the_fallback_entry(self, section):
        self._loaded(section, ["local"])
        section._rename_profile("local", "box")
        section._refresh_profile_combo()
        assert self._listed(section) == ["box"]

    def test_delete_removes_the_fallback_entry(self, section):
        self._loaded(section, ["local", "spare"])
        section._delete_profile("local")
        section._refresh_profile_combo()
        assert self._listed(section) == ["spare"]
```

Append to `tests/unit/test_provider_page.py`:

```python
def test_the_dialog_page_saves_the_fallback_list(page):
    page.load(_cfg())
    section = page.section
    section.fallback_picker.setCurrentIndex(
        section.fallback_picker.findData("local"))
    section.fallback_add_btn.click()
    target = _cfg()
    page.apply_to(target)
    assert target.fallback_profiles == ["local"]
```

Append to `tests/unit/test_prefs_page.py`:

```python
def test_preferences_saves_the_fallback_list(pages):
    section = pages["FreeCADAIProviderPrefs"].page.section
    section.fallback_picker.setCurrentIndex(
        section.fallback_picker.findData("local"))
    section.fallback_add_btn.click()
    _ok(pages)
    assert config_mod.load_config().fallback_profiles == ["local"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_provider_section.py tests/unit/test_provider_page.py tests/unit/test_prefs_page.py -q -k "Fallback or fallback"`
Expected: FAIL with `AttributeError: 'ProviderSection' object has no attribute 'fallback_group'`.

- [ ] **Step 3: Implement the widgets.** Add `QListWidget = QtWidgets.QListWidget` to the Qt aliases. In `__init__`, right after `self._utility_profiles = {}`, add `self._fallback_profiles = []`. Right after `layout.addWidget(self.utility_group)`:

```python
        # ── Fallback ────────────────────────────────────────────────
        # One global ordered list, walked once per turn after the chat
        # profile fails to answer (#104).
        self.fallback_group = QGroupBox(translate(
            "SettingsDialog", "Fallback when the chat model can't be reached"))
        self.fallback_group.setToolTip(translate(
            "SettingsDialog",
            "Tried once, in this order, after the chat profile fails to "
            "answer. A paid profile in this list is used without asking."))
        fb_layout = QVBoxLayout()
        self.fallback_list = QListWidget()
        fb_layout.addWidget(self.fallback_list)
        fb_row = QHBoxLayout()
        self.fallback_picker = QComboBox()
        fb_row.addWidget(self.fallback_picker, 1)
        self.fallback_add_btn = QPushButton(translate("SettingsDialog", "Add"))
        self.fallback_remove_btn = QPushButton(
            translate("SettingsDialog", "Remove"))
        self.fallback_up_btn = QPushButton(translate("SettingsDialog", "Up"))
        self.fallback_down_btn = QPushButton(translate("SettingsDialog", "Down"))
        for btn in (self.fallback_add_btn, self.fallback_remove_btn,
                    self.fallback_up_btn, self.fallback_down_btn):
            fb_row.addWidget(btn)
        fb_layout.addLayout(fb_row)
        self.fallback_group.setLayout(fb_layout)
        layout.addWidget(self.fallback_group)
        self.fallback_add_btn.clicked.connect(self._on_fallback_add)
        self.fallback_remove_btn.clicked.connect(self._on_fallback_remove)
        self.fallback_up_btn.clicked.connect(lambda: self._move_fallback(-1))
        self.fallback_down_btn.clicked.connect(lambda: self._move_fallback(1))
```

- [ ] **Step 4: Implement the state.**
  - In `load`, right after `self._utility_profiles = dict(cfg.utility_profiles)`, add `self._fallback_profiles = list(cfg.fallback_profiles)`.
  - In `apply_to`, right after the `cfg.utility_profiles = ...` statement, add `cfg.fallback_profiles = list(self._fallback_profiles)`.
  - In `_state`, add `list(self._fallback_profiles),` as a fourth tuple element.
  - At the end of `_rename_profile`, add:

```python
        # In place: callers and tests may hold this list.
        self._fallback_profiles[:] = [
            new if label == old else label for label in self._fallback_profiles]
```

  - At the end of `_delete_profile`, add:

```python
        self._fallback_profiles[:] = [
            entry for entry in self._fallback_profiles if entry != label]
```

  - At the end of `_refresh_profile_combo`, after `self._refresh_utility_combos()`, add `self._refresh_fallback_widgets()`.
  - Add these methods right after `_refresh_utility_combos`:

```python
    def _refresh_fallback_widgets(self) -> None:
        """Repopulate the fallback list and its picker from the working copy."""
        row = self.fallback_list.currentRow()
        self.fallback_list.clear()
        for label in self._fallback_profiles:
            self.fallback_list.addItem(label)
        if 0 <= row < self.fallback_list.count():
            self.fallback_list.setCurrentRow(row)
        self.fallback_picker.clear()
        for label in self._profiles:
            if label not in self._fallback_profiles:
                self.fallback_picker.addItem(label, label)

    def _on_fallback_add(self) -> None:
        label = self.fallback_picker.currentData()
        if label and label not in self._fallback_profiles:
            self._fallback_profiles.append(label)
            self._refresh_fallback_widgets()
            self.fallback_list.setCurrentRow(len(self._fallback_profiles) - 1)

    def _on_fallback_remove(self) -> None:
        row = self.fallback_list.currentRow()
        if 0 <= row < len(self._fallback_profiles):
            del self._fallback_profiles[row]
            self._refresh_fallback_widgets()

    def _move_fallback(self, step: int) -> None:
        row = self.fallback_list.currentRow()
        target = row + step
        entries = self._fallback_profiles
        if 0 <= row < len(entries) and 0 <= target < len(entries):
            entries[row], entries[target] = entries[target], entries[row]
            self._refresh_fallback_widgets()
            self.fallback_list.setCurrentRow(target)
```

- [ ] **Step 5: Teach the fakes the new field.** In `tests/unit/test_profile_selector.py`:
  - `_FakeSelf.__init__` (~line 57): add `self._fallback_profiles = cfg.fallback_profiles` right after the `_utility_profiles` line.
  - The `SimpleNamespace` at ~line 407: add `_fallback_profiles=list(cfg.fallback_profiles),` right after `_utility_profiles=...`.
  - `_selector_fake` (~line 711): add `_fallback_profiles=cfg.fallback_profiles,` to the namespace, and after the fake is built add `fake._refresh_fallback_widgets = lambda: None` next to the other stubbed refreshers.

- [ ] **Step 6: Run the section, page and selector tests**

Run: `env PYTHONPATH= .venv/bin/pytest tests/unit/test_provider_section.py tests/unit/test_provider_page.py tests/unit/test_prefs_page.py tests/unit/test_profile_selector.py tests/unit/test_config_changed_notification.py -q`
Expected: PASS.

- [ ] **Step 7: Full suite, then commit**

Run: the full test command. Expected: all pass.

```bash
git add freecad_ai/ui/provider_section.py tests/unit/test_provider_section.py tests/unit/test_provider_page.py tests/unit/test_prefs_page.py tests/unit/test_profile_selector.py
git commit -m "feat(settings): fallback profile list in the shared provider section (#104)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Docs and live probes

**Files:**
- Modify: `README.md` (the `## Features` list), `/home/alf/Projects/programming/misc/freecad-ai-wiki/Configuration.md` (after `### Utility models Section`)
- Probe scripts (not committed): `<scratchpad>/probe99/probe104_failover.py`, `<scratchpad>/probe99/probe104_stop.py`

**Interfaces:**
- Consumes: everything above. Produces: nothing code depends on.

- [ ] **Step 1: README.** In `## Features`, right after the `**Context compacting**` line, add:

```markdown
- **Fallback profiles** — when the chat model can't be reached (Ollama box off, vendor 503, revoked key), the turn is tried once on each profile you list, in order, and a note says which one answered
```

- [ ] **Step 2: Wiki.** In `Configuration.md`, add a section right after the `### Utility models Section` block:

```markdown
### Fallback profiles Section

**Fallback when the chat model can't be reached** is an ordered list of
profiles. When a request fails before its first token, the next profile
in the list is tried; the list is walked **once** per turn, starting at
the chat profile. Pick a profile and press **Add**; **Remove**, **Up**
and **Down** edit the order. An empty list means the feature is off.

- **What counts as a failure.** No connection, DNS failure, timeout,
  HTTP 5xx and HTTP 429 mean "couldn't be reached". HTTP 400/401/403/404
  mean "refused" — usually a wrong key or model name — and are shown as a
  warning before the next profile is tried. A failure after the reply has
  started ends the turn as before.
- **Forward only.** Once a profile has answered, the rest of the turn
  (every tool round) stays on it. A profile that failed is not retried in
  the same turn; the next turn starts at the chat profile again.
- **Capabilities.** A profile without tool calling is skipped in Act
  mode; a profile without vision is skipped when the turn sends images.
  The skip is logged.
- **Rate limits.** Only the last profile that can be tried waits and
  retries on HTTP 429; the others hand over at once.
- **Cost.** A paid profile in this list is used without asking. Putting
  it on the list is the consent.
- **Where to see it.** The Report view logs every attempt
  (`FreeCAD AI: local-qwen failed — connection refused; trying
  claude-work`), the chat shows a short note above the reply, and the
  session log records `fallback_attempts`.
- **Stop** works during the walk. If a request hangs in connect or read,
  the input comes back after 2 s and the stuck request finishes unheard.
- **Known limit.** Compaction still uses the chat profile's threshold. A
  fallback with a smaller context window answers "context too long" with
  a 400, which is treated as refused, and the next profile is tried.
```

Commit it in the wiki repo but **do not push** (wiki pushes wait for the release):

```bash
git -C /home/alf/Projects/programming/misc/freecad-ai-wiki add Configuration.md
git -C /home/alf/Projects/programming/misc/freecad-ai-wiki commit -m "docs: fallback profiles (#104)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3: Live probe 1: failover.** Reuse the isolated environment `<scratchpad>/probe99/run.sh` (it sets HOME, XDG_* and `FREECAD_AI_CONFIG_DIR` under the scratchpad and launches the AppImage with `QT_QPA_PLATFORM=xcb` under Xvfb). Check that `probe_stub.py` in the same folder serves a minimal OpenAI-style SSE reply; if it doesn't, make it answer `POST /v1/chat/completions` with two `data:` chunks (`"content":"stub ok"`, then `"finish_reason":"stop"`) and `data: [DONE]`. The probe script:
  - seeds the isolated config with the chat profile `dead` (`ollama`, `http://127.0.0.1:1/v1`) and the fallback profile `stub` (`custom`, `http://127.0.0.1:<stub port>/v1`), with `fallback_profiles: ["stub"]`, mode Plan;
  - sends "hi" through the dock's `_send_message`;
  - waits for `response_finished`;
  - writes to a result file: the reply text, whether the Report view (captured via the `freecad_ai` logger) contains `FreeCAD AI: dead failed — connection refused; trying stub`, and whether the chat HTML contains `answered by stub`.

Run: `bash <scratchpad>/probe99/run.sh <scratchpad>/probe99/probe104_failover.py`
Expected: the result file shows `stub ok`, and both markers are `True`.

- [ ] **Step 4: Live probe 2: Stop on a hanging stub.** The stub accepts the connection and never answers (`socket.listen`, `accept`, then sleep). The chat profile points at it, and the list is empty. The probe presses Stop 1 s after send (`_send_message` again), then polls `input_edit.isReadOnly()` and records the time until it is False.

Run: `bash <scratchpad>/probe99/run.sh <scratchpad>/probe99/probe104_stop.py`
Expected: input usable again within 2.5 s of Stop, the chat shows `⏹ Stopped`, and FreeCAD doesn't crash when the probe exits while the detached worker is still blocked.

- [ ] **Step 5: Full suite, then commit**

Run: the full test command. Expected: all pass.

```bash
git add README.md
git commit -m "docs: fallback profiles in the feature list (#104)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
