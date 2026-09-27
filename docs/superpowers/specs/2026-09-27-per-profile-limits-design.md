# Per-profile output cap and compaction threshold

**Issue:** #103. Two of the four *Limits* settings describe the model, yet
they are stored once for all profiles. Switching the active profile from a
cloud model with a 200k window to a local 8k model keeps both, so compaction
starts too late (or too early) and the output cap may exceed what the
smaller model accepts.
**Predecessor:** #101 / PR #102 (`d017a8d`), which gave both windows the
shared Provider and Behavior pages this change lands on.
**Successor:** #104 (fallback models) needs every profile to carry its own
limits.

## Decisions (maintainer, 2026-09-27)

1. **"Context Window" stays a compaction threshold** and is relabelled
   **"Compact above"** in the UI. The config key stays `context_window`,
   nothing is migrated, and existing values keep their meaning. Rejected:
   storing the model's real window and compacting at a percentage — it
   changes what every existing value means.
2. **The per-profile output cap is a `max_tokens` row in the profile's
   Model Parameters table**, the mechanism `temperature` already uses.
   Rejected: a dedicated spin box — a second per-profile mechanism beside
   the table.
3. **"Compact above" is a new profile field**, because it is client-side
   only and must never be sent to a vendor (a thin proxy may 400 on an
   unknown request key).
4. **No probe offer.** Test Connection and the Ollama `/api/show` probe do
   not propose a value; with threshold semantics any number derived from a
   model's context length would be a guess.
5. **Thinking stays global.** Per-profile thinking touches both request
   builders and gets its own issue.

## What exists today

- `create_client` (`llm/client.py`) builds every client — chat, compaction,
  skill evaluation, tool optimization, reranker — and passes
  `cfg.max_tokens` unless the call site overrides it (the reranker passes
  1024).
- The request builders send `model_params.get("temperature",
  self.temperature)`, so a table row already wins for temperature.
  `max_tokens` is on `_RESERVED`, so no table row can set it.
- `chat_widget.py` compacts when `needs_compaction(cfg.context_window)`.
- `cfg.max_tokens` is also read directly by the truncation warning
  (`render_truncation_warning(get_config().max_tokens)`) and by Test
  Connection (`provider_page.py`).
- `ProviderSection` owns the profile's own fields and commits them into
  its working copy in `_commit_profile_fields`; `ProviderPage` owns only
  the Model Parameters table, committed through `aboutToCommit`.

## Resolution

### Output cap

`create_client` resolves `max_tokens` once:

1. the call-site `max_tokens` argument, when given;
2. otherwise the resolved profile's `max_tokens` params row, when valid;
3. otherwise `cfg.max_tokens`.

The row is removed from the `model_params` dict handed to `LLMClient`, and
`max_tokens` **stays on `_RESERVED`** in both request builders. A table row
can therefore never override a value the call site set explicitly: the
reranker's 1024 beats a `max_tokens: 32000` row.

This applies to every utility. The compaction summarizer runs with the
compaction profile's cap, the skill evaluator with its own profile's cap.

**Known asymmetry, not fixed here:** temperature is resolved inside the
request builders, so for temperature a table row beats a call-site
override. No call site overrides temperature on a profile that states one,
so it is harmless today.

### Valid `max_tokens` values

A row is used when its value is an `int` greater than 0, or a `float` with
no fractional part greater than 0 (`8192.0` → 8192). A `bool` is not an
int here. Anything else (`"abc"`, `0`, `-5`, `True`, `8192.5`) is ignored,
the global value applies, and a warning names the profile and the value:

```
max_tokens row "abc" in profile "local" ignored — not a positive integer
```

The warning goes through the module `logger`, which reaches FreeCAD's
report view. A silent fallback to the global cap would leave the user
unable to tell why their row did nothing.

### Compaction threshold

New field `ProviderConfig.context_window: int | None = None`. `None`
means "use `cfg.context_window`". It is never sent to any API.

The threshold comes from the **active chat profile**
(`cfg.profiles[cfg.active_profile]`), never from the compaction profile:
the question is whether this conversation is too big for the model you are
talking to. Who writes the summary is a separate setting.

`_profile_from_dict` validates the field on load: an `int` (not `bool`)
greater than 0 is kept; anything else loads as `None` with a warning.
Small values such as 3000 are **kept** — the 4000 floor applies only to
values typed in the UI, so a hand edit survives (#101's rule).

### Other readers of `cfg.max_tokens`

- **Truncation warning:** takes the cap from the client that produced the
  truncated response (`LLMClient.max_tokens`), not from config. An edit
  made during the turn cannot make the warning name the wrong number.
- **Test Connection:** uses the `max_tokens` row in the table on screen
  when it holds a valid value, otherwise the saved `cfg.max_tokens` — the
  same way it already treats temperature.

## Serialization

`context_window: None` is written as `"context_window": null`, like the
capability fields. An untouched OK writes nothing at all because the page
is not dirty. An older version reading a newer config drops the unknown
key with its existing warning.

Provider presets' `default_params` gain **no** `max_tokens`, so every
existing profile sends exactly what it sends today.

## UI

### Provider page: "Compact above"

A row in the **LLM Provider group, directly under Vision**, built and
committed by `ProviderSection` like the other profile fields, so it
follows profile switches, renames and saves, and appears in both the
Settings dialog and Edit → Preferences.

- Widget: `QSpinBox`, range 0–1,000,000, single step 10000, with
  `setSpecialValueText("Use global")` so 0 displays as **Use global**.
- Show: `None` → 0; any stored integer is shown as is (3000 included).
- Commit: the spin box is written into the profile **only when its value
  differs from what was shown for that profile**. Then 0 → `None`,
  1–3999 → 4000, anything else as typed. An untouched hand-edited 3000 is
  never re-clamped.
- Tooltip: *"Compact older messages once the conversation is estimated
  above this many tokens. \"Use global\" takes the value from Behavior →
  Limits."*
- Rejected: showing "Use global (20000)". The global value lives on the
  Behavior page, and an unsaved edit there would make the number stale.

### Provider page: Model Parameters table

- Label: "Sampling parameters sent with each request (saved per model):"
  → **"Parameters sent with each request (saved per profile):"** (the
  table is per profile since #99).
- Tooltip: `max_tokens` joins the "Common:" list, with one line saying it
  overrides Max Output Tokens for this profile.
- `ProviderPage.apply_to` copies the table's temperature into
  `cfg.temperature`. `max_tokens` must **not** get the same treatment: a
  row changes its profile only, never `cfg.max_tokens`.

### Behavior page → Limits

Shared by the dialog and Preferences.

- "Context Window:" → **"Compact above:"**. Tooltip: *"Older messages are
  compacted once the conversation is estimated above this many tokens.
  Default for profiles that don't set their own on the Provider page."*
- "Max Output Tokens:" keeps its label. Tooltip drops "Context window is
  determined by the model/provider." and reads: *"Default output cap per
  response. A profile can set its own with a max_tokens row in its Model
  Parameters table."*

### Truncation warning

The text "Raise Max Output Tokens in Settings → Model Parameters, or ask
the model to continue." points at a field that moved to Behavior → Limits
in #101 and may not be the one that applied. New text: *"Response was cut
off at the output limit ({max_tokens} tokens). Raise Max Output Tokens in
Settings → Behavior, or this profile's max_tokens row, or ask the model to
continue."*

## Testing

Unit tests, test-first; fake-self fixtures bind the real helpers.

1. **Resolver order:** override beats row beats global; the reranker's
   1024 beats a `max_tokens: 32000` row; the row is absent from the
   client's `model_params`.
2. **Bad rows:** `"abc"`, `0`, `-5`, `True` and `8192.5` fall back to the
   global value and log the warning; `8192.0` becomes 8192.
3. **Threshold source:** with the compaction utility pointing at a
   *different* profile that has a *different* `context_window`, the
   threshold is the active profile's. Both values must differ so reading
   the wrong profile fails the test.
4. **`None` threshold:** falls back to `cfg.context_window`.
5. **Truncation warning:** shows the client's resolved cap, not
   `cfg.max_tokens`.
6. **Config round-trip:** an old config without the key loads `None`;
   `null` and `3000` survive a save; `"x"`, `0`, `-1` and `true` load as
   `None` with a warning.
7. **UI:** 0 maps to `None`; switching profiles shows each profile's own
   value; an untouched OK leaves `config.json` byte-identical; a
   hand-edited 3000 survives an unrelated edit on the same profile; a typed
   1500 is saved as 4000; a `max_tokens` row never writes
   `cfg.max_tokens`; the new labels and tooltips.
8. **Test Connection:** uses the on-screen row, else the saved global.
9. **Live probe:** FreeCAD 1.1.1 AppImage under Xvfb with isolated HOME /
   XDG / `FREECAD_AI_CONFIG_DIR` (the #101 scaffolding). Set "Compact
   above" on one profile in Edit → Preferences, OK, check the JSON; OK
   untouched, check the file hash is unchanged.

## Docs

- CHANGELOG `[Unreleased]`: per-profile output cap and compaction
  threshold; "Context Window" relabelled "Compact above"; truncation
  warning text.
- README Configuration bullets.
- Wiki Configuration page — committed locally, pushed at the next release
  together with #101's wiki commits.
- New translatable strings: `translations/update_translations.sh` runs
  before the release tag.

## Out of scope

- Per-profile thinking (its own issue).
- A probe that offers a threshold from the model's context length.
- Fixing the temperature override asymmetry.
- The two other Limits (tool-loop turns, execution timeout) — they guard
  the job and FreeCAD's executor, not the model.
- Fallback models (#104).
