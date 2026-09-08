# Testing conventions

Root `CLAUDE.md` covers test-run commands (Fast Iteration Loop) and the
real-API marker schedule. This file covers test-AUTHORING conventions that
subagents otherwise re-derive from reading existing files.

## Async test pattern

This repo uses `def test_*(...)` with `asyncio.run(_inner_async())` —
NOT `@pytest.mark.asyncio + async def`.

```python
def test_some_async_path(self, monkeypatch):
    async def _run():
        # ... async test body ...
        result = await provider._deep_research_submit("prompt", "mode", None, False)
        assert result.startswith("interactions/")
    asyncio.run(_run())
```

Ratio in `tests/test_provider_gemini.py`: 25+ uses of `asyncio.run`,
0 uses of `@pytest.mark.asyncio`. Match it when adding tests.
Reason: avoids the `pytest-asyncio` plugin dep for a small async surface.

## Mocking provider SDKs — the read-only-property gotcha

Some SDKs (notably `google.genai`) expose `client.aio.<resource>` as a
read-only `@property`. The naïve setter raises `AttributeError`:

```python
# WRONG — AttributeError: property 'interactions' has no setter
provider.client.aio.interactions = MagicMock()

# RIGHT — replace the entire client
provider.client = MagicMock()
provider.client.aio.interactions.create = AsyncMock(return_value=...)
```

This applies to Gemini's `client.aio.interactions`; verify case-by-case
for other SDKs. The OpenAI Python SDK and the Perplexity OpenAI-compat
client do NOT have this restriction.

## Mocking provider exceptions — real constructors only

Some provider SDKs use private exception modules with non-obvious
constructors. For Gemini's Deep Research exceptions
(`{BadRequestError, NotFoundError, ...}`, resolved by the
`_sdk_interactions_errors()` helper because the module path moved in
google-genai 2.0),
the signature is:

```python
ExceptionClass(message: str, *, response: httpx.Response, body: object | None)
# NOT (status_code=..., message=...)
```

Build a real `httpx.Request + Response` pair in test fixtures:

```python
req = httpx.Request("POST", "https://example.com")
resp = httpx.Response(404, request=req)
exc = NotFoundError("Interaction not found", response=resp, body=None)
```

If `tests/conftest.py` (or a future `tests/_helpers.py`) ever ships a
`make_interactions_error(status_code, message)` builder, use it
instead of constructing raw exceptions per-test.

## File naming and location

| Pattern | Purpose | Marker |
|---|---|---|
| `tests/test_provider_<name>.py` | Unit tests with monkeypatched SDK | (none — runs by default) |
| `tests/test_vcr_<name>.py` | Cassette replay (when used) | (none) |
| `tests/extended/test_<name>_real_workflows.py` | Live-API CLI workflow tests | `@pytest.mark.live_api` |
| `tests/extended/test_model_kind_runtime.py` | Auto-iterates `KNOWN_MODELS`; covers drift for ALL providers | `@pytest.mark.extended` |

Adding a new mode to `BUILTIN_MODES` / `KNOWN_MODELS` is auto-covered
by the extended drift test — no per-mode test file needed.

## Marker gating (recap)

`pyproject.toml` sets `addopts = "-m 'not extended and not live_api'"`,
so `live_api` and `extended` tests are deselected by default. They run
only when invoked explicitly via `just test-extended` / `just test-live-api`
or via the nightly / weekly GitHub workflows.

`extended_slow` is a sub-marker for tests that take >1 min wall-time;
opt in with `THOTH_EXTENDED_SLOW=1` (legacy env name) or via
`pytest -m extended_slow`.

## Long-running test budgets

If a test calls `subprocess.run(...)` with `doxa ask --mode gemini_*_research`
or another DR mode, set `timeout=20*60` minimum — Deep Research fast tier
can take 5-15 minutes wall-clock; max tier longer.

## Don't add VCR cassettes for Gemini in v1

P24 chose `unittest.mock.patch`-based monkeypatching over VCR. P28 follows.
VCR support is an open option for v1.1+ — don't add cassettes without
the project owner's sign-off.
