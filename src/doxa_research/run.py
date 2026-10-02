"""Research execution loop.

Holds the async functions that run a research operation end-to-end:
discovery of previous outputs, duration estimates, the submit path,
the polling loop, and resume/reconnect. Console I/O uses a local
Rich Console; signal-handling state is routed through ``doxa_research.signals``
so SIGINT can still checkpoint the active operation.
"""

from __future__ import annotations

import asyncio
import math
import random
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import aiofiles
import click
from rich.console import Console
from rich.markup import escape as _rich_escape
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

import doxa_research.signals as _doxa_signals
from doxa_research.checkpoint import CheckpointManager
from doxa_research.config import ConfigManager, get_config
from doxa_research.context import AppContext
from doxa_research.errors import APIKeyError, DiskSpaceError, DoxaError, ProviderError
from doxa_research.hints import print_hint, print_saved_not_submitted
from doxa_research.models import OperationStatus
from doxa_research.output import OutputManager
from doxa_research.progress import run_with_spinner, should_show_spinner
from doxa_research.providers import ResearchProvider, available_providers, create_provider
from doxa_research.providers.base import Citation, StreamEvent
from doxa_research.signals import _interrupt_event
from doxa_research.utils import (
    check_disk_space,
    generate_operation_id,
    mask_api_key,
    md_link_title,
    md_link_url,
)

console = Console()


@contextmanager
def _maybe_spinner(*, model, async_mode, verbose, label, expected_minutes=20):
    if should_show_spinner(model=model, async_mode=async_mode, verbose=verbose):
        with run_with_spinner(label, expected_minutes=expected_minutes):
            yield
    else:
        yield


@contextmanager
def _poll_display(
    *,
    quiet: bool,
    mode_model: str | None,
    verbose: bool,
    label: str = "Deep research running",
    expected_minutes: int = 20,
    rich_console: Console | None = None,
    mode_cfg: dict[str, Any] | None = None,
):
    """Yield ONE polling-display context: Progress XOR spinner XOR none.

    P18: when ``mode_cfg`` declares ``kind = "immediate"``, NEITHER the
    spinner NOR the Progress bar engages — immediate runs complete in one
    polling tick and any progress UI is theatre. Yield ``None`` for that
    case; the polling loop's existing per-tick render code handles ``None``
    by simply not rendering.

    When ``should_show_spinner`` engages (background mode, sync, TTY,
    non-verbose, non-quiet), the spinner runs alone. Otherwise (background
    mode in non-TTY, verbose, or quiet) the legacy ``rich.Progress`` bar
    runs as before.
    """
    target_console = rich_console if rich_console is not None else console
    if mode_cfg is not None:
        from doxa_research.config import mode_kind as _mode_kind

        if _mode_kind(mode_cfg) != "background":
            # Immediate runs: yield no display. The polling loop tolerates
            # `progress=None`; immediate runs only spin the loop once because
            # OpenAIProvider.check_status short-circuits non-background jobs.
            yield None
            return
    if not quiet and should_show_spinner(
        model=mode_model, async_mode=False, verbose=verbose, mode_cfg=mode_cfg
    ):
        with run_with_spinner(label, expected_minutes=expected_minutes, console=target_console):
            yield None
    else:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            TextColumn("| Next poll: {task.fields[next_poll]}s"),
            console=target_console,
            disable=quiet,
        ) as progress:
            yield progress


async def find_latest_outputs(
    current_mode: str, project: str | None, config: ConfigManager
) -> list[Path]:
    """Find latest outputs from previous mode in chain"""
    mode_config = config.get_mode_config(current_mode)
    previous_mode = mode_config.get("previous")

    if not previous_mode:
        return []

    if project:
        search_dir = Path(config.data["paths"]["base_output_dir"]) / project
    else:
        search_dir = Path.cwd()

    if not search_dir.exists():
        return []

    pattern = f"*_*_{previous_mode}_*_*.md"
    files = sorted(search_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)

    valid_files = []
    for file in files:
        parts = file.stem.split("_")
        if len(parts) >= 5:
            try:
                datetime.strptime(f"{parts[0]}_{parts[1]}", "%Y-%m-%d_%H%M%S")
                if parts[2] == previous_mode:
                    valid_files.append(file)
            except ValueError:
                continue

    providers_found = set()
    result = []
    for file in valid_files:
        parts = file.stem.split("_")
        if len(parts) >= 4:
            provider = parts[3]
            if provider != "combined" and provider not in providers_found:
                providers_found.add(provider)
                result.append(file)

    return result


def get_estimated_duration(mode: str, provider: str | None) -> float:
    """Get estimated duration in seconds based on mode and provider"""
    estimates = {
        "thinking": {"openai": 10, "perplexity": 8, "gemini": 8, "mock": 5},
        "clarification": {"openai": 15, "perplexity": 12, "gemini": 12, "mock": 5},
        "exploration": {"openai": 20, "perplexity": 15, "gemini": 15, "mock": 10},
        "deep_research": {
            "openai": 22,
            "perplexity": 20,
            "gemini": 20,
            "combined": 23,
            "mock": 2,
        },
    }

    mode_estimates = estimates.get(mode, {"default": 60})
    if provider:
        return mode_estimates.get(provider, 60)
    else:
        return mode_estimates.get("combined", max(mode_estimates.values(), default=60))


def _select_providers(
    provider: str | None,
    mode: str,
    mode_config: dict[str, Any],
    config: ConfigManager,
    cli_api_keys: dict[str, str | None] | None,
) -> list[str]:
    """Resolve which providers the runner should attempt, in order.

    Precedence (P36 Layer 3):

      1. Explicit ``--provider`` flag wins.
      2. ``thinking`` mode or a mode pinning a single ``provider``.
      3. A mode pinning a ``providers`` list (mux flow).
      4. ``general.default_provider`` config key, when its provider has
         a resolvable key.
      5. First entry in ``available_providers(...)`` (PROVIDERS dict order).
      6. ``["openai"]`` legacy fallback so ``create_provider`` raises
         Layer 2's enhanced ``APIKeyError`` with full multi-provider
         status enumeration when zero providers have keys.
    """
    if provider:
        return [provider]
    if mode == "thinking" or "provider" in mode_config:
        return [mode_config.get("provider", "openai")]
    if "providers" in mode_config:
        return list(mode_config.get("providers", ["openai"]))

    available = available_providers(config, cli_api_keys=cli_api_keys)
    if not available:
        return ["openai"]

    configured_default = config.data.get("general", {}).get("default_provider")
    if configured_default in available:
        return [configured_default]
    return [available[0]]


async def run_research(
    mode: str,
    prompt: str,
    async_mode: bool,
    project: str | None,
    output_dir: str | None,
    provider: str | None,
    input_file: str | None,
    auto: bool,
    verbose: bool,
    cli_api_keys: dict[str, str | None] | None = None,
    combined: bool = False,
    quiet: bool = False,
    no_metadata: bool = False,
    timeout_override: float | None = None,
    ctx: AppContext | None = None,
    model_override: str | None = None,
    out_specs: tuple[str, ...] = (),
    append: bool = False,
    profile: str | None = None,
):
    """Execute research operation.

    ``ctx`` carries the shared runtime dependencies (config, console,
    interrupt event). When not provided, a fresh one is constructed from
    ``get_config()`` so doxa_test can keep calling this function without
    threading ctx through every test case.
    """

    config = get_config(profile=profile)
    if ctx is None:
        ctx = AppContext(config=config, verbose=verbose)
    console = ctx.console  # noqa: F811 — shadow module-level console with ctx's

    output_path = Path(output_dir) if output_dir else Path.cwd()

    if output_dir and not output_path.exists():
        output_path.mkdir(parents=True, exist_ok=True)

    if not check_disk_space(output_path):
        raise DiskSpaceError("Insufficient disk space. Free up at least 100MB")

    if not mode or not prompt:
        raise click.BadParameter("Both mode and prompt are required for research operations")

    from doxa_research.config_profiles import assemble_prompt_with_prefix

    prompt = assemble_prompt_with_prefix(config, mode, prompt)

    operation_id = generate_operation_id()

    if verbose:
        console.print(f"[dim]Operation ID: {operation_id}[/dim]")

    input_files = []
    if input_file:
        input_files.append(Path(input_file))
    elif auto:
        mode_config = config.get_mode_config(mode)
        mode_auto = mode_config.get("auto_input")
        global_auto = config.data["execution"].get("auto_input", False)

        if mode_auto is not None:
            use_auto = mode_auto
        else:
            use_auto = global_auto

        if use_auto:
            input_files = await find_latest_outputs(mode, project, config)
            if not input_files:
                console.print("[yellow]Warning:[/yellow] No previous outputs found for auto-input")

    operation = OperationStatus(
        id=operation_id,
        prompt=prompt,
        mode=mode,
        status="queued",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        project=project,
        input_files=input_files,
    )

    checkpoint_manager = CheckpointManager(config)
    output_manager = OutputManager(config, no_metadata=no_metadata)

    ctx.checkpoint_manager = checkpoint_manager
    ctx.output_manager = output_manager
    ctx.current_operation = operation
    # Mirror into signals module globals so the SIGINT handler and tests
    # that read doxa_research.__main__._current_* observe the same state.
    _doxa_signals._current_checkpoint_manager = checkpoint_manager
    _doxa_signals._current_operation = operation

    await checkpoint_manager.save(operation)

    mode_config = config.get_mode_config(mode)

    if model_override is not None:
        # Patch both the mode-generic model AND the matching per-provider
        # namespace if narrowing with --provider X. Otherwise the
        # parameter_config.py merge order (namespace > generic) would
        # silently drop the override on modes that have a per-provider
        # namespace (e.g. openai_quick, all_deep_research after narrowing).
        mode_config = {**mode_config, "model": model_override}
        if provider:
            existing_ns = mode_config.get(provider)
            if isinstance(existing_ns, dict):
                mode_config[provider] = {**existing_ns, "model": model_override}

    providers_to_use = _select_providers(
        provider=provider,
        mode=mode,
        mode_config=mode_config,
        config=config,
        cli_api_keys=cli_api_keys,
    )

    # Preflight: refuse flag/mode combinations that would silently no-op.
    # Each error names the flag, the mode, and the correct alternative.
    from doxa_research.config import mode_kind as _mode_kind
    from doxa_research.errors import (
        BackgroundFlagError,
        CombinedNeedsMultiProviderError,
        ImmediateMultiProviderError,
        ModelOverrideMultiProviderError,
    )

    _resolved_kind = _mode_kind(mode_config)

    # Immediate modes are single-provider by design (two model spaces don't mix).
    if _resolved_kind == "immediate" and len(providers_to_use) > 1:
        raise ImmediateMultiProviderError(mode, providers_to_use)

    # Streaming-only flags don't make sense on background modes.
    if _resolved_kind == "background":
        if out_specs:
            raise BackgroundFlagError("--out", mode)
        if append:
            raise BackgroundFlagError("--append", mode)

    # --combined needs N providers to combine.
    if combined and len(providers_to_use) < 2:
        raise CombinedNeedsMultiProviderError(mode, providers_to_use)

    # --model NAME is ambiguous on multi-provider modes (per-provider
    # namespace models would win in the merge order anyway). Force the user
    # to narrow with --provider first.
    if model_override is not None and len(providers_to_use) > 1:
        raise ModelOverrideMultiProviderError(mode, providers_to_use, model_override)

    providers = {}
    for provider_name in providers_to_use:
        try:
            provider_cli_key = None
            if cli_api_keys:
                provider_cli_key = cli_api_keys.get(provider_name)
            providers[provider_name] = create_provider(
                provider_name,
                config,
                cli_api_key=provider_cli_key,
                timeout_override=timeout_override,
                mode_config=mode_config,
            )
        except APIKeyError as e:
            console.print(f"[red]Error:[/red] {e.message}")
            console.print(f"[yellow]Suggestion:[/yellow] {_rich_escape(e.suggestion or '')}")
            raise click.Abort()
        except ProviderError as e:
            console.print(f"[red]Error:[/red] {e.message}")
            console.print(f"[yellow]Suggestion:[/yellow] {_rich_escape(e.suggestion or '')}")
            if verbose and hasattr(e, "raw_error") and e.raw_error:
                console.print(f"[dim]Raw error: {e.raw_error}[/dim]")
            raise click.Abort()
        except DoxaError as e:
            console.print(f"[red]Error:[/red] {e.message}")
            console.print(f"[yellow]Suggestion:[/yellow] {_rich_escape(e.suggestion or '')}")
            raise click.Abort()

    if verbose:
        for provider_name, provider_instance in providers.items():
            if hasattr(provider_instance, "api_key") and provider_instance.api_key:
                masked = mask_api_key(provider_instance.api_key)
                console.print(f"[dim]{provider_name} API Key: {masked}[/dim]")
            if hasattr(provider_instance, "config") and "timeout" in provider_instance.config:
                timeout_value = provider_instance.config.get("timeout")
                console.print(f"[dim]{provider_name} Timeout: {timeout_value}s[/dim]")

    if async_mode:
        try:
            for provider_name, provider_instance in providers.items():
                system_prompt = mode_config.get("system_prompt", "")
                job_id = await provider_instance.submit(
                    prompt, mode, system_prompt, verbose=verbose
                )
                operation.providers[provider_name] = {
                    "status": "running",
                    "job_id": job_id,
                }

            operation.transition_to("running")
            await checkpoint_manager.save(operation)

            if not quiet:
                console.print("[green]✓[/green] Research submitted")
            console.print(f"Operation ID: [bold]{operation_id}[/bold]")
            if not quiet:
                console.print(f"\nCheck status: [dim]doxa status {operation_id}[/dim]")
            return operation_id
        except (ProviderError, DoxaError) as e:
            operation.transition_to("failed", error=e.message)
            await checkpoint_manager.save(operation)
            console.print(f"[red]Error:[/red] {e.message}")
            console.print(f"[yellow]Suggestion:[/yellow] {_rich_escape(e.suggestion or '')}")
            if verbose and hasattr(e, "raw_error") and getattr(e, "raw_error", None):
                console.print(f"[dim]Raw error: {e.raw_error}[/dim]")
            print_saved_not_submitted(operation_id)
            raise click.Abort()
        except Exception as e:
            operation.transition_to("failed", error=str(e))
            await checkpoint_manager.save(operation)
            console.print(f"\n[red]Unexpected error during submission:[/red] {str(e)}")
            if verbose:
                console.print(f"[dim]Full error: {repr(e)}[/dim]")
            print_saved_not_submitted(operation_id)
            raise click.Abort()

    # P18 Phase E: dispatch on mode_kind. Immediate-kind runs route to
    # `_execute_immediate` which streams via `provider.stream()` to a
    # MultiSink (stdout / file / tee) and falls back to submit + get_result
    # if the provider doesn't implement streaming. Background runs continue
    # through the existing polling-loop path. The `--out`/`--append` flags
    # are honored only on the immediate path; background runs ignore them
    # (deferred per spec §3 / §10).
    from doxa_research.config import mode_kind as _mode_kind_local

    is_immediate = _mode_kind_local(mode_config) == "immediate"

    try:
        if is_immediate and not async_mode:
            await _execute_immediate(
                operation,
                checkpoint_manager,
                output_manager,
                config,
                mode_config,
                providers,
                quiet,
                verbose,
                output_dir,
                project,
                mode,
                prompt,
                out_specs,
                append,
                ctx=ctx,
            )
        else:
            await _execute_research(
                operation,
                checkpoint_manager,
                output_manager,
                config,
                mode_config,
                providers,
                quiet,
                verbose,
                output_dir,
                combined,
                project,
                mode,
                prompt,
                ctx=ctx,
            )
    except (click.Abort, KeyboardInterrupt):
        if operation.status not in ("cancelled", "failed", "completed"):
            operation.transition_to("cancelled")
            await checkpoint_manager.save(operation)
        raise
    except Exception as e:
        if operation.status not in ("cancelled", "failed", "completed"):
            operation.transition_to("failed", error=str(e))
            await checkpoint_manager.save(operation)
        raise

    if operation.status == "failed":
        raise click.Abort()


def _format_reasoning_block(reasoning_chunks: list[str]) -> str:
    """Render buffered reasoning as a clearly separated markdown section."""
    text = "".join(reasoning_chunks).strip()
    if not text:
        return ""
    return "\n\n## Reasoning\n\n" + text + "\n"


def _format_citations_block(citations: list[Citation]) -> str:
    """Render structured citations as a `## Sources` markdown block.

    Deduplicates by URL (the provider already dedupes, but a downstream
    `--combined` run may join two providers' citation streams).
    """
    seen_urls: set[str] = set()
    lines: list[str] = []
    for entry in citations:
        title = entry.title.strip()
        url = entry.url.strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        lines.append(f"- [{md_link_title(title or url)}]({md_link_url(url)})")
    if not lines:
        return ""
    return "\n\n## Sources\n\n" + "\n".join(lines) + "\n"


def _citation_from_event(event: StreamEvent) -> Citation | None:
    """Return structured citation data from a citation stream event.

    P23 remediation stopped using `title|url` for new providers. The string
    fallback keeps older test doubles or third-party providers from breaking.
    """
    if event.citation is not None:
        return event.citation
    if "|" not in event.text:
        return None
    title, _, url = event.text.partition("|")
    return Citation(title=title, url=url)


async def _execute_immediate(
    operation: OperationStatus,
    checkpoint_manager: CheckpointManager,
    output_manager: OutputManager,
    config: ConfigManager,
    mode_config: dict,
    providers: dict,
    quiet: bool,
    verbose: bool,
    output_dir: str | None,
    project: str | None,
    mode: str,
    prompt: str,
    out_specs: tuple[str, ...],
    append: bool,
    ctx: AppContext | None = None,
) -> None:
    """P18 Phase E: streaming execution path for immediate-kind runs.

    Uses `provider.stream()` to deliver tokens to a `MultiSink` (stdout /
    file / tee). Falls back to `submit() + get_result()` for providers that
    don't implement streaming yet (e.g., Mock pre-Phase E, or future
    providers). Saves the aggregated result via `output_manager.save_result`
    only if `--project` is set (matching pre-P18 background behavior of
    only writing checkpoints when persistence is requested).

    Skips the polling loop entirely — single provider, single stream call,
    no Progress bar, no operation-ID echo, no resume hint.
    """
    if ctx is None:
        ctx = AppContext(config=config, verbose=verbose)
    target_console = ctx.console  # noqa: F811

    from doxa_research.sinks import MultiSink

    # Single-provider immediate runs are the norm. Pick the first one.
    provider_name, provider_instance = next(iter(providers.items()))
    operation.providers[provider_name] = {"status": "running", "job_id": "<streaming>"}
    operation.transition_to("running")
    # Always save checkpoint — `doxa list` / `doxa status` / `doxa resume`
    # need it, and the global checkpoint dir lives outside the project dir
    # anyway. `--project` only governs whether we ALSO save the result file.
    await checkpoint_manager.save(operation)

    system_prompt = mode_config.get("system_prompt") or ""
    aggregated: list[str] = []
    reasoning: list[str] = []
    citations: list[Citation] = []

    # P23-T06: explicit non-stream opt-out via mode_config['stream'] = False.
    # Skips provider.stream() entirely and uses the submit/get_result path.
    explicit_no_stream = mode_config.get("stream") is False

    sink = MultiSink.from_specs(list(out_specs), append=append)
    try:
        try:
            if explicit_no_stream:
                job_id = await provider_instance.submit(
                    prompt, mode, system_prompt, verbose=verbose
                )
                operation.providers[provider_name]["job_id"] = job_id
                content = await provider_instance.get_result(job_id, verbose=verbose)
                sink.write(content)
                aggregated.append(content)
            else:
                try:
                    async for event in provider_instance.stream(
                        prompt, mode, system_prompt, verbose=verbose
                    ):
                        if event.kind == "text":
                            sink.write(event.text)
                            aggregated.append(event.text)
                        elif event.kind == "reasoning":
                            reasoning.append(event.text)
                        elif event.kind == "citation":
                            citation = _citation_from_event(event)
                            if citation is not None:
                                citations.append(citation)
                        elif event.kind == "done":
                            break
                except NotImplementedError:
                    # Fallback for providers that haven't implemented stream yet.
                    job_id = await provider_instance.submit(
                        prompt, mode, system_prompt, verbose=verbose
                    )
                    operation.providers[provider_name]["job_id"] = job_id
                    content = await provider_instance.get_result(job_id, verbose=verbose)
                    sink.write(content)
                    aggregated.append(content)
        except Exception as e:
            # Streaming runs treat any exception as a permanent failure —
            # immediate-kind ops have no upstream job to retry/resume against.
            target_console.print(
                f"\n[red]✗[/red] {provider_name.title()} permanent failure: {str(e)}"
            )
            operation.providers[provider_name]["status"] = "failed"
            operation.providers[provider_name]["failure_type"] = "permanent"
            operation.providers[provider_name]["error"] = str(e)
            operation.transition_to("failed", error=str(e))
            operation.failure_type = "permanent"
            await checkpoint_manager.save(operation)
            return
        if reasoning:
            reasoning_block = _format_reasoning_block(reasoning)
            sink.write(reasoning_block)
            aggregated.append(reasoning_block)
        # P23: render buffered citations as a `## Sources` markdown block to
        # every selected sink, after text/reasoning events have flushed.
        if citations:
            sources_block = _format_citations_block(citations)
            sink.write(sources_block)
            aggregated.append(sources_block)
    finally:
        sink.close()

    final_text = "".join(aggregated)
    operation.providers[provider_name]["status"] = "completed"

    # Persist via output_manager when the user opted in via ANY destination
    # flag — either --project NAME (project subdirectory namespace) or
    # --output-dir DIR (explicit directory). Both flags express "I want a
    # file here"; without either, the streamed output is the user-visible
    # result and no auto-named file is also written. Standardization note:
    # `--project` was previously the sole gate; making `--output-dir` an
    # equal trigger fixes a silent-ignore bug and removes the side-effect
    # overload of `--project` (which previously meant both "subdirectory
    # namespace" AND "please persist").
    if project or output_dir:
        provider_model = getattr(provider_instance, "model", None)
        output_path = await output_manager.save_result(
            operation,
            provider_name,
            final_text,
            output_dir,
            model=provider_model,
            system_prompt=system_prompt,
        )
        operation.output_paths[provider_name] = output_path

    operation.transition_to("completed")
    await checkpoint_manager.save(operation)

    if not quiet and (project or output_dir):
        # Use the actually-resolved output directory rather than reconstructing
        # from --project / --output-dir, which would lie when --project is set
        # without --output-dir (the file lands in base_output_dir/<project>/).
        op_path = operation.output_paths.get(provider_name)
        saved_to = str(op_path.parent) if op_path else str(output_dir or "")
        target_console.print(f"\n[green]✓[/green] Saved to: [dim]{saved_to}[/dim]")


async def _maybe_cancel_upstream_and_raise(
    jobs: dict[str, dict[str, Any]],
    completed_providers: set[str],
    failed_providers: set[str],
    ctx: AppContext,
    config: ConfigManager,
) -> None:
    """If interrupted, attempt parallel upstream cancel then raise KeyboardInterrupt.

    P18-T27: only the polling loop reaches this path, so the upstream-cancel
    behavior fires for sync background runs and resume runs. ``--async``
    submissions exit before Ctrl-C lands; immediate-kind runs never enter the
    polling loop.

    Resolution order for the toggle:
      1. AppContext.cancel_on_interrupt_override (CLI flag, None if not given)
      2. config[execution].cancel_upstream_on_interrupt (default True)

    When the toggle is False and ``ctx.as_json`` is also False, prints a
    one-line hint pointing at ``doxa cancel`` so the user knows the upstream
    job is still billing.
    """
    if not _interrupt_event.is_set():
        return

    cli_override = ctx.cancel_on_interrupt_override
    config_default = config.data.get("execution", {}).get("cancel_upstream_on_interrupt", True)
    should_cancel = cli_override if cli_override is not None else config_default

    if should_cancel:
        in_flight = [
            (name, info)
            for name, info in jobs.items()
            if name not in completed_providers and name not in failed_providers
        ]
        if in_flight:
            tasks = [info["provider"].cancel(info["job_id"]) for _, info in in_flight]
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=5,
                )
                for (name, _), result in zip(in_flight, results, strict=False):
                    if isinstance(result, BaseException):
                        # Swallow NotImplementedError / network failures; best-effort.
                        continue
                    status = result.get("status") if isinstance(result, dict) else None
                    if status == "upstream_unsupported":
                        ctx.console.print(
                            f"[yellow]⚠ {name}: upstream cancel not supported; "
                            f"local checkpoint marked cancelled[/yellow]"
                        )
                    elif isinstance(result, dict) and result.get("best_effort"):
                        ctx.console.print(
                            f"[yellow]Cancellation outcome unconfirmed:[/yellow] {name}"
                        )
                    elif status == "cancelling":
                        ctx.console.print(
                            f"[yellow]Cancellation requested upstream:[/yellow] {name}; confirmation is pending"
                        )
                    elif status == "completed" or (
                        status == "already_terminal" and result.get("previous") == "completed"
                    ):
                        ctx.console.print(f"[yellow]Already completed upstream:[/yellow] {name}")
                    elif status == "cancelled":
                        ctx.console.print(f"[yellow]Cancelled upstream:[/yellow] {name}")
                    else:
                        ctx.console.print(
                            f"[yellow]Cancellation outcome unconfirmed:[/yellow] {name}"
                        )

            except TimeoutError:
                pass  # 5s envelope exceeded; exit anyway
    elif not ctx.as_json:
        op = ctx.current_operation
        op_id = op.id if op is not None else None
        target = f"`doxa cancel {op_id}`" if op_id else "`doxa cancel <op-id>`"
        ctx.console.print(f"[dim]Upstream job still running; run {target} to stop billing.[/dim]")

    raise KeyboardInterrupt


async def _run_polling_loop(
    operation: OperationStatus,
    jobs: dict[str, dict[str, Any]],
    progress: Progress | None,
    checkpoint_manager: CheckpointManager,
    output_manager: OutputManager,
    config: ConfigManager,
    mode_config: dict,
    output_dir: str | None,
    verbose: bool,
    ctx: AppContext | None = None,
) -> tuple[set[str], set[str]]:
    """Poll all jobs until each completes or fails.

    Handles transient-error retries with per-provider backoff, permanent
    failures, and timeouts. Updates ``operation`` in place (including
    status transitions on timeout) and writes checkpoints after every
    provider-level status change. Returns
    ``(completed_providers, failed_providers)``.
    """

    if ctx is None:
        ctx = AppContext(config=config, verbose=verbose)
    console = ctx.console  # noqa: F811 — shadow module-level console with ctx's

    poll_refresh_interval = 1.0
    poll_jitter_ratio = 0.10
    min_poll_interval = 0.1

    def _normalize_poll_interval(value: Any) -> float:
        return max(min_poll_interval, float(value))

    def _compute_poll_interval(base_interval: float) -> float:
        jitter_multiplier = 1.0 + random.uniform(-poll_jitter_ratio, poll_jitter_ratio)
        return max(min_poll_interval, base_interval * jitter_multiplier)

    def _next_poll_countdown(now: float, next_poll_at: float) -> int:
        return max(0, math.ceil(next_poll_at - now))

    def _next_poll_sleep(now: float, next_poll_at: float) -> float:
        return max(0.0, min(poll_refresh_interval, next_poll_at - now))

    completed_providers: set[str] = set()
    failed_providers: set[str] = set()
    transient_error_counts: dict[str, int] = {p: 0 for p in jobs}
    max_transient = int(config.data["execution"].get("max_transient_errors", 5))
    base_poll_interval = _normalize_poll_interval(config.data["execution"]["poll_interval"])
    max_wait = config.data["execution"]["max_wait"] * 60
    start_time = asyncio.get_running_loop().time()
    effective_interval = base_poll_interval
    next_poll_at = start_time
    cached_statuses: dict[str, dict[str, Any]] = {}

    while len(completed_providers) < len(jobs):
        await _maybe_cancel_upstream_and_raise(
            jobs, completed_providers, failed_providers, ctx, config
        )
        now = asyncio.get_running_loop().time()
        should_poll = now >= next_poll_at

        if should_poll:
            effective_interval = _compute_poll_interval(base_poll_interval)
            next_poll_at = now + effective_interval

            for provider_name, job_info in jobs.items():
                if provider_name in completed_providers:
                    continue

                status = await job_info["provider"].check_status(job_info["job_id"])
                provider_status = status.get("status")

                if provider_status in ("running", "queued"):
                    transient_error_counts[provider_name] = 0
                    cached_statuses[provider_name] = status
                elif provider_status == "completed":
                    transient_error_counts[provider_name] = 0
                    if progress is not None and job_info.get("task_id") is not None:
                        progress.update(job_info["task_id"], completed=100, next_poll=0)
                    completed_providers.add(provider_name)

                    result_content = await job_info["provider"].get_result(
                        job_info["job_id"], verbose=verbose
                    )
                    provider_model = getattr(job_info["provider"], "model", None)
                    system_prompt = mode_config.get("system_prompt", "")
                    output_path = await output_manager.save_result(
                        operation,
                        provider_name,
                        result_content,
                        output_dir,
                        model=provider_model,
                        system_prompt=system_prompt,
                    )
                    operation.output_paths[provider_name] = output_path
                    operation.providers[provider_name]["status"] = "completed"
                    operation.providers[provider_name].pop("failure_type", None)
                    operation.providers[provider_name].pop("error", None)
                    await checkpoint_manager.save(operation)
                elif provider_status == "transient_error":
                    transient_error_counts[provider_name] += 1
                    count = transient_error_counts[provider_name]
                    error_msg = status.get("error", "transient error")
                    if count >= max_transient:
                        console.print(
                            f"\n[yellow]⚠[/yellow] {provider_name.title()}: "
                            f"{count} consecutive transient errors — last: {error_msg}"
                        )
                        operation.providers[provider_name]["status"] = "failed"
                        operation.providers[provider_name]["failure_type"] = "recoverable"
                        operation.providers[provider_name]["error"] = error_msg
                        completed_providers.add(provider_name)
                        failed_providers.add(provider_name)
                        await checkpoint_manager.save(operation)
                    else:
                        console.print(
                            f"\n[dim]{provider_name.title()}: transient error "
                            f"({count}/{max_transient}) — {error_msg}; retrying[/dim]"
                        )
                        backoff = min(effective_interval * (2 ** (count - 1)), 60.0)
                        next_poll_at = now + backoff
                        cached_statuses[provider_name] = {
                            "status": "running",
                            "progress": cached_statuses.get(provider_name, {}).get("progress", 0.0),
                        }
                elif provider_status == "permanent_error":
                    error_msg = status.get("error", "permanent error")
                    console.print(
                        f"\n[red]✗[/red] {provider_name.title()} permanent failure: {error_msg}"
                    )
                    operation.providers[provider_name]["status"] = "failed"
                    operation.providers[provider_name]["failure_type"] = "permanent"
                    operation.providers[provider_name]["error"] = error_msg
                    completed_providers.add(provider_name)
                    failed_providers.add(provider_name)
                    await checkpoint_manager.save(operation)
                elif provider_status == "cancelled":
                    # Cancelled jobs cannot be resumed via the original provider
                    # job id, so route to permanent to suppress the misleading
                    # `doxa resume` hint.
                    error_msg = status.get("error", "job was cancelled")
                    console.print(f"\n[red]✗[/red] {provider_name.title()} cancelled: {error_msg}")
                    operation.providers[provider_name]["status"] = "failed"
                    operation.providers[provider_name]["failure_type"] = "permanent"
                    operation.providers[provider_name]["error"] = error_msg
                    completed_providers.add(provider_name)
                    failed_providers.add(provider_name)
                    await checkpoint_manager.save(operation)
                else:
                    error_msg = status.get("error", f"unknown status {provider_status!r}")
                    console.print(f"\n[yellow]⚠[/yellow] {provider_name.title()}: {error_msg}")
                    operation.providers[provider_name]["status"] = "failed"
                    operation.providers[provider_name]["failure_type"] = "recoverable"
                    operation.providers[provider_name]["error"] = error_msg
                    completed_providers.add(provider_name)
                    failed_providers.add(provider_name)
                    await checkpoint_manager.save(operation)

        for provider_name, job_info in jobs.items():
            if provider_name in completed_providers:
                continue
            cached = cached_statuses.get(provider_name)
            if cached is not None and cached.get("status") in ("running", "queued"):
                progress_pct = int(cached.get("progress", 0) * 100)
                next_poll = _next_poll_countdown(now, next_poll_at)
                if progress is not None and job_info.get("task_id") is not None:
                    progress.update(
                        job_info["task_id"], completed=progress_pct, next_poll=next_poll
                    )

        if len(completed_providers) >= len(jobs):
            break

        if asyncio.get_running_loop().time() - start_time > max_wait:
            console.print(f"\n[red]Timeout exceeded ({max_wait / 60} minutes)[/red]")
            operation.transition_to("failed", error=f"Timeout exceeded ({max_wait / 60} minutes)")
            operation.failure_type = "recoverable"
            await checkpoint_manager.save(operation)
            ctx.checkpoint_manager = None
            ctx.current_operation = None
            _doxa_signals._current_checkpoint_manager = None
            _doxa_signals._current_operation = None
            return completed_providers, failed_providers

        await asyncio.sleep(_next_poll_sleep(asyncio.get_running_loop().time(), next_poll_at))

    return completed_providers, failed_providers


async def _execute_research(
    operation: OperationStatus,
    checkpoint_manager: CheckpointManager,
    output_manager: OutputManager,
    config: ConfigManager,
    mode_config: dict,
    providers: dict,
    quiet: bool,
    verbose: bool,
    output_dir: str | None,
    combined: bool,
    project: str | None,
    mode: str,
    prompt: str,
    ctx: AppContext | None = None,
):
    """Execute research with providers and track progress."""

    if ctx is None:
        ctx = AppContext(config=config, verbose=verbose)
    console = ctx.console  # noqa: F811 — shadow module-level console with ctx's

    jobs: dict[str, dict[str, Any]] = {}
    for provider_name, provider_instance in providers.items():
        try:
            system_prompt = mode_config.get("system_prompt", "")
            job_id = await provider_instance.submit(prompt, mode, system_prompt, verbose=verbose)
            jobs[provider_name] = {
                "provider": provider_instance,
                "job_id": job_id,
                "task_id": None,
            }
            operation.providers[provider_name] = {
                "status": "running",
                "job_id": job_id,
            }
        except ProviderError as e:
            console.print(f"\n[red]Error:[/red] {e.message}")
            console.print(f"[yellow]Suggestion:[/yellow] {_rich_escape(e.suggestion or '')}")
            if verbose and hasattr(e, "raw_error") and e.raw_error:
                console.print(f"[dim]Raw error: {e.raw_error}[/dim]")
            raise click.Abort()
        except Exception as e:
            console.print(f"\n[red]Unexpected error during submission:[/red] {str(e)}")
            if verbose:
                console.print(f"[dim]Full error: {repr(e)}[/dim]")
            raise click.Abort()

    operation.transition_to("running")
    await checkpoint_manager.save(operation)

    mode_model = mode_config.get("model")
    # P18: gate the resume-hint emission on mode_kind. Immediate-kind runs
    # have no upstream job to reattach to, so a "doxa resume {id}" hint is a
    # dead end. Computed once here, used at the four failure-emission sites
    # below.
    from doxa_research.config import mode_kind as _mode_kind

    is_background_kind = _mode_kind(mode_config) == "background"
    with _poll_display(
        quiet=quiet,
        mode_model=mode_model,
        verbose=verbose,
        rich_console=console,
        mode_cfg=mode_config,
    ) as progress:
        if progress is not None:
            for provider_name, info in jobs.items():
                info["task_id"] = progress.add_task(
                    f"{provider_name.title()} Research",
                    total=100,
                    next_poll=0,
                )
        completed_providers, failed_providers = await _run_polling_loop(
            operation,
            jobs,
            progress,
            checkpoint_manager,
            output_manager,
            config,
            mode_config,
            output_dir,
            verbose,
            ctx=ctx,
        )

    if operation.status == "failed":
        if operation.failure_type == "recoverable" and is_background_kind:
            console.print(
                f"\n[cyan]This failure is recoverable.[/cyan] "
                f"Resume with: [bold]doxa resume {operation.id}[/bold]"
            )
        return

    if len(failed_providers) == len(jobs):
        all_errors = "; ".join(
            operation.providers[p].get("error", "unknown") for p in failed_providers
        )
        op_failure_type = (
            "permanent"
            if all(
                operation.providers[p].get("failure_type") == "permanent" for p in failed_providers
            )
            else "recoverable"
        )
        operation.transition_to("failed", error=f"All providers failed: {all_errors}")
        operation.failure_type = op_failure_type
        await checkpoint_manager.save(operation)
        ctx.checkpoint_manager = None
        ctx.current_operation = None
        _doxa_signals._current_checkpoint_manager = None
        _doxa_signals._current_operation = None
        if op_failure_type == "recoverable" and is_background_kind:
            console.print(
                f"\n[cyan]This failure is recoverable.[/cyan] "
                f"Resume with: [bold]doxa resume {operation.id}[/bold]"
            )
        return

    if len(operation.output_paths) > 1 and combined:
        contents = {}
        for provider_name, path in operation.output_paths.items():
            async with aiofiles.open(path) as f:
                contents[provider_name] = await f.read()

        combined_path = await output_manager.generate_combined_report(
            operation,
            contents,
            output_dir,
            system_prompt=mode_config.get("system_prompt", ""),
        )
        operation.output_paths["combined"] = combined_path

    operation.transition_to("completed")
    await checkpoint_manager.save(operation)

    if not quiet:
        console.print("\n[green]✓[/green] Research completed!")
        if project:
            console.print(
                f"Results saved to: [dim]{config.data['paths']['base_output_dir']}/{project}/[/dim]"
            )
        else:
            console.print("Results saved to: [dim]current directory[/dim]")

    for provider_name, path in operation.output_paths.items():
        console.print(f"  • {path.name}")

    if not quiet and len(operation.output_paths) > 1 and "combined" not in operation.output_paths:
        console.print("\nTo generate a combined report, run with --combined flag")

    # P18: only emit operation-ID echo + status/list hints for background runs.
    # Immediate runs have no resumable job; the user already has the result
    # printed in front of them. `--project` and explicit persistence (Phase E
    # `--out FILE`) still trigger checkpoint writes, but the hints are the
    # background-mode UX.
    if not quiet and is_background_kind:
        console.print(f"\nOperation ID: [bold]{operation.id}[/bold]")
        print_hint(f"doxa status {operation.id}", "Re-check later")
        print_hint("doxa list", "See recent runs")

    ctx.checkpoint_manager = None
    ctx.current_operation = None
    _doxa_signals._current_checkpoint_manager = None
    _doxa_signals._current_operation = None

    return operation.id


def get_resume_snapshot_data(operation_id: str) -> dict | None:
    """Pure data function for ``doxa resume OP_ID --json``.

    Reads the checkpoint and returns a snapshot dict. Per spec §6.8 +
    §8.5, this function NEVER advances state (no provider polling, no
    retries) and maps the on-disk status field as follows:

      status="failed", failure_type="permanent"  -> "failed_permanent"
      status="failed", failure_type!=permanent  -> "recoverable_failure"
      otherwise                                  -> status verbatim

    Synchronous on purpose — completion-style "snapshot" semantics demand
    sub-second response time. Uses an in-line synchronous read of the
    checkpoint JSON rather than CheckpointManager.load (which is async).
    """
    import json as _json
    from pathlib import Path as _Path

    from doxa_research.config import get_config

    config = get_config()
    checkpoint_dir = _Path(config.data["paths"]["checkpoint_dir"])
    checkpoint_file = checkpoint_dir / f"{operation_id}.json"
    if not checkpoint_file.exists():
        return None

    try:
        raw = _json.loads(checkpoint_file.read_text())
    except (_json.JSONDecodeError, OSError):
        return None

    raw_status = raw.get("status")
    failure_type = raw.get("failure_type")

    if raw_status == "failed":
        snapshot_status = (
            "failed_permanent" if failure_type == "permanent" else "recoverable_failure"
        )
    else:
        snapshot_status = raw_status

    return {
        "operation_id": raw.get("id", operation_id),
        "status": snapshot_status,
        "mode": raw.get("mode"),
        "prompt": raw.get("prompt"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "providers": raw.get("providers", {}),
        "last_error": raw.get("error"),
        "failure_type": failure_type,
        "retry_count": raw.get("retry_count", 0),
    }


async def _resume_one_tick(
    operation: OperationStatus,
    provider_instances: dict[str, ResearchProvider],
    output_manager: OutputManager,
    checkpoint_manager: CheckpointManager,
    mode_config: dict,
    ctx: AppContext,
    verbose: bool,
) -> dict[str, Any]:
    """P18-T38: single status-check tick for `doxa resume --async`.

    For each already-reconnected provider, call ``check_status`` once. If a
    provider returns ``"completed"``, fetch and save its result, mark its
    per-provider status, and append its name to ``newly_completed``. Save the
    checkpoint exactly once at the end. Never enters the polling loop.

    The aggregate ``operation.status`` flips to ``"completed"`` only when
    every provider reports completed (locked design decision); otherwise it
    is left untouched.

    Returns a dict ``{"newly_completed": list[str], "all_done": bool}``.
    """
    newly_completed: list[str] = []
    for provider_name, provider in provider_instances.items():
        job_id = operation.providers[provider_name]["job_id"]
        status = await provider.check_status(job_id)
        provider_status = status.get("status")

        if provider_status == "completed":
            result_content = await provider.get_result(job_id, verbose=verbose)
            provider_model = getattr(provider, "model", None)
            system_prompt = mode_config.get("system_prompt", "")
            output_path = await output_manager.save_result(
                operation,
                provider_name,
                result_content,
                None,
                model=provider_model,
                system_prompt=system_prompt,
            )
            operation.output_paths[provider_name] = output_path
            operation.providers[provider_name]["status"] = "completed"
            operation.providers[provider_name].pop("failure_type", None)
            operation.providers[provider_name].pop("error", None)
            newly_completed.append(provider_name)
        elif provider_status in ("permanent_error", "failed"):
            # Locked decision: partial success — record the failure on the
            # affected provider but keep going. Caller exits 0; user can
            # re-run plain `doxa resume` for recoverable retries.
            operation.providers[provider_name]["status"] = "failed"
            operation.providers[provider_name]["error"] = status.get("error", "unknown")
        # `running` / `queued` / anything else: leave checkpoint state untouched.

    all_done = all(
        operation.providers[name].get("status") == "completed" for name in operation.providers
    )
    if all_done:
        operation.transition_to("completed")

    await checkpoint_manager.save(operation)
    return {"newly_completed": newly_completed, "all_done": all_done}


async def resume_operation(
    operation_id: str,
    verbose: bool = False,
    ctx: AppContext | None = None,
    *,
    quiet: bool = False,
    no_metadata: bool = False,
    timeout_override: float | None = None,
    cli_api_keys: dict[str, str | None] | None = None,
    async_check: bool = False,
):
    """Resume an existing operation by reconnecting to its providers.

    Honor-set per Q1-PR2-C: the resume subcommand passes `quiet`,
    `no_metadata`, `timeout_override`, and `cli_api_keys` as keyword
    arguments. The legacy `--resume` flag callsite (cli.py until Task 5)
    passes only the first three positional args; keyword-only defaults
    keep both callsites valid during the transition window.

    P18-T38: when ``async_check=True``, perform exactly one
    ``provider.check_status()`` per non-completed provider, save any
    newly-completed results, update the checkpoint, and return WITHOUT
    entering the polling loop. The return value is the
    ``_resume_one_tick`` dict (``{"newly_completed": [...], "all_done": bool}``)
    so the CLI layer can render either a Rich summary or a JSON envelope.
    """

    config = get_config()
    if ctx is None:
        ctx = AppContext(config=config, verbose=verbose)
    ctx.quiet = quiet
    ctx.no_metadata = no_metadata
    if timeout_override is not None:
        ctx.timeout_override = timeout_override
    if cli_api_keys:
        ctx.cli_api_keys = cli_api_keys
    console = ctx.console  # noqa: F811 — shadow module-level console with ctx's
    checkpoint_manager = CheckpointManager(config)
    output_manager = OutputManager(config, no_metadata=no_metadata)

    operation = await checkpoint_manager.load(operation_id)
    if not operation:
        console.print(f"[red]Error:[/red] Operation {operation_id} not found")
        sys.exit(6)

    if not quiet:
        console.print(f"[yellow]Resuming operation {operation_id}...[/yellow]")
        console.print(f"Prompt: {operation.prompt}")
        console.print(f"Mode: {operation.mode}")
        console.print(f"Status: {operation.status}")

    if operation.status == "completed":
        if not quiet:
            console.print(f"[green]Operation {operation_id} already completed.[/green]")
        return
    if operation.status == "cancelled" and not any(
        p.get("status") != "completed" for p in operation.providers.values()
    ):
        if not quiet:
            console.print(
                f"[yellow]Operation {operation_id} was cancelled with no pending work.[/yellow]"
            )
        return
    if operation.status == "failed" and getattr(operation, "failure_type", None) == "permanent":
        console.print(
            f"[red]Operation {operation_id} failed permanently and cannot be resumed: "
            f"{operation.error}[/red]"
        )
        sys.exit(7)

    mode_config = config.get_mode_config(operation.mode)

    provider_instances: dict[str, ResearchProvider] = {}
    for provider_name, pdata in operation.providers.items():
        if pdata.get("status") == "completed":
            continue
        job_id = pdata.get("job_id")
        if not job_id:
            console.print(f"[red]Provider {provider_name} has no job_id; cannot reconnect.[/red]")
            continue
        try:
            provider = create_provider(
                provider_name,
                config,
                cli_api_key=cli_api_keys.get(provider_name) if cli_api_keys else None,
                timeout_override=timeout_override,
                mode_config=mode_config,
            )
        except (APIKeyError, ProviderError, DoxaError) as e:
            console.print(f"[red]Error:[/red] {e.message}")
            console.print(f"[yellow]Suggestion:[/yellow] {_rich_escape(e.suggestion or '')}")
            sys.exit(1)

        try:
            await provider.reconnect(job_id)
        except NotImplementedError as exc:
            console.print(f"[red]Provider {provider_name} does not support resume: {exc}[/red]")
            sys.exit(1)
        provider_instances[provider_name] = provider

    if not provider_instances:
        if not quiet:
            console.print("[yellow]No providers to resume.[/yellow]")
        return

    if operation.status in ("failed", "cancelled"):
        operation.transition_to("running")
        operation.error = None
        operation.failure_type = None
        for pdata in operation.providers.values():
            if pdata.get("status") == "failed":
                pdata["status"] = "running"
                pdata.pop("failure_type", None)
                pdata.pop("error", None)
        await checkpoint_manager.save(operation)

    ctx.checkpoint_manager = checkpoint_manager
    ctx.output_manager = output_manager
    ctx.current_operation = operation
    _doxa_signals._current_checkpoint_manager = checkpoint_manager
    _doxa_signals._current_operation = operation

    if async_check:
        # P18-T38: drive-by progress check + ready-file download. ONE
        # check_status per provider; never enters the polling loop.
        tick = await _resume_one_tick(
            operation,
            provider_instances,
            output_manager,
            checkpoint_manager,
            mode_config,
            ctx,
            verbose,
        )
        if not quiet and not ctx.as_json:
            if tick["newly_completed"]:
                console.print(
                    "[green]Saved results from:[/green] " + ", ".join(tick["newly_completed"])
                )
            else:
                console.print("[dim]No providers completed since last check.[/dim]")
            if tick["all_done"]:
                console.print(f"[green]Operation {operation.id} fully completed.[/green]")
        return tick

    resume_mode_model = mode_config.get("model")
    with _poll_display(
        quiet=quiet, mode_model=resume_mode_model, verbose=verbose, rich_console=console
    ) as progress:
        jobs: dict[str, dict[str, Any]] = {}
        for provider_name, provider in provider_instances.items():
            job_id = operation.providers[provider_name]["job_id"]
            jobs[provider_name] = {
                "provider": provider,
                "job_id": job_id,
                "task_id": (
                    progress.add_task(
                        f"{provider_name.title()} Research (resume)",
                        total=100,
                        next_poll=0,
                    )
                    if progress is not None
                    else None
                ),
            }

        completed_providers, failed_providers = await _run_polling_loop(
            operation,
            jobs,
            progress,
            checkpoint_manager,
            output_manager,
            config,
            mode_config,
            None,
            verbose,
            ctx=ctx,
        )

        if operation.status == "failed":
            if operation.failure_type == "recoverable":
                console.print(
                    f"\n[cyan]This failure is recoverable.[/cyan] "
                    f"Resume with: [bold]doxa resume {operation.id}[/bold]"
                )
            raise click.Abort()

        total_providers_to_watch = len(jobs)
        if len(failed_providers) == total_providers_to_watch:
            all_errors = "; ".join(
                operation.providers[p].get("error", "unknown") for p in failed_providers
            )
            op_failure_type = (
                "permanent"
                if all(
                    operation.providers[p].get("failure_type") == "permanent"
                    for p in failed_providers
                )
                else "recoverable"
            )
            operation.transition_to("failed", error=f"All providers failed: {all_errors}")
            operation.failure_type = op_failure_type
            await checkpoint_manager.save(operation)
            ctx.checkpoint_manager = None
            ctx.current_operation = None
            _doxa_signals._current_checkpoint_manager = None
            _doxa_signals._current_operation = None
            if op_failure_type == "recoverable":
                console.print(
                    f"\n[cyan]This failure is recoverable.[/cyan] "
                    f"Resume with: [bold]doxa resume {operation.id}[/bold]"
                )
            raise click.Abort()

    operation.transition_to("completed")
    await checkpoint_manager.save(operation)

    if not quiet:
        console.print("\n[green]✓[/green] Research completed!")
        for provider_name, path in operation.output_paths.items():
            console.print(f"  • {path.name}")

    ctx.checkpoint_manager = None
    ctx.current_operation = None
    _doxa_signals._current_checkpoint_manager = None
    _doxa_signals._current_operation = None


__all__ = [
    "_execute_research",
    "_maybe_spinner",
    "_run_polling_loop",
    "find_latest_outputs",
    "get_estimated_duration",
    "get_resume_snapshot_data",
    "resume_operation",
    "run_research",
]
