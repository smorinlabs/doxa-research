"""P18-T27: upstream cancel on Ctrl-C inside the shared polling loop.

Exercises ``_maybe_cancel_upstream_and_raise`` directly. The polling loop
itself is the only caller, so testing the helper covers both
``_execute_background`` and ``resume_operation`` (they share
``_run_polling_loop``).

Toggle resolution: ``ctx.cancel_on_interrupt_override`` (CLI flag) wins;
fallback is ``config[execution].cancel_upstream_on_interrupt`` (default
``True``).
"""

from __future__ import annotations

import asyncio
import io
from typing import Any, cast

import pytest
from rich.console import Console

from doxa_research import signals as doxa_signals
from doxa_research.config import ConfigManager
from doxa_research.context import AppContext
from doxa_research.run import _maybe_cancel_upstream_and_raise


@pytest.fixture(autouse=True)
def _reset_interrupt() -> Any:
    doxa_signals._interrupt_event.clear()
    yield
    doxa_signals._interrupt_event.clear()


class _StubConfig:
    """Minimal ConfigManager stand-in: only ``data["execution"]`` matters."""

    def __init__(self, cancel_upstream_on_interrupt: bool = True) -> None:
        self.data = {
            "execution": {
                "cancel_upstream_on_interrupt": cancel_upstream_on_interrupt,
            }
        }


class _StubProvider:
    """Records cancel() calls. Optional behavior knobs simulate corner cases."""

    def __init__(
        self,
        *,
        raise_not_implemented: bool = False,
        return_upstream_unsupported: bool = False,
        hang_seconds: float = 0.0,
    ) -> None:
        self.cancel_calls: list[str] = []
        self.raise_not_implemented = raise_not_implemented
        self.return_upstream_unsupported = return_upstream_unsupported
        self.hang_seconds = hang_seconds

    async def cancel(self, job_id: str) -> dict[str, Any]:
        self.cancel_calls.append(job_id)
        if self.hang_seconds:
            await asyncio.sleep(self.hang_seconds)
        if self.raise_not_implemented:
            raise NotImplementedError("upstream cancel not supported")
        if self.return_upstream_unsupported:
            return {"status": "upstream_unsupported"}
        return {"status": "cancelled"}


def _make_ctx(
    *,
    override: bool | None = None,
    as_json: bool = False,
) -> tuple[AppContext, io.StringIO]:
    """Build an AppContext with a captured Rich Console for assertions."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=200)
    ctx = AppContext(
        config=cast(ConfigManager, _StubConfig()),
        console=console,
        cancel_on_interrupt_override=override,
        as_json=as_json,
    )
    return ctx, buf


def _make_jobs(*provider_specs: tuple[str, _StubProvider, str]) -> dict[str, dict[str, Any]]:
    """Build a jobs dict shaped like ``_run_polling_loop`` does."""
    return {
        name: {"provider": provider, "job_id": job_id} for name, provider, job_id in provider_specs
    }


def test_no_cancel_when_interrupt_not_set() -> None:
    """No interrupt → helper returns silently, no cancel calls, no raise."""
    p = _StubProvider()
    ctx, buf = _make_ctx()
    config = ctx.config
    jobs = _make_jobs(("openai", p, "job-1"))

    # _interrupt_event is cleared by the autouse fixture; helper must no-op.
    asyncio.run(_maybe_cancel_upstream_and_raise(jobs, set(), set(), ctx, config))

    assert p.cancel_calls == []
    assert buf.getvalue() == ""


def test_default_config_triggers_cancel_and_raises() -> None:
    """Toggle defaults to true via config; interrupt fires every provider's cancel."""
    p_oai = _StubProvider()
    p_pplx = _StubProvider()
    ctx, buf = _make_ctx()
    config = ctx.config
    jobs = _make_jobs(("openai", p_oai, "job-1"), ("perplexity", p_pplx, "job-2"))

    doxa_signals._interrupt_event.set()
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(_maybe_cancel_upstream_and_raise(jobs, set(), set(), ctx, config))

    assert p_oai.cancel_calls == ["job-1"]
    assert p_pplx.cancel_calls == ["job-2"]
    out = buf.getvalue()
    assert "Cancelled upstream: openai" in out
    assert "Cancelled upstream: perplexity" in out


def test_cli_override_false_skips_cancel_and_prints_hint() -> None:
    """--no-cancel-on-interrupt skips cancel; hint prints when not --json."""
    p = _StubProvider()
    ctx, buf = _make_ctx(override=False, as_json=False)
    config = ctx.config
    jobs = _make_jobs(("openai", p, "job-1"))

    doxa_signals._interrupt_event.set()
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(_maybe_cancel_upstream_and_raise(jobs, set(), set(), ctx, config))

    assert p.cancel_calls == []
    assert "Upstream job still running" in buf.getvalue()
    assert "doxa cancel" in buf.getvalue()


def test_cli_override_false_with_as_json_suppresses_hint() -> None:
    """When as_json=True the hint is suppressed (would corrupt JSON envelopes)."""
    p = _StubProvider()
    ctx, buf = _make_ctx(override=False, as_json=True)
    config = ctx.config
    jobs = _make_jobs(("openai", p, "job-1"))

    doxa_signals._interrupt_event.set()
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(_maybe_cancel_upstream_and_raise(jobs, set(), set(), ctx, config))

    assert p.cancel_calls == []
    assert buf.getvalue() == ""


def test_completed_and_failed_providers_are_skipped() -> None:
    """Only providers in neither completed nor failed sets get cancel calls."""
    p_done = _StubProvider()
    p_failed = _StubProvider()
    p_running = _StubProvider()
    ctx, _buf = _make_ctx()
    config = ctx.config
    jobs = _make_jobs(
        ("done", p_done, "j-done"),
        ("failed", p_failed, "j-failed"),
        ("running", p_running, "j-running"),
    )

    doxa_signals._interrupt_event.set()
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(_maybe_cancel_upstream_and_raise(jobs, {"done"}, {"failed"}, ctx, config))

    assert p_done.cancel_calls == []
    assert p_failed.cancel_calls == []
    assert p_running.cancel_calls == ["j-running"]


def test_not_implemented_in_one_provider_does_not_block_others() -> None:
    """If one provider raises NotImplementedError, the rest still cancel cleanly."""
    p_oai = _StubProvider()
    p_pplx = _StubProvider(raise_not_implemented=True)
    ctx, buf = _make_ctx()
    config = ctx.config
    jobs = _make_jobs(("openai", p_oai, "job-1"), ("perplexity", p_pplx, "job-2"))

    doxa_signals._interrupt_event.set()
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(_maybe_cancel_upstream_and_raise(jobs, set(), set(), ctx, config))

    assert p_oai.cancel_calls == ["job-1"]
    assert p_pplx.cancel_calls == ["job-2"]  # was attempted
    out = buf.getvalue()
    assert "Cancelled upstream: openai" in out
    # The perplexity attempt failed with NotImplementedError → no success line.
    assert "Cancelled upstream: perplexity" not in out


# ---------------------------------------------------------------------------
# P27-TS10 — upstream_unsupported return shape
# ---------------------------------------------------------------------------
#
# Perplexity's cancel() returns {"status": "upstream_unsupported"} (T01) instead
# of raising NotImplementedError as the pre-P27 default did. The runner must
# distinguish this case from a genuine cancel: print the unsupported notice,
# not the misleading "Cancelled upstream" success line.


def test_upstream_unsupported_does_not_print_cancelled_upstream() -> None:
    """TS10: a {status: upstream_unsupported} return prints the unsupported notice.

    Mirrors `cancel.py:126` rendering for the polling-loop path. Without
    this, P27's PerplexityProvider.cancel() succeeds silently with a
    misleading "Cancelled upstream: perplexity" line — even though no
    upstream cancel actually happened.
    """
    p_oai = _StubProvider()
    p_pplx = _StubProvider(return_upstream_unsupported=True)
    ctx, buf = _make_ctx()
    config = ctx.config
    jobs = _make_jobs(("openai", p_oai, "job-1"), ("perplexity", p_pplx, "job-2"))

    doxa_signals._interrupt_event.set()
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(_maybe_cancel_upstream_and_raise(jobs, set(), set(), ctx, config))

    # Both cancel attempts fired (best-effort; the runner doesn't pre-filter).
    assert p_oai.cancel_calls == ["job-1"]
    assert p_pplx.cancel_calls == ["job-2"]

    out = buf.getvalue()
    # OpenAI: genuine cancel → success line.
    assert "Cancelled upstream: openai" in out
    # Perplexity: upstream_unsupported → MUST NOT claim success.
    assert "Cancelled upstream: perplexity" not in out, (
        "upstream_unsupported result misreported as a successful cancel"
    )
    # The user-visible warning is exactly what cancel.py:126 renders for the
    # `doxa cancel` subcommand path — the polling-loop path mirrors it.
    assert "perplexity" in out
    assert "upstream cancel not supported" in out


def test_upstream_unsupported_alone_still_raises_keyboard_interrupt() -> None:
    """TS10: even when EVERY provider returns upstream_unsupported, KI still raises.

    Local checkpoint marking happens via the bubbled-up KeyboardInterrupt;
    the runner must still raise so the operation gets marked cancelled.
    """
    p = _StubProvider(return_upstream_unsupported=True)
    ctx, buf = _make_ctx()
    config = ctx.config
    jobs = _make_jobs(("perplexity", p, "job-1"))

    doxa_signals._interrupt_event.set()
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(_maybe_cancel_upstream_and_raise(jobs, set(), set(), ctx, config))

    out = buf.getvalue()
    assert "Cancelled upstream: perplexity" not in out
    assert "Cancelled upstream: perplexity" not in out


def test_hung_cancel_unwinds_within_5s_envelope() -> None:
    """If cancel() hangs forever, the 5s wait_for budget unwinds and we still raise."""
    p = _StubProvider(hang_seconds=60)  # would hang for a minute without timeout
    ctx, _buf = _make_ctx()
    config = ctx.config
    jobs = _make_jobs(("openai", p, "job-1"))

    doxa_signals._interrupt_event.set()

    async def _run_with_clock() -> float:
        loop = asyncio.get_running_loop()
        start = loop.time()
        try:
            await _maybe_cancel_upstream_and_raise(jobs, set(), set(), ctx, config)
        except KeyboardInterrupt:
            pass
        return loop.time() - start

    elapsed = asyncio.run(_run_with_clock())
    # 5s envelope + small overhead. If wait_for were missing, this would be ~60s.
    assert elapsed < 6.5, f"helper hung past the 5s envelope: {elapsed:.2f}s"
    assert p.cancel_calls == ["job-1"]


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"status": "cancelling"}, "confirmation is pending"),
        ({"status": "completed"}, "Already completed upstream"),
        ({"status": "already_terminal", "previous": "completed"}, "Already completed upstream"),
        ({"status": "cancelled", "best_effort": True}, "Cancellation outcome unconfirmed"),
        ({"status": "unknown"}, "Cancellation outcome unconfirmed"),
    ],
)
def test_interrupt_cancel_reports_pending_terminal_and_uncertain_outcomes(result, expected):
    class Provider(_StubProvider):
        async def cancel(self, job_id):
            self.cancel_calls.append(job_id)
            return result

    provider = Provider()
    ctx, output = _make_ctx()
    jobs = _make_jobs(("perplexity", provider, "agent:saved-id"))
    doxa_signals._interrupt_event.set()
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(_maybe_cancel_upstream_and_raise(jobs, set(), set(), ctx, ctx.config))
    assert provider.cancel_calls == ["agent:saved-id"]
    assert expected in output.getvalue()
    assert "Cancelled upstream" not in output.getvalue()
