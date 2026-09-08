# Provider implementation conventions

Each provider class extends `ResearchProvider` (`base.py`). The conventions
below were established by P26 (OpenAI), refined by P27 (Perplexity), and
extended by P28 (Gemini). Future provider work should match.

## Module-level constants

```python
_PROVIDER_NAME_<UPPER> = "<lower>"  # e.g. _PROVIDER_NAME_GEMINI = "gemini"
```

Used in every `ProviderError(_PROVIDER_NAME_<X>, ...)` raise. Never use
string literals.

## Shared helpers (in `_helpers.py`)

| Helper | Purpose |
|---|---|
| `_invalid_key_thotherror(provider, settings_url)` | Consistent 401-with-pricing-URL error. Pass the provider's auth-key management URL. |
| `render_sources_block(citations)` | Renders a `## Sources` markdown block from a list of `Citation` instances. Used by all providers' `get_result` paths. |
| `debug_print_empty_response(response, *, provider_label)` | Verbose-mode dump for an unexpectedly empty response. |
| `_extract_unsupported_param(message)` | Pulls a parameter name out of an "unsupported parameter X" SDK error message. |

## Required methods (from `ResearchProvider` base contract)

| Method | Notes |
|---|---|
| `submit(prompt, mode, system_prompt, verbose) -> str` | Returns a `job_id`. For immediate-only providers, response is cached for `get_result`. |
| `check_status(job_id) -> dict[str, Any]` | Returns `{"status": "<thoth-status>", ...}`. Status values: `in_progress` / `completed` / `cancelled` / `transient_error` / `permanent_error` / `not_found`. May include `failure_type` discriminator. |
| `get_result(job_id, verbose=False) -> str` | Renders the completed job as markdown. Empty string OK; never None. |
| `cancel(job_id) -> dict[str, Any]` | Returns `{"status": "cancelled", ...}` on success. For 5xx errors, may include `"best_effort": True`. |
| `reconnect(job_id) -> None` | Re-attaches to an existing job after process restart. Seeds `self.jobs[job_id]`. |
| `list_models() -> list[dict[str, Any]]` | Returns models the provider exposes. May raise. |
| `stream(prompt, mode, system_prompt, verbose) -> AsyncIterator[StreamEvent]` | Optional. Background-only providers may raise NotImplementedError; the runtime tolerates it. |

## Kind-mismatch guard

Call `self._validate_kind_for_model(mode)` at the TOP of `submit()` (and
inside `stream()` if implemented). Raises `ModeKindMismatchError` BEFORE
any HTTP attempt when `kind="immediate"` is dispatched against a
background-only model.

Hybrid-routing providers (currently only Gemini) put the check in BOTH
the public router AND each `_immediate_*` / `_deep_research_*` sub-method.
Idempotent, intentional — see Task 3 spec-review reasoning in
`docs/superpowers/plans/2026-05-11-p28-gemini-deep-research-background.md`.

## Internal-helper layout

P27 convention: provider-specific helpers (citation formatters, retry
predicates, parsers) live BELOW the provider class at module bottom.
See `perplexity.py` for the canonical example.

## Retry policy

Tenacity decorator on `submit()` (and `_*_submit_with_retry` internal
helpers) — 3 attempts, exponential 4-10s, filter by transient/timeout/
connection only (never 4xx other than 429):

```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception(_is_retryable_<provider>_exception),
    reraise=True,
)
```

Each provider needs a `_is_retryable_<provider>_exception(exc)` predicate.

## Error mapping

`_map_<provider>_error(exc, model, verbose=False) -> ThothError`. Covers
401/403/404/429/400/5xx + httpx network errors. Each branch returns a
ThothError subclass with an actionable message. Mirror the 12-branch
shape of `_map_openai_error` (in `openai.py`).

For providers with private SDK exception modules (Gemini), add a
try-import + duck-type fallback predicate at module top:

```python
try:
    from google.genai._gaos.lib.compat_errors import GeminiNextGenAPIClientError as _IExc
    _HAS_IEXC = True
except ImportError:
    _HAS_IEXC = False
    _IExc = None

def _is_interactions_error(exc):
    if _HAS_IEXC and isinstance(exc, _IExc):
        return True
    return type(exc).__module__.startswith("google.genai._") and hasattr(exc, "status_code")
```

## `self.jobs` schema

```python
self.jobs[job_id] = {
    "kind": "immediate" | "deep_research",   # discriminator
    "interaction_id": str,                   # DR only — same as job_id usually
    "mode": str,
    "model": str,
    "submitted_at": float,                   # time.time()
    # Immediate path only:
    "response": <cached SDK response>,
    "created_at": float,
    # DR path only:
    "last_status": str,
    "last_interaction": <last get response>,
    "cancel_requested": bool,
    "reconnected_at": float,
}
```

The `kind` discriminator is REQUIRED for hybrid-routing providers.
Use `self.jobs.get(job_id, {}).get("kind") == "deep_research"` for the
DR check (e.g. via a `_is_dr_job(job_id)` helper).

## Hybrid routing (P28 Gemini delta — not P26/P27)

When a provider supports BOTH immediate and background paths over
DIFFERENT SDK methods, public `submit / check_status / get_result /
cancel / reconnect` become 5-line routers that branch on
`is_background_model(self.model)` and delegate to `_immediate_*`
(P24 chat path) or `_deep_research_*` (P28 Interactions path).

This is NOT the P26/P27 pattern — those route inside a single SDK
method. The hybrid pattern is Gemini-specific because the Gemini DR
API surface is entirely different from chat completion. Future
cross-provider refactor (P29) should treat the divergence as
intentional, not consolidation drift.

---

# Per-provider specifics

## OpenAI (`openai.py`)

| Topic | Notes |
|---|---|
| **SDK** | Official `openai` Python SDK. Auth-key URL: `https://platform.openai.com/api-keys`. |
| **Background path** | `client.responses.create(model=..., input=..., background=True)` returns `resp_*` ID. Poll via `client.responses.retrieve(id)`. |
| **Models** | `o3-deep-research`, `o4-mini-deep-research` (background). Standard chat models (immediate). |
| **Error hierarchy** | Public via `openai.<ErrorClass>` (`AuthenticationError`, `RateLimitError`, etc.). No private-module quirk. |
| **Citations** | On response output annotations. See `openai.py` `_render_sources` and the annotation extraction loop. |
| **Mode-config support** | `max_tool_calls`, `code_interpreter`, `organization` all consumed. |
| **Polling cadence** | 30s default (P26). |

## Perplexity (`perplexity.py`)

| Topic | Notes |
|---|---|
| **SDK** | OpenAI Python SDK in compatibility mode against `https://api.perplexity.ai` for sync. httpx directly for async DR jobs (`/async/chat/completions`). |
| **DUAL-PATH ARCHITECTURE** | Sync path (`chat.completions`) AND async path (`async/chat/completions`). Two error mappers: `_map_perplexity_error` (sync) + `_map_perplexity_error_async`. |
| **Models** | `sonar`, `sonar-pro` (immediate); `sonar-deep-research` (background). |
| **`extra_body` namespace** | Perplexity-specific options live under `[modes.<X>.perplexity]` and forward to the SDK's `extra_body` (e.g. `web_search_options.search_context_size`). Modeled as `dict[str, Any]` in `config_schema.PerplexityConfig` — permissive to allow SDK evolution. |
| **Think-tag parser** | `_ThinkStreamParser` handles `<think>...</think>` tags split across stream chunks. Reuse if any other provider emits the same pattern. |
| **Citations** | `response.search_results` for async; inline for sync. See `_format_async_sources_block`. |
| **Polling cadence** | 30s. |

## Gemini (`gemini.py`)

Substantial extra detail because P28 shipped recently and the API has
non-obvious quirks.

### SDK and surfaces

| Topic | Notes |
|---|---|
| **SDK package** | `google-genai>=1.74.0`. Auth-key URL: `https://aistudio.google.com/app/apikey`. Tier: paid Tier 1+ required for Deep Research. |
| **Immediate path** | `client.aio.models.generate_content[_stream](model=..., contents=..., config=...)`. P24's territory. |
| **Background path (Deep Research)** | `client.aio.interactions.create(agent=..., input=..., background=True, store=True)`. P28's territory. ASYNC-ONLY surface (no sync equivalent). |
| **Hybrid class** | `GeminiProvider` routes between immediate and DR based on `is_background_model(self.model)`. See "Hybrid routing" section above. |

### Deep Research exception hierarchy (PRIVATE MODULE)

DR exceptions live in a **private** SDK module whose path moved in
google-genai 2.0: 1.x used `google.genai._interactions`, 2.x uses
`google.genai._gaos.lib.compat_errors`. `gemini.py` resolves it newest-first
and `_is_interactions_error()` keeps a duck-type fallback, so a further move
degrades classification instead of breaking the import:

```
GeminiNextGenAPIClientError  <-  Exception   (NOT inherited from google.genai.errors.APIError)
  BadRequestError      (HTTP 400)
  AuthenticationError  (HTTP 401)
  PermissionDeniedError (HTTP 403)
  NotFoundError        (HTTP 404)
  RateLimitError       (HTTP 429)   (NOT named TooManyRequestsError)
  InternalServerError  (HTTP 500)
  APIConnectionError   (no status_code)
  APITimeoutError      (no status_code)
```

**Important**:
- `exc.status_code` is the int. `exc.code` is ALWAYS `None` for these.
- Constructor is `(message: str, *, response: httpx.Response, body: object | None)`. NOT `(status_code=..., message=...)`.
- The hierarchy does NOT inherit from `google.genai.errors.APIError`. Catch
  via the `_is_interactions_error(exc)` predicate in `gemini.py`, NOT
  `isinstance(exc, genai_errors.APIError)`.

### Deep Research API quirks

| Topic | Notes |
|---|---|
| **Required `create()` params** | `agent=<id>`, `background=True`, `store=True`. All three required. |
| **`agent` parameter values (as of 2026-05-13)** | `deep-research-preview-04-2026` (fast, $1-3/task, P28 v1 default). `deep-research-max-preview-04-2026` ($3-7/task, deferred to successor project). `deep-research-pro-preview-12-2025` (legacy — STILL LISTED by live API; don't assume removed). |
| **API methods on `client.aio.interactions`** | `create`, `get`, `cancel`, `delete`, `with_raw_response`, `with_streaming_response`. **NO `continue` / `resume` / `refetch` method** — `incomplete` interactions are terminal. |
| **`previous_interaction_id`** | Conversation chaining only. NOT a refetch mechanism for incomplete runs. |
| **Status enum (6 values, not 4)** | `Literal['in_progress', 'requires_action', 'completed', 'failed', 'cancelled', 'incomplete']`. v1 maps `requires_action` and `incomplete` via the `failure_type` discriminator (`"requires_action"` / `"permanent"`). |
| **Citation extraction path** | `interaction.steps[N].content[0].annotations[]` where N is the first `model_output` step. **NOT** `interaction.outputs[-1].annotations[]` — `outputs` does not exist. Only the first model_output step carries annotations. |
| **`URLCitation` shape** | `{type: 'url_citation', start_index: int, end_index: int, title: None (always), url: <Vertex AI grounding redirect URL>}`. `title` is always None. `url` is a redirect, not the source URL. |
| **SDK-rendered Sources block** | The LAST step's `content[0].text` contains a markdown `[domain](redirect-url)` block. Parse it with `_parse_sdk_sources_block()` for domain titles since `URLCitation.title` is unusable. |
| **Layered citation rendering** | (1) parse SDK Sources block for `{redirect_url: domain_title}`. (2) Bounded-concurrency HEAD-follow each redirect URL to get source URL. (3) Title-derivation chain: `parsed_sources.get → urlparse(source).netloc → URL`. (4) Dedupe by final URL. See `_resolve_dr_redirects` in `gemini.py`. |
| **Polling cadence and timeout** | Schema-only in v1: `[providers.gemini].poll_interval = 10` and `.max_wait_minutes = 60`. Runtime still reads `[execution].poll_interval` (default 30s) and `.max_wait` (default 30min). Set `[execution].max_wait = 60` for DR users (upstream hard limit is 60 min). v1.1 will wire the per-provider override. |
| **Retention windows** | Paid tier: 55 days. Free tier: 1 day (but DR is paid-only). `interactions.get()` on expired ID → `NotFoundError(404)` → mapped to "interaction expired" message via `_map_gemini_error`. |
| **`is_background_model("deep-research-...")` returns True** | Substring match on "deep-research" (see `config.py`). Covers all three DR agent IDs. |
| **Pricing (preview)** | Fast tier $1-3/task, max tier $3-7/task. Free tier ineligible. |

### Cancel behavior (defensive)

`client.aio.interactions.cancel(id)` exists. Behavior under load (e.g.
overscoped prompt that triggers server-side capacity rejection) can
return HTTP 500. v1 implementation treats 5xx as best-effort: returns
`{"status": "cancelled", "best_effort": True}` so the runtime's SIGINT
path completes cleanly. The actual server state surfaces on the next
`check_status` poll.

For post-restart `doxa cancel <op-id>` (when `self.jobs` is empty),
the cancel path seeds a minimal jobs entry and attempts the upstream
call directly — does NOT short-circuit to `not_found` for DR-shaped
job IDs.

### v1 deferred / open questions

- `requires_action` trigger conditions — Task 6a spike found 0 of 3
  probe configurations triggered it (tool=code_execution,
  collaborative_planning, tool=file_search). v1 treats as
  `permanent_error + failure_type=requires_action` with a useful
  error message.
- `incomplete` recoverability — NO recovery method on the SDK. v1
  treats as `permanent_error + failure_type=permanent`.
- Per-provider polling tunables — schema defined, runtime wiring v1.1.
- Cancel re-verification — defensive 5xx impl ships; spike re-verify
  deferred to v1.1.

## Mock (`mock.py`)

Synthetic provider for tests. No special conventions. Useful for
exercising the polling loop / checkpoint state machine without API
spend.
