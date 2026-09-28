# Per-profile thinking

**Issue:** #108. Thinking is one global setting (off / on / extended, on
the Behavior page), but profiles differ: a local Ollama model, a Claude
model and a reasoning model behind a gateway each want something else,
and every fallback candidate inherits the chat profile's value.
**Predecessors:** #103 (per-profile limits, the `context_window` profile
field this copies), #104 (fallback walker, which builds each candidate
with `create_client(profile=...)`), #107 (current-Claude request shape).

## Decisions (maintainer, 2026-09-28)

1. **Vendor values are sent verbatim.** No per-model tables, no
   discovery. "Ship sane defaults and let the user enter what they need;
   if a value does not work, they will notice." Both Anthropic and Ollama
   list the accepted values in their 400 message.
2. **The default changes nothing.** A profile's thinking is unset
   ("Use global") until the user picks something, so an upgrade sends the
   same bytes as today.
3. **Test Connection sends the profile's value**, so a bad level fails in
   Settings with the vendor's message rather than mid-chat.
4. **Docs over code**: a wiki chapter explains how to tune a model.

### One deviation from the issue text, for review

The issue says a profile's `off` sends `thinking: {type: disabled}`
(Anthropic) and `reasoning_effort: none` (OpenAI-style). This spec instead
gives **`off` one meaning everywhere: today's global "off" bytes**. The
reasons:

- A value's meaning should not depend on which page it was typed on. If
  profile `off` and global `off` sent different bytes, "Use global" with
  global off and a profile set to `off` would behave differently while
  looking identical.
- Verbatim is still available: typing `none` sends `reasoning_effort:
  none`. `off` is our word, not a vendor's, so it keeps our meaning.
- Anthropic `disabled` is rejected by Opus 5.5 (probed 2026-09-28), so
  the issue's mapping would make the obvious choice fail on a flagship.

If you prefer the issue's mapping, it is a two-line change in the
builders; everything else in this spec stands either way.

## What exists today

- `AppConfig.thinking: str = "off"`, values `off` / `on` / `extended`,
  edited in `behavior_page.py` (`thinking_combo`).
- `create_client(cfg, utility, *, thinking=None, ...)` passes
  `cfg.thinking if thinking is None else thinking` to `LLMClient`. Only
  the reranker overrides it (`thinking="off"`, `chat_widget.py`).
- `LLMClient._openai_body`: Ollama gets `/no_think` (off) or `/think`
  appended to the system prompt; `reasoning_effort` (on → medium,
  extended → high) is sent **only when no tools are sent**.
- `LLMClient._anthropic_body` / `_anthropic_headers`: #107's split —
  legacy Claude ids get `enabled` + `budget_tokens` + temperature 1 + the
  interleaved beta header; current ones get `adaptive` + `output_config.
  effort`; off sends no thinking key.
- `_TestConnectionThread` is handed `get_config().thinking`
  (`provider_page.py`, `_test_connection`).
- `ProviderConfig` (`config.py`) carries per-connection fields;
  `_profile_from_dict` drops unknown keys and validates `context_window`.

## Design

### 1. Storage

`ProviderConfig.thinking: str | None = None`.

- `None` — use `AppConfig.thinking` (the "Use global" row).
- Any other string — the profile's value, stored as typed after
  stripping surrounding whitespace. An empty string after stripping is
  stored as `None`.
- `_profile_from_dict`: a non-string value (hand-edited JSON) becomes
  `None` with a log warning, like `context_window`'s validation.
- No migration. Old configs have no key → `None` → today's behavior.

### 2. Resolution

In `create_client`:

```
thinking = call-site value  if thinking is not None
      else chosen.thinking  if chosen.thinking is not None
      else cfg.thinking
```

- The reranker keeps forcing `off`.
- Every fallback candidate is built with `create_client(profile=label)`,
  so it gets its own profile's value with no walker change.
- A utility profile (compaction, skill evaluation, tool optimization)
  now uses its own thinking value. That is the point of the feature; a
  utility profile left on "Use global" behaves as today.

### 3. Values and what each sends

`LLMClient.thinking` keeps its type (`str`). The builders classify the
value, case-sensitively, as typed:

| Value | Anthropic | OpenAI-style (incl. Ollama) |
|---|---|---|
| `off` | today's off bytes (no thinking key; temperature per #107) | today's off bytes (Ollama `/no_think`; no `reasoning_effort`) |
| `on`, `extended` | today's bytes (#107 split) | today's bytes (`reasoning_effort` medium/high only without tools; Ollama `/think`) |
| `default` | no thinking key, no beta header; temperature per #107's model rule | no `reasoning_effort`, no Ollama tag |
| all digits, e.g. `8000` | `thinking: {type: enabled, budget_tokens: N}`, temperature 1, beta header for legacy ids only | `reasoning_effort: "8000"` (verbatim; the vendor will say no) |
| any other word, e.g. `low`, `xhigh`, `none` | `thinking: {type: adaptive}` + `output_config: {effort: <word>}`, no global temperature, no beta header | `reasoning_effort: <word>`, **also when tools are sent**, no Ollama tag |

Notes:

- `on` / `extended` are the global vocabulary. They mean the same thing
  on a profile, so a user typing `on` gets today's behavior.
- For verbatim values the model id no longer chooses the shape: a level
  word on a legacy Claude model sends `adaptive` + `effort`, and Haiku
  4.5 answers with a 400. That is decision 1 — the user sees the message
  and picks a budget number instead.
- An explicit `temperature` row in the profile's params is still sent in
  every case (#107's rule).
- Verbatim values are sent with tools because a user who set a level on
  the profile meant it for Act mode too. The global `on` / `extended`
  keep today's "not with tools" rule, so upgrades change nothing.
- Ollama: verbatim values rely on `/v1`'s `reasoning_effort` (validated
  by Ollama 0.33.2, probed 2026-09-28), so no `/think` tag is added.

### 4. UI — provider section

A new row under "Compact above", in the shared `ProviderSection`, so both
the Settings dialog and Edit → Preferences get it (#101):

- **Thinking:** an editable `QComboBox`.
  - Row 0 **"Use global"** (item data `None`) — shows the global value in
    its tooltip.
  - Row 1 **"Model default"** (stores `default`).
  - Row 2 **`off`**.
  - Then suggestions for the profile's API style:
    - Anthropic: `low`, `medium`, `high`, `xhigh`, `max`
    - OpenAI-style: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`,
      `max`
  - Typed text is saved as typed (stripped). The suggestion list is
    rebuilt when the vendor changes; the current value is kept, even if it
    is not in the new list.
- Tooltip: "Sent to the vendor as-is. Anthropic: a level uses adaptive
  thinking, a number is a token budget. Use Test Connection to check a
  value." (translatable).
- Loading a profile shows its value; a stored value not in the list is
  shown as the edit text.
- The Behavior page's global combo is unchanged; its label becomes
  "Thinking (default for profiles):" so the relationship is visible.

### 5. Test Connection

`_test_connection` passes the section's **current, unsaved** thinking
value if set, else `get_config().thinking`, matching how it already
passes the unsaved model and params. The reranker test keeps `off`.

## Error handling

- A value the vendor rejects surfaces as its own 400 message, in Test
  Connection or in chat. With a fallback list, #104 moves on to the next
  profile and logs why — which is why the wiki says to take the profile
  out of the fallback list while tuning.
- No client-side validation beyond whitespace stripping. Decision 1.

## Testing

Unit tests, no FreeCAD:

- `ProviderConfig` round-trip with `thinking` set, unset, blank, and a
  non-string in JSON.
- `create_client` resolution: call site > profile > global; a fallback
  `profile=` label picks that profile's value; the reranker stays `off`.
- `_openai_body` for each row of the table, with and without tools, for
  `openai` and `ollama` providers. The global `on` with tools still sends
  no `reasoning_effort` (unchanged).
- `_anthropic_body` / headers for each row, on a legacy and a current id;
  a temperature row survives a level word.
- Golden request bodies (#104) unchanged — the fixture profiles have no
  thinking value.
- `ProviderSection`: combo shows the stored value, "Use global" stores
  `None`, a typed unknown value round-trips, suggestions switch with the
  vendor and keep the current text.
- Test Connection thread receives the unsaved profile value.

## Docs

- Wiki chapter **"Setting up thinking for a model"** (local commit, pushed
  at release):
  1. Take the profile out of the fallback list while testing.
  2. Start with *Model default* and Test Connection.
  3. Try levels one at a time.
  4. Raise the profile's `max_tokens` row — reasoning counts against it,
     and a small cap gives empty answers (qwen3:8b at `low`/`high`,
     probed).
  5. Known quirks: gpt-oss reasons even at `none`; Haiku 4.5 wants a
     budget number, not a level; Sonnet 5 and Opus 5.5 want a level, not
     a number.
- CHANGELOG entry at release.

## Out of scope

- Per-model tables, capability discovery, validation of levels.
- Moving the global setting off the Behavior page.
- `strip_thinking_history` stays global.
