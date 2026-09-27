# Fallback models

**Issue:** #104. When the chat model can't be reached (the local Ollama box
is off, a cloud vendor returns 503, the API key was revoked), the turn ends
with `Error: ...`, and the user has to switch profiles by hand and ask again.
This change lets the user name other profiles to try, in order, when that
happens.
**Depends on:** #103 / PR #105 (`d8f013f`), which gave every profile its own
output cap, and #101 / PR #102 (`d017a8d`), which made every Settings section
shared between the Settings dialog and Edit → Preferences.

## Decisions (maintainer, 2026-09-27/28)

1. **The scenario is generic.** Local → cloud, cloud → local and
   cloud → cloud all have to work. None of them gets special treatment.
2. **One global ordered list**, `AppConfig.fallback_profiles`. Rejected:
   a fallback dropdown on each profile, where the chains are hard to see
   and can loop, and a single global fallback, which is too narrow.
3. **The list is walked once per turn, starting with the chat profile.**
   The maintainer first asked for repeated rounds with a round limit and a
   wait between rounds, then withdrew the request: one pass is enough.
   Stop still has to work at every point of the walk.
4. **Switch point B.** The switch happens before the first token of any
   request, including later rounds of the tool loop. Once a profile has
   answered, the rest of the turn stays with it. Deferred to a follow-up
   issue: (c), recovering from a failure mid-stream. It is feasible:
   remove that round's partial text from the chat bubble and from
   `_full_response`, remove the indicators for tool calls that never ran,
   add a one-line note, and resend. No FreeCAD undo is needed, because
   tools only run after a stream completes.
5. **A profile that lacks a capability the turn needs is skipped**, and
   the reason is logged. Rejected: re-preparing the turn for that profile
   (describing its images, dropping its tools). That means a second
   preparation path for a rare case.
6. **Chat only.** Utility jobs (compaction, skill evaluation, tool
   optimization, rerank) keep their current behaviour.
7. **Each request is rendered for the profile about to be called, just
   before the call.** The vendor format (Anthropic or OpenAI) belongs to
   the client. The turn itself doesn't have one.

## What exists today

- `ChatDockWidget._send_message` (`ui/chat_widget.py`) reads `api_style`
  from the active profile. It uses that style to render the tool schema
  (`to_anthropic_schema` / `to_openai_schema`) and the history
  (`conversation.get_messages_for_api(api_style=..., strip_images=...,
  strip_thinking=...)`). It then passes both, and `api_style`, into
  `_LLMWorker`. If the chat profile has no vision, `describe_fn` wraps the
  `describe_image` fallback tool. `strip_images` is set when there is
  neither vision nor a fallback. With `optimize_prompt_caching`, the
  document context is recorded on the conversation, so every re-render
  keeps it.
- `_LLMWorker.run` calls `create_client_from_config()` once per turn and
  takes `_response_max_tokens`, `_strip_thinking`, `_optimize_caching` and
  `_preserve_reasoning` from that client. With `describe_fn` set it
  re-renders `self.messages`. Then it runs `_simple_stream` (no tools) or
  `_tool_loop`. Every exception becomes `error_occurred.emit(str(e))`.
- `_tool_loop` keeps `messages = list(self.messages)` and appends each round
  in the vendor format: Anthropic `content_blocks` with `tool_use`, then
  `tool_result` blocks sent as `role: user`; OpenAI `tool_calls` with
  `json.dumps(arguments)`, `reasoning_content`, then `role: "tool"`
  results. It also records the round in a neutral form in
  `self._tool_results`. `_store_tool_results` writes that record into the
  real conversation at the end of the turn. Tools run only after a stream
  has finished.
- `llm/client.py`: `_http_post` / `_http_stream` retry a 429 up to
  `_MAX_RETRIES = 5` times and honour `Retry-After`. Any other `HTTPError`
  raises `LLMError(f"HTTP {code}: {reason}\n{body}")`, a `URLError` raises
  `LLMError("Connection error: ...")`, and anything else raises
  `LLMError("Request failed: ...")`. `LLMError` is a bare `Exception`
  and carries no status code.
- `create_client(cfg=None, utility=None, ...)` resolves a profile through
  `resolve_profile(cfg, utility)`. The chat path can't ask for a
  particular profile by label.
- The Stop button calls `self._worker.requestInterruption()`. A worker
  blocked in a connect or read that never returns stays blocked until the
  timeout: 300 s for Ollama, 120 s otherwise.
- `Conversation.get_messages_for_api(max_chars=100000, ...)` truncates by
  walking backwards from the newest message. The truncation window is
  therefore recomputed on every call.

## Configuration and UI

`AppConfig.fallback_profiles: list[str] = []`. An empty list means the
feature is off, and behaviour is exactly today's.

On load:

- labels that don't name a profile are dropped with a `logger.warning`;
- duplicates are dropped, keeping the first;
- the active profile may appear in the list. It stays there, and is
  skipped at run time. Otherwise, switching the active profile would
  rewrite the list.

The UI is a group in the shared `ProviderSection`, next to the utility
dropdowns, titled **"Fallback when the chat model can't be reached"**. It
contains an ordered list, a profile picker, and **Add / Remove / Up /
Down** buttons. The tooltip reads:

> Tried once, in this order, after the chat profile fails to answer. A
> paid profile in this list is used without asking.

Renaming a profile updates its entries, and deleting a profile removes
them, using the same paths as `_utility_profiles`. Because the section is
shared (#101), the Settings dialog and Edit → Preferences show and save
the list identically.

## Attempt logic

`fallback_chain(cfg) -> list[str]` returns `[cfg.active_profile] +
cfg.fallback_profiles`, with the active profile's repeats and unknown
labels removed.

`create_client` gains a keyword `profile: str | None = None` that builds
the client for that label. With `profile=None`, the result is exactly
today's.

**A forward-only cursor per turn:**

- Each request, including every round of the tool loop, starts at the
  profile that answered last. The first request starts at the chat
  profile.
- When a request fails, the cursor moves past that profile and the next
  one is tried. A profile that failed is not tried again in the same turn.
- The next turn starts again at the chat profile.
- **A request has succeeded once its first stream event has arrived.** An
  error after that ends the turn as it does today, and is logged (see (c)
  under Out of scope).

**Error classes.** `LLMError` gains `status: int | None` and
`kind: str`, set where the transport raises it:

| kind | raised for | effect |
|---|---|---|
| `unreachable` | `URLError` (refused, DNS), timeout, 5xx, 429 | try the next profile |
| `config` | 400, 401, 403, 404, other 4xx | skip this profile with a visible warning, try the next |

Any exception that isn't an `LLMError` ends the turn as it does today.

**429 retries.** Every candidate except the last raises at once
(`max_retries=0`), so a rate-limited profile doesn't hold up the chain
for minutes. The last candidate keeps today's backoff. With an empty
list, the chat profile is the last candidate, so nothing changes.

**Capability skip.** At turn start the widget records two flags:

- `needs_tools`: a tool registry was attached;
- `needs_vision`: raw images are sent, meaning the chat profile has
  vision and the rendered history holds images.

A candidate whose `supports_tools` / `supports_vision` lacks either is
skipped with a logged reason. The chat profile is never skipped: the turn
was prepared for it.

**All profiles fail.** The turn ends with one error that has a segment
per attempt:

```
No profile could answer — local-qwen: connection refused · claude-work: HTTP 503 · kimi: skipped (no vision)
```

With an empty list, the error text is today's, unchanged.

**Stop.**

- The worker checks `isInterruptionRequested()` before each attempt.
- The widget's Stop handler calls `requestInterruption()` and starts a
  **2 s** single-shot timer. If the worker is still running when it fires,
  the widget **detaches** it:
  - it disconnects the worker's signals;
  - it ends the turn as "⏹ Stopped" and re-enables input;
  - it keeps a reference to the worker until its `finished` signal, so a
    running `QThread` is never destroyed.
- A detached worker checks a `_detached` flag before it writes to the
  conversation or emits anything, so a late result is discarded.
- Stop during streaming behaves as today: the partial reply is kept and
  "⏹ Stopped by user" is shown.

## Rendering each request for its profile

The worker gets the prepared turn in a neutral form: the conversation, the
tool registry and filter names, `describe_fn`, `strip_images` and the
system prompt. It no longer gets `api_style` or pre-rendered messages.

- `Conversation.fork_for_turn()` returns a shallow working copy. Each round
  is recorded on the copy with `add_assistant_message(...)` and
  `add_tool_result(...)`, in the same neutral form that
  `_store_tool_results` already writes. The real conversation is written
  only at the end of the turn, by `_store_tool_results`, as today.
- Before every request the worker renders the history for the client it
  is about to call:

  ```python
  messages = work.get_messages_for_api(
      api_style=client.api_style,
      strip_thinking=should_strip_thinking(client.model,
                                           cfg.strip_thinking_history),
      strip_images=strip_images, describe_fn=describe_fn,
      start_index=start_index)
  ```

  It renders the tool schema the same way: `to_anthropic_schema` or
  `to_openai_schema`, chosen by `client.api_style`.
- A new `start_index` parameter pins the truncation window. It is
  computed once, at turn start, so later rounds don't drop older messages
  from under the prompt cache (#47).
- The describe wrapper memoises by image hash for the turn, so each image
  is described once, however many rounds or profiles render it.
- Each round's `reasoning_to_persist(...)` uses the settings of the
  profile that answered that round.
- Deleted: the vendor-specific `messages.append` branches in `_tool_loop`
  (about 45 lines), and the `api_style` argument to `_LLMWorker`.

**Golden test.** For a single-profile tool turn with two rounds, the
request bodies must be byte-identical to today's, in both Anthropic and
OpenAI style. The test captures today's bodies before the refactor. This
guards the #47 prompt cache.

## Visibility

- **Report view.** Each event is a `logger.warning`:
  - `FreeCAD AI: local-qwen failed — connection refused; trying claude-work`
  - `FreeCAD AI: kimi skipped — no vision`
  - `FreeCAD AI: answered by claude-work (fallback 1 of 2)`
- **Chat note.** Before the reply, a note that is only displayed:
  "⚠ local-qwen couldn't be reached (connection refused) — answered by
  claude-work". It is **not** stored as a `[System]` message, so it never
  reaches a model. A second switch in the same turn adds a second note.
- **Session log.** A `fallback_attempts` list, one entry per attempt:
  `round`, `profile`, `outcome` (`answered` / `failed` / `skipped`),
  `error`.
- The output cap follows the answering profile, because `create_client`
  resolves it per profile (#103). `_response_max_tokens` is updated from
  the answering client, so the truncation warning names the right cap.

**Known limit.** The compaction threshold stays the chat profile's. If a
fallback has a smaller window, its "context too long" reply is a 400.
That counts as `config`, so the profile is skipped with a visible warning
and the chain continues.

## Testing

Unit tests (no FreeCAD):

- `fallback_chain`: order; removal of the active profile's repeats and of
  unknown labels; empty list.
- `LLMError` classification, with `status` kept, for: refused, DNS,
  timeout, 500, 503, 429, 400, 401, 403 and 404.
- On 429, only the last candidate retries; the others raise at once.
- A candidate is skipped for missing tools or vision; the chat profile is
  never skipped.
- The forward-only cursor across rounds: round 1 fails over to B; round 2
  starts at B; A is never retried.
- The summary text when every profile fails.
- **Empty list:** exactly one `create_client` call, no fallback logging,
  and today's error text.
- Golden request bodies, Anthropic and OpenAI, for a single-profile
  two-round tool turn.
- `start_index` stays pinned across rounds; each image is described once.
- A detached worker discards its result and never writes to the
  conversation.
- Config load: unknown labels and duplicates are dropped; the active
  profile is kept.
- The list widget: Add, Remove, Up and Down; rename and delete
  propagation; the Settings dialog and Preferences behave identically.

Live probe on FreeCAD 1.1.1, with isolated HOME / XDG /
`FREECAD_AI_CONFIG_DIR` (`scratchpad/probe99/run.sh`):

1. The chat profile points at a closed local port, and the fallback at a
   tiny local OpenAI-style SSE stub. Assert that the reply arrives, the
   Report view shows the `failed … trying` line and the chat note is
   shown.
2. The chat profile points at a stub that accepts the connection and never
   answers. Press Stop and assert the input is usable again within about
   2.5 s.

## Docs

- Wiki `Configuration.md`: a "Fallback profiles" section covering the walk
  order, the error classes, the capability skip, the cost warning and the
  known limit.
- README: one line in the feature list.
- CHANGELOG entry at release.

## Out of scope

- (c) Recovering from a failure mid-stream by rolling the round back. It
  becomes a follow-up issue.
- Fallback for utility jobs.
- More than one pass over the list.
- A separate cost opt-in. Putting a profile on the list is the consent.
- Per-profile thinking, which gets its own issue.
