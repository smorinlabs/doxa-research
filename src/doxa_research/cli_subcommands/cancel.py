"""`doxa cancel OP_ID` Click subcommand.

P18 Phase G. Mirrors the `cli_subcommands/resume.py` pattern. Loads the
operation from checkpoint, invokes `provider.cancel(job_id)` for each
non-completed provider, marks the checkpoint cancelled, and exits 0.

For providers without upstream cancel support (`NotImplementedError`), the
CLI reports "upstream cancel not supported, local checkpoint marked
cancelled" — the operation is still locally cancelled so `doxa status`
and `doxa list` reflect that.

Spec: `docs/superpowers/specs/2026-04-26-p18-immediate-vs-background-design.md`
§5.3 + §5.7.
"""

from __future__ import annotations

import sys

import click

from doxa_research.cli_subcommands._config_context import load_config
from doxa_research.cli_subcommands._option_policy import (
    DEFAULT_HONOR,
    inherited_api_keys,
    inherited_value,
    validate_inherited_options,
)
from doxa_research.completion.sources import operation_ids as _operation_ids_completer

_CANCEL_HONOR = DEFAULT_HONOR | {
    "verbose",
    "quiet",
    "api_key_openai",
    "api_key_perplexity",
    "api_key_mock",
}


@click.command(name="cancel")
@click.argument("operation_id", metavar="OP_ID", shell_complete=_operation_ids_completer)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug output")
@click.option("--config", "-c", "config_path", help="Path to custom config file")
@click.option("--quiet", "-Q", is_flag=True, help="Minimal output")
@click.option("--api-key-openai", help="API key for OpenAI provider")
@click.option("--api-key-perplexity", help="API key for Perplexity provider")
@click.option("--api-key-mock", help="API key for Mock provider")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON envelope")
@click.pass_context
def cancel(
    ctx: click.Context,
    operation_id: str,
    verbose: bool,
    config_path: str | None,
    quiet: bool,
    api_key_openai: str | None,
    api_key_perplexity: str | None,
    api_key_mock: str | None,
    as_json: bool,
) -> None:
    """Cancel an in-flight operation by ID.

    Calls `provider.cancel()` upstream where supported; updates the local
    checkpoint to `cancelled` regardless. Exit codes:
      0 — operation successfully cancelled (or already terminal)
      6 — operation not found
    """
    validate_inherited_options(ctx, "cancel", _CANCEL_HONOR)

    # Local imports to avoid cli.py → cli_subcommands → cli.py circular.
    import asyncio

    from doxa_research.commands import cancel_operation

    inherited = ctx.obj or {}
    effective_config = config_path or inherited.get("config_path")
    effective_profile = inherited_value(ctx, "profile")
    effective_quiet = bool(quiet or inherited.get("quiet"))
    root_api_keys = inherited_api_keys(ctx)
    cli_api_keys = {
        "openai": api_key_openai or root_api_keys["openai"],
        "perplexity": api_key_perplexity or root_api_keys["perplexity"],
        "mock": api_key_mock or root_api_keys["mock"],
    }

    def _cancel_with_config() -> dict:
        cm = load_config(config_path=effective_config, profile=effective_profile)
        return asyncio.run(cancel_operation(operation_id, config=cm, cli_api_keys=cli_api_keys))

    if as_json:
        from doxa_research.json_output import emit_error, emit_json, run_json_doxa_boundary

        result = run_json_doxa_boundary(_cancel_with_config)

        if result["status"] == "not_found":
            emit_error(
                "OPERATION_NOT_FOUND",
                f"Operation {operation_id} not found",
                {"operation_id": operation_id},
                exit_code=6,
            )
        emit_json(result)

    result = _cancel_with_config()

    # Rich rendering
    if result["status"] == "not_found":
        click.echo(f"Error: operation {operation_id} not found", err=True)
        sys.exit(6)

    if result["status"] == "already_terminal":
        if not effective_quiet:
            click.echo(
                f"Operation {operation_id} is already {result['previous']!r}; nothing to cancel."
            )
        sys.exit(0)

    if not effective_quiet:
        click.echo(f"Cancelling operation {operation_id}...")
        for name, provider_result in (result.get("providers") or {}).items():
            status = provider_result.get("status", "?")
            if status == "cancelled":
                click.echo(f"  ✓ {name}: cancelled upstream")
            elif status == "cancelling":
                click.echo(f"  - {name}: cancellation requested upstream; confirmation is pending")
            elif status == "completed":
                click.echo(f"  ✓ {name}: completed before cancel landed")
            elif status == "upstream_unsupported":
                click.echo(
                    f"  ⚠ {name}: upstream cancel not supported; local checkpoint marked cancelled"
                )
            elif status == "skipped":
                click.echo(f"  - {name}: already {provider_result.get('reason', 'terminal')}")
            else:
                click.echo(f"  ✗ {name}: {status} ({provider_result.get('error', '')})")
        click.echo(f"Operation {operation_id} marked cancelled.")
    sys.exit(0)


__all__ = ["cancel"]
