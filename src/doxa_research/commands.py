"""Command implementations for the Doxa Research CLI.

`CommandHandler` is the dispatch facade that ``cli.py`` and the interactive
mode both use. The async functions ``show_status``, ``list_operations``, and
``providers_command`` are the long-form implementations the dispatcher calls
into. Thin sync wrappers ``status_command`` / ``list_command`` exist for
back-compat with older callers.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import tomlkit
import tomlkit.items
from rich import box
from rich.console import Console
from rich.table import Table

from doxa_research.checkpoint import CheckpointManager
from doxa_research.config import ConfigManager, get_config
from doxa_research.errors import APIKeyError, DoxaError
from doxa_research.hints import print_hint
from doxa_research.models import ModelCache, OperationStatus
from doxa_research.paths import user_config_file
from doxa_research.providers import create_provider, resolve_api_key
from doxa_research.providers.openai import _is_retired
from doxa_research.run import run_research

console = Console()


def _populate_table(table: Any, data: dict[str, Any]) -> None:
    """Recursively populate a tomlkit table from a nested dict.

    Dict values become sub-tables; all other values become leaf entries.
    """
    for key, value in data.items():
        if isinstance(value, dict):
            sub = tomlkit.table()
            _populate_table(sub, value)
            table[key] = sub
        else:
            table[key] = value


def _build_profile_section(body: dict[str, Any]) -> tomlkit.items.Table:
    """Build a `[profiles.<name>]` table from a nested-dict body.

    `body` is a nested mapping that mirrors `ProfileConfig`. Each dict value
    becomes a TOML sub-table; non-dict values become leaf entries.
    """
    profile_table = tomlkit.table()
    _populate_table(profile_table, body)
    return profile_table


def _build_starter_document() -> tomlkit.TOMLDocument:
    """Read the shipped starter template from package data.

    Source of truth is `src/doxa_research/data/starter.config.toml`.
    See projects/P40-on-disk-starter-template.md.
    """
    from importlib.resources import files

    resource = files("doxa_research.data") / "starter.config.toml"
    text = resource.read_text(encoding="utf-8")
    return tomlkit.parse(text)


def _apply_wizard_answers(doc: tomlkit.TOMLDocument, answers) -> None:
    """Merge `WizardAnswers` into a tomlkit document in place.

    Touches only `[general].default_mode` and `[providers.<name>].api_key`.
    All other sections are preserved verbatim. Missing tables are created.
    """
    from doxa_research.init_wizard import ENV_VAR_BY_PROVIDER

    # general.default_mode
    general = doc.get("general")
    if general is None or not hasattr(general, "keys"):
        general = tomlkit.table()
        doc["general"] = general
    general["default_mode"] = answers.default_mode

    # providers.<name>.api_key
    providers = doc.get("providers")
    if providers is None or not hasattr(providers, "keys"):
        providers = tomlkit.table()
        doc["providers"] = providers
    for choice in answers.providers:
        if choice.storage == "skip":
            continue
        prov_table = providers.get(choice.name)
        if prov_table is None or not hasattr(prov_table, "keys"):
            prov_table = tomlkit.table()
            providers[choice.name] = prov_table
        if choice.storage == "env_ref":
            var = ENV_VAR_BY_PROVIDER[choice.name]
            prov_table["api_key"] = f"${{{var}}}"
        else:  # literal
            prov_table["api_key"] = choice.literal_value or ""


def _prefill_from_doc(doc: tomlkit.TOMLDocument):
    """Extract wizard-relevant fields from an existing tomlkit doc."""
    from doxa_research.init_wizard import (
        DEFAULT_MODE_OPTIONS,
        _Prefill,
    )

    general = doc.get("general") or {}
    raw_mode = general.get("default_mode") if hasattr(general, "get") else None
    mode = raw_mode if raw_mode in DEFAULT_MODE_OPTIONS else None

    # Provider pre-fill is intentionally empty: api_key strings can't be
    # round-tripped without exposing user secrets in prompts. The wizard
    # re-asks each picked provider's key from scratch on `--force`.
    return _Prefill(providers=(), default_mode=mode)


def _load_or_build_doc(target: Path, *, force: bool) -> tomlkit.TOMLDocument:
    """Return the doc to merge wizard answers into.

    Existing file + force → parse it (preserves unknown sections).
    Anything else → fresh starter doc.
    """
    if force and target.exists():
        try:
            return tomlkit.parse(target.read_text())
        except Exception as exc:  # tomlkit raises a variety of errors
            raise DoxaError(
                f"Cannot parse existing config at {target}: {exc}. "
                "Pass --non-interactive to overwrite with defaults, "
                "or fix the file."
            ) from exc
    return _build_starter_document()


class CommandHandler:
    """Unified command execution for CLI and interactive modes"""

    def __init__(self, config_manager: ConfigManager):
        self.config = config_manager
        self.commands = {
            "init": self.init_command,
            "status": self.status_command,
            "list": self.list_command,
            "providers": self.providers_command,
            "research": self.research_command,
            "config": self.config_command,
        }

    def execute(self, command: str, **params) -> Any:
        """Execute command with parameters"""
        if command not in self.commands:
            raise DoxaError(
                f"Unknown command: {command}",
                f"Available commands: {', '.join(self.commands.keys())}",
            )
        return self.commands[command](**params)

    def init_command(
        self,
        config_path: str | Path | None = None,
        *,
        user: bool = False,
        hidden: bool = False,
        force: bool = False,
        non_interactive: bool = False,
        **params,
    ):
        """Initialize Doxa Research configuration"""
        if user and hidden:
            raise DoxaError(
                "doxa init: --user and --hidden are mutually exclusive",
            )

        target = self._resolve_init_target(config_path, user=user, hidden=hidden)
        if target.exists() and not force:
            raise DoxaError(
                f"doxa init: refusing to overwrite existing {target}. Pass --force to overwrite.",
            )

        console.print("[bold]Welcome to Doxa Research Research Assistant Setup![/bold]\n")
        console.print("Checking environment...")
        console.print(f"✓ Python {sys.version.split()[0]} detected")
        console.print("✓ UV package manager available")
        console.print(f"✓ Operating System: {sys.platform} (supported)\n")
        console.print(f"Configuration file will be created at: {target}\n")

        if non_interactive:
            doc = _build_starter_document()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(tomlkit.dumps(doc))
            console.print(f"\n[green]✓[/green] Configuration saved to {target}")
            console.print('\nYou can now run: doxa deep_research "your prompt"')
            return

        # Interactive wizard path
        import os

        from doxa_research import init_wizard

        base_doc = _load_or_build_doc(target, force=force)
        prefill = _prefill_from_doc(base_doc) if force and target.exists() else None

        def _real_prompt(p: str) -> str:
            from rich.prompt import Prompt
            from rich.text import Text

            return Prompt.ask(Text(p, style="prompt"), default="", show_default=False)

        answers = init_wizard.run(
            target=target,
            prefill=prefill,
            prompt_fn=_real_prompt,
            env=dict(os.environ),
        )
        if answers is None:
            console.print("[yellow]Init cancelled — no file written.[/yellow]")
            return

        _apply_wizard_answers(base_doc, answers)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(tomlkit.dumps(base_doc))
        console.print(f"\n[green]✓[/green] Configuration saved to {target}")
        console.print('\nYou can now run: doxa deep_research "your prompt"')

    def _resolve_init_target(
        self,
        config_path: str | Path | None,
        *,
        user: bool,
        hidden: bool,
    ) -> Path:
        if config_path is not None:
            return Path(config_path).expanduser().resolve()
        if hidden:
            return Path("./.doxa.config.toml").resolve()
        # Default (and `--user`): user-tier XDG config. Project-tier writes
        # are driven by the CLI leaf pre-resolving `./doxa.config.toml`
        # before calling into init_command, so direct API callers keep the
        # legacy "no args → user config" semantics.
        return user_config_file()

    def status_command(self, operation_id: str, **params):
        """Check status of a research operation"""
        return asyncio.run(show_status(operation_id, config=self.config))

    def list_command(self, show_all: bool = False, **params):
        """List research operations"""
        return asyncio.run(list_operations(show_all=show_all, config=self.config))

    def providers_command(self, **params):
        """Show provider information - delegate to existing function"""
        return asyncio.run(providers_command(**params))

    def research_command(self, **params):
        """Execute research operation - delegate to run_research"""
        mode = params.get("mode", "default")
        prompt = params.get("prompt")
        if not prompt:
            raise DoxaError("Prompt is required for research command")

        return asyncio.run(
            run_research(
                mode=mode,
                prompt=prompt,
                async_mode=params.get("async_mode", False),
                project=params.get("project"),
                output_dir=params.get("output_dir"),
                provider=params.get("provider"),
                input_file=params.get("input_file"),
                auto=params.get("auto", False),
                verbose=params.get("verbose", False),
                cli_api_keys=params.get("cli_api_keys"),
                combined=params.get("combined", False),
                quiet=params.get("quiet", False),
                no_metadata=params.get("no_metadata", False),
                timeout_override=params.get("timeout_override"),
                profile=params.get("profile"),
            )
        )

    def config_command(self, op: str | None = None, rest: list[str] | None = None, **params) -> int:
        """Dispatch to the doxa_research.config_cmd.config_command op handler."""
        from doxa_research.config_cmd import config_command as _cfg

        if op is None:
            raise DoxaError(
                "config requires an op",
                "Run `doxa config help` for usage",
            )
        return _cfg(op, rest or [])


def get_init_data(
    *,
    non_interactive: bool,
    config_path: str | None,
    user: bool = False,
    hidden: bool = False,
    force: bool = False,
) -> dict:
    """Pure data function for `doxa init`.

    Returns a dict describing the init outcome (config path, whether the
    file was newly created, and the version). Does NOT print Rich output.
    The legacy `init_command` continues to handle the human-readable path.
    """
    from doxa_research.config import DOXA_VERSION

    if user and hidden:
        raise DoxaError(
            "doxa init: --user and --hidden are mutually exclusive",
        )

    if config_path is not None:
        target = Path(config_path).expanduser().resolve()
    elif hidden:
        target = Path("./.doxa.config.toml").resolve()
    else:
        # Default (and `--user`): user-tier XDG config. Project-tier writes
        # are driven by the CLI leaf pre-resolving `./doxa.config.toml`
        # before calling into get_init_data.
        target = user_config_file()

    existed = target.exists()
    if existed and not force:
        raise DoxaError(
            f"doxa init: refusing to overwrite existing {target}. Pass --force to overwrite.",
        )
    created = not existed
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(tomlkit.dumps(_build_starter_document()))
    return {
        "config_path": str(target),
        "created": created,
        "doxa_version": DOXA_VERSION,
        "non_interactive": non_interactive,
    }


def status_command(operation_id):
    """Check status of a research operation"""
    return show_status(operation_id)


def list_command(show_all):
    """List research operations"""
    return list_operations(show_all=show_all)


async def get_status_data(operation_id: str, config: ConfigManager | None = None) -> dict | None:
    """Pure data function for `doxa status OP_ID`.

    Returns a dict describing the operation, or None if not found.
    Per spec §7.2, this function NEVER takes an `as_json` flag — the
    JSON-vs-Rich choice lives at the subcommand-wrapper layer.
    """
    resolved_config = config if config is not None else get_config()
    checkpoint_manager = CheckpointManager(resolved_config)

    operation = await checkpoint_manager.load(operation_id)
    if operation is None:
        return None

    return {
        "operation_id": operation.id,
        "prompt": operation.prompt,
        "mode": operation.mode,
        "status": operation.status,
        "created_at": operation.created_at.isoformat(),
        "updated_at": operation.updated_at.isoformat(),
        "project": operation.project,
        "providers": dict(operation.providers),
        "output_paths": {k: str(v) for k, v in operation.output_paths.items()},
        "failure_type": getattr(operation, "failure_type", None),
        "error": operation.error,
    }


async def show_status(operation_id: str, config: ConfigManager | None = None):
    """Show status of a specific operation (Rich rendering)."""
    resolved_config = config if config is not None else get_config()
    data = await get_status_data(operation_id, config=resolved_config)
    if data is None:
        console.print(f"[red]Error:[/red] Operation {operation_id} not found")
        sys.exit(6)

    # Re-load for the existing Rich rendering helpers (_print_status_hints).
    checkpoint_manager = CheckpointManager(resolved_config)
    operation = await checkpoint_manager.load(operation_id)  # already known to exist
    assert operation is not None  # data was non-None above

    console.print("\nOperation Details:")
    console.print("─────────────────")
    console.print(f"ID:        {data['operation_id']}")
    console.print(f"Prompt:    {data['prompt']}")
    console.print(f"Mode:      {data['mode']}")
    console.print(f"Status:    {data['status']}")
    console.print(f"Started:   {operation.created_at.strftime('%Y-%m-%d %H:%M:%S')}")

    if data["status"] in ["running", "completed"]:
        elapsed = datetime.now() - operation.created_at
        minutes = int(elapsed.total_seconds() / 60)
        console.print(f"Elapsed:   {minutes} minutes")

    if data["project"]:
        console.print(f"Project:   {data['project']}")

    if data["providers"]:
        console.print("\nProvider Status:")
        console.print("───────────────")
        for provider_name, provider_info in data["providers"].items():
            status_icon = "✓" if provider_info.get("status") == "completed" else "▶"
            status_text = provider_info.get("status", "unknown").title()
            console.print(f"{provider_name.title()}:  {status_icon} {status_text}")

    if data["output_paths"]:
        console.print("\nOutput Files:")
        console.print("────────────")
        if data["project"]:
            base_dir = Path(resolved_config.data["paths"]["base_output_dir"]) / data["project"]
            console.print(f"{base_dir}/")
        else:
            console.print("./")

        for _provider_name, path in data["output_paths"].items():
            console.print(f"  ├── {Path(path).name}")

    _print_status_hints(operation)


def _print_status_hints(operation: OperationStatus) -> None:
    console.print("\nNext steps:")
    status = operation.status
    op_id = operation.id
    if status == "queued":
        print_hint(f"doxa status {op_id}", "Re-check (should transition to running shortly)")
    elif status == "running":
        print_hint(f"doxa status {op_id}", "Re-check progress")
    elif status == "completed":
        print_hint("doxa list", "See recent runs")
    elif status == "cancelled":
        print_hint(f"doxa resume {op_id}", "Pick up where Ctrl-C left off")
    elif status == "failed":
        if operation.failure_type == "permanent":
            console.print("  This failure is permanent and cannot be resumed.")
            first = next(iter(operation.providers), None)
            if first is not None:
                print_hint(
                    f"doxa config get providers.{first}.api_key --show-secrets",
                    "Check credentials",
                )
        else:
            print_hint(f"doxa resume {op_id}", "Retry from checkpoint")


async def get_list_data(show_all: bool, config: ConfigManager | None = None) -> dict:
    """Pure data function for `doxa list`.

    Returns a dict with `count` and `operations` (list of dicts). Per spec
    §7.2, this function NEVER takes an `as_json` flag — the JSON-vs-Rich
    choice lives at the subcommand-wrapper layer.
    """
    resolved_config = config if config is not None else get_config()
    checkpoint_manager = CheckpointManager(resolved_config)

    checkpoint_files = list(checkpoint_manager.checkpoint_dir.glob("*.json"))
    operations = []
    for checkpoint_file in checkpoint_files:
        operation_id = checkpoint_file.stem
        operation = await checkpoint_manager.load(operation_id)
        if operation:
            operations.append(operation)

    operations.sort(key=lambda op: op.created_at, reverse=True)

    if not show_all:
        cutoff_time = datetime.now() - timedelta(hours=24)
        operations = [
            op
            for op in operations
            if op.status in ["running", "queued"] or op.created_at > cutoff_time
        ]

    return {
        "count": len(operations),
        "operations": [
            {
                "operation_id": op.id,
                "prompt": op.prompt,
                "mode": op.mode,
                "status": op.status,
                "created_at": op.created_at.isoformat(),
                "project": op.project,
            }
            for op in operations
        ],
    }


async def list_operations(show_all: bool, config: ConfigManager | None = None):
    """List all operations"""
    resolved_config = config if config is not None else get_config()
    checkpoint_manager = CheckpointManager(resolved_config)

    checkpoint_files = list(checkpoint_manager.checkpoint_dir.glob("*.json"))

    if not checkpoint_files:
        console.print("No operations found.")
        return

    operations = []
    for checkpoint_file in checkpoint_files:
        operation_id = checkpoint_file.stem
        operation = await checkpoint_manager.load(operation_id)
        if operation:
            operations.append(operation)

    operations.sort(key=lambda op: op.created_at, reverse=True)

    if not show_all:
        cutoff_time = datetime.now() - timedelta(hours=24)
        operations = [
            op
            for op in operations
            if op.status in ["running", "queued"] or op.created_at > cutoff_time
        ]

    if not operations:
        console.print("No active operations found. Use --all to see all operations.")
        return

    table = Table(title="Research Operations")
    table.add_column("ID", style="dim", width=40)
    table.add_column("Prompt", width=25)
    table.add_column("Status", width=10)
    table.add_column("Elapsed", width=8)
    table.add_column("Mode", width=15)

    for operation in operations:
        prompt_display = (
            operation.prompt[:22] + "..." if len(operation.prompt) > 25 else operation.prompt
        )

        elapsed = datetime.now() - operation.created_at
        if elapsed.total_seconds() < 3600:
            elapsed_str = f"{int(elapsed.total_seconds() / 60)}m"
        else:
            elapsed_str = f"{int(elapsed.total_seconds() / 3600)}h"

        status_style = (
            "green"
            if operation.status == "completed"
            else "yellow"
            if operation.status == "running"
            else "dim"
        )

        table.add_row(
            operation.id,
            prompt_display,
            f"[{status_style}]{operation.status}[/{status_style}]",
            elapsed_str,
            operation.mode,
        )

    console.print(table)
    console.print("\nUse 'doxa status <ID>' for details")


async def providers_command(
    show_models: bool = False,
    show_list: bool = False,
    show_keys: bool = False,
    filter_provider: str | None = None,
    refresh_cache: bool = False,
    no_cache: bool = False,
    cli_api_keys: dict[str, str | None] | None = None,
    timeout_override: float | None = None,
    profile: str | None = None,
):
    """Show provider information and available models"""
    if not show_models and not show_list and not show_keys:
        console.print("[yellow]Usage:[/yellow] doxa providers <list|models|check> [OPTIONS]")
        console.print("\nShow provider information and available models.")
        console.print("\nSubcommands:")
        console.print("  list                  List available providers and their status")
        console.print("  models                List available models from providers")
        console.print("  check                 Show API key configuration for each provider")
        console.print("\nOptions (for `models`):")
        console.print("  --provider, -P        Filter by specific provider")
        console.print("  --refresh-cache       Force refresh of cached model lists")
        console.print("  --no-cache            Bypass cache without updating it")
        console.print("\nExamples:")
        console.print("  # List all available providers")
        console.print("  $ doxa providers list")
        console.print("\n  # Show API key configuration")
        console.print("  $ doxa providers check")
        console.print("\n  # List all models from all providers")
        console.print("  $ doxa providers models")
        console.print("\n  # List only OpenAI models")
        console.print("  $ doxa providers models --provider openai")
        console.print("\n  # Force refresh cached models")
        console.print("  $ doxa providers models --refresh-cache")
        console.print("\n  # Check current models without updating cache")
        console.print("  $ doxa providers models --no-cache")
        return

    config = get_config(profile=profile)
    cli_api_keys = cli_api_keys or {}

    all_providers = ["openai", "perplexity", "gemini", "mock"]
    selected_providers = all_providers
    if filter_provider:
        if filter_provider not in all_providers:
            print(f"Error: Unknown provider: {filter_provider}", file=sys.stderr)
            print(f"Available providers: {', '.join(all_providers)}", file=sys.stderr)
            sys.exit(1)
        selected_providers = [filter_provider]

    provider_descriptions = {
        "openai": "OpenAI GPT models",
        "perplexity": "Perplexity Sonar (web-grounded synchronous search)",
        "gemini": "Gemini 2.5 (web-grounded synchronous search with optional thinking)",
        "mock": "Mock provider for tests",
    }

    if show_list:
        table = Table(title="Available Providers", box=box.ROUNDED)
        table.add_column("Provider", style="cyan", width=13)
        table.add_column("Status", width=10)
        table.add_column("Description", style="dim", width=25)
        table.add_column("Model Cache", style="dim", width=30)

        for provider_name in selected_providers:
            try:
                provider = create_provider(
                    provider_name,
                    config,
                    cli_api_key=cli_api_keys.get(provider_name),
                )
                status = "[green]✓ Ready[/green]"

                cache_info = "N/A"
                if not provider.is_implemented():
                    status = (
                        f"[yellow]⚠ {provider.implementation_status() or 'Unavailable'}[/yellow]"
                    )
                elif provider_name == "openai":
                    cache = ModelCache(provider_name)
                    age = cache.get_cache_age()
                    if age:
                        days = age.days
                        if days == 0:
                            cache_info = "Cached today"
                        elif days == 1:
                            cache_info = "1 day old"
                        else:
                            refresh_in = 7 - days
                            if refresh_in > 0:
                                cache_info = f"{days} days old (refresh in {refresh_in} days)"
                            else:
                                cache_info = f"{days} days old (needs refresh)"
                    else:
                        cache_info = "Not cached"

            except APIKeyError:
                status = "[red]✗ No key[/red]"
                cache_info = "N/A"
            except Exception:
                status = "[yellow]⚠ Error[/yellow]"
                cache_info = "N/A"

            description = provider_descriptions.get(provider_name, "Unknown provider")
            table.add_row(provider_name, status, description, cache_info)

        console.print(table)
        console.print("\nTo see available models, use: doxa providers models")
        console.print("To refresh model cache, use: doxa providers models --refresh-cache")
        return

    if show_keys:
        env_vars = {
            "openai": "OPENAI_API_KEY",
            "perplexity": "PERPLEXITY_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "mock": "MOCK_API_KEY",
        }

        table = Table(title="Provider API Key Configuration", box=box.ROUNDED)
        table.add_column("Provider", style="cyan", width=13)
        table.add_column("Environment Variable", style="green", width=22)
        table.add_column("CLI Argument", style="yellow", width=22)
        table.add_column("Status", width=12)

        missing: list[str] = []
        for provider_name in selected_providers:
            env_var = env_vars.get(provider_name, f"{provider_name.upper()}_API_KEY")
            cli_arg = f"--api-key-{provider_name}"
            provider_config = config.data["providers"].get(provider_name, {})
            try:
                resolved = resolve_api_key(
                    provider_name,
                    cli_api_keys.get(provider_name),
                    provider_config,
                    env_var_name=env_var,
                )
                if provider_name == "mock" and resolved == "invalid":
                    raise DoxaError(
                        "Invalid mock API key format",
                        "Mock API key should not be 'invalid'",
                    )
                status = "[green]set[/green]"
            except DoxaError:
                missing.append(provider_name)
                status = "[red]missing[/red]"
            table.add_row(provider_name, env_var, cli_arg, status)

        console.print(table)
        if missing:
            console.print(f"\n[red]Missing keys for:[/red] {', '.join(missing)}")
            return 2
        console.print("\n[green]All selected providers have keys set[/green]")
        return 0

    providers = selected_providers

    if refresh_cache:
        console.print("Fetching available models (refreshing cache)...\n")
    else:
        console.print("Fetching available models...\n")

    for provider_name in providers:
        try:
            provider = create_provider(
                provider_name,
                config,
                cli_api_key=cli_api_keys.get(provider_name),
                timeout_override=timeout_override,
            )

            if hasattr(provider, "list_models_cached"):
                models = await provider.list_models_cached(
                    force_refresh=refresh_cache, no_cache=no_cache
                )
            else:
                models = await provider.list_models()

            if models:
                max_model_id_length = max(len(model.get("id", "")) for model in models)
                model_id_width = max(max_model_id_length + 2, 20)
            else:
                model_id_width = 25

            if provider_name == "openai":
                table = Table(title="OpenAI Models", box=box.ROUNDED)
                table.add_column("Model ID", style="cyan", width=model_id_width)
                table.add_column("Created", style="green", width=14)
                table.add_column("Owned By", style="yellow", width=16)
                table.add_column("Status", style="red", width=22)

                for model in models:
                    created_date = datetime.fromtimestamp(model["created"]).strftime("%Y-%m-%d")
                    # `/v1/models` keeps listing retired models, so an ID
                    # appearing here does not mean it is callable. Say so:
                    # the o3/o4-mini deep-research shutdown was invisible
                    # precisely because the listing looked normal.
                    #
                    # Derived from the date at render time, never from the
                    # cached `type`: list_models_cached can serve a week-old
                    # entry, so a model that retired during that week would
                    # still be labelled active.
                    shutdown = model.get("shutdown_date")
                    if shutdown and _is_retired(shutdown):
                        status = f"retired {shutdown}"
                    elif shutdown:
                        status = f"retires {shutdown}"
                    else:
                        status = ""
                    table.add_row(model["id"], created_date, model["owned_by"], status)
            elif provider_name == "perplexity":
                table = Table(title="Perplexity Models", box=box.ROUNDED)
                table.add_column("Model ID", style="cyan", width=model_id_width)

                for model in models:
                    table.add_row(model["id"])
            elif provider_name == "gemini":
                table = Table(title="Gemini Models", box=box.ROUNDED)
                table.add_column("Model ID", style="cyan", width=model_id_width)

                for model in models:
                    table.add_row(model["id"])
            else:
                table = Table(title="Mock Models", box=box.ROUNDED)
                table.add_column("Model ID", style="cyan", width=model_id_width)

                for model in models:
                    table.add_row(model["id"])

            console.print(table)
            console.print()

        except APIKeyError as e:
            console.print(f"[yellow]Skipping {provider_name}:[/yellow] {e}")
            console.print()
        except Exception as e:
            console.print(f"[red]Error fetching models from {provider_name}:[/red] {str(e)}")
            console.print()


def get_providers_list_data(config: ConfigManager, filter_provider: str | None = None) -> dict:
    """Pure data function for `doxa providers list`.

    Returns:
        {"providers": [{"name": str, "key_set": bool}, ...]} on success.
        {"providers": [], "filter_provider": ..., "unknown": True} when
        `filter_provider` is unknown.
    """
    import os
    import re

    providers = sorted(config.data["providers"].keys())
    if filter_provider and filter_provider not in providers:
        return {"providers": [], "filter_provider": filter_provider, "unknown": True}
    if filter_provider:
        providers = [filter_provider]

    out = []
    for name in providers:
        raw = config.data["providers"][name].get("api_key", "")
        m = re.match(r"\$\{(\w+)\}", raw or "")
        resolved = os.environ.get(m.group(1)) if m else (raw or None)
        out.append({"name": name, "key_set": bool(resolved)})
    return {"providers": out}


def get_providers_models_data(config: ConfigManager, filter_provider: str | None = None) -> dict:
    """Pure data function for `doxa providers models`."""
    from doxa_research.config import BUILTIN_MODES

    seen: dict[str, set[str]] = {}
    for cfg in BUILTIN_MODES.values():
        # P18: alias stubs (`_deprecated_alias_for`) don't carry provider/model.
        if "_deprecated_alias_for" in cfg:
            continue
        # Multi-provider fan-out modes (e.g. all_deep_research) declare
        # `providers` (plural) + per-provider namespace models; iterate
        # those namespaces instead of expecting singular fields.
        providers_list = cfg.get("providers")
        if cfg.get("provider") is None and isinstance(providers_list, list):
            for p_raw in providers_list:
                if not isinstance(p_raw, str):
                    continue
                if filter_provider and p_raw != filter_provider:
                    continue
                namespace = cfg.get(p_raw)
                if not isinstance(namespace, dict):
                    continue
                model = namespace.get("model")
                if isinstance(model, str):
                    seen.setdefault(p_raw, set()).add(model)
            continue
        p = str(cfg["provider"])
        if filter_provider and p != filter_provider:
            continue
        seen.setdefault(p, set()).add(str(cfg["model"]))

    return {
        "providers": [
            {"name": provider, "models": sorted(models)}
            for provider, models in sorted(seen.items())
        ],
        "filter_provider": filter_provider,
    }


def get_providers_check_data(config: ConfigManager) -> dict:
    """Pure data function for `doxa providers check`."""
    import os
    import re

    missing = []
    for name, p in config.data["providers"].items():
        raw = p.get("api_key", "")
        m = re.match(r"\$\{(\w+)\}", raw or "")
        resolved = os.environ.get(m.group(1)) if m else (raw or None)
        if not resolved:
            missing.append(name)
    return {"missing": missing, "complete": not missing}


def providers_list(config: ConfigManager, filter_provider: str | None = None) -> int:
    """List configured providers and whether each has a usable key (Rich)."""
    from rich.console import Console as _Console

    data = get_providers_list_data(config, filter_provider=filter_provider)
    if data.get("unknown"):
        all_providers = sorted(config.data["providers"].keys())
        print(f"Error: Unknown provider: {filter_provider}", file=sys.stderr)
        print(f"Available providers: {', '.join(all_providers)}", file=sys.stderr)
        return 1

    _console = _Console()
    _console.print("Configured providers:")
    for entry in data["providers"]:
        label = "key set" if entry["key_set"] else "no key"
        _console.print(f"  {entry['name']:<12} {label}")
    return 0


def providers_models(config: ConfigManager, filter_provider: str | None = None) -> int:
    """List models known per provider, derived from BUILTIN_MODES (Rich)."""
    from rich.console import Console as _Console

    data = get_providers_models_data(config, filter_provider=filter_provider)
    _console = _Console()
    if filter_provider and not data["providers"]:
        _console.print(f"[red]No models found for provider:[/] {filter_provider}")
        return 1
    for entry in data["providers"]:
        _console.print(f"{entry['name']}:")
        for m in entry["models"]:
            _console.print(f"  {m}")
    return 0


def providers_check(config: ConfigManager) -> int:
    """Exit 0 if every configured provider has a usable key; else 2 (Rich)."""
    from rich.console import Console as _Console

    data = get_providers_check_data(config)
    _console = _Console()
    if not data["complete"]:
        _console.print(f"[red]Missing keys for:[/] {', '.join(data['missing'])}")
        return 2
    _console.print("[green]All providers have keys set[/]")
    return 0


async def cancel_operation(
    op_id: str,
    *,
    config: ConfigManager | None = None,
    cli_api_keys: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """P18 Phase G: cancel a previously-started operation.

    Loads the operation from checkpoint, calls `provider.cancel(job_id)` for
    each non-completed provider on the operation, updates the checkpoint to
    `cancelled`, and returns a structured result for the caller to render.

    Returns one of:
      * {"status": "ok",  "operation_id": ..., "providers": {...}}
      * {"status": "not_found", "operation_id": ...}              — exit 6
      * {"status": "already_terminal", "operation_id": ..., "previous": "completed"|"cancelled"|...}

    Catches `NotImplementedError` from providers without upstream cancel
    support; reports best-effort behavior in `providers[name]` as
    "upstream cancel not supported". The local checkpoint is marked
    cancelled regardless.
    """
    cm = config if config is not None else get_config()
    cpm = CheckpointManager(cm)
    op = await cpm.load(op_id)
    if op is None:
        return {"status": "not_found", "operation_id": op_id}

    if op.status in ("completed", "cancelled", "failed"):
        return {
            "status": "already_terminal",
            "operation_id": op_id,
            "previous": op.status,
        }

    # Local import to avoid CLI ↔ providers circular at module load.
    from doxa_research.providers import create_provider

    per_provider: dict[str, dict[str, Any]] = {}
    for provider_name, pdata in op.providers.items():
        if pdata.get("status") in ("completed", "cancelled", "failed"):
            per_provider[provider_name] = {"status": "skipped", "reason": pdata.get("status")}
            continue
        job_id = pdata.get("job_id")
        if not job_id:
            per_provider[provider_name] = {"status": "no_job_id"}
            continue
        try:
            mode_config = cm.get_mode_config(op.mode)
            provider_instance = create_provider(
                provider_name,
                cm,
                cli_api_key=(cli_api_keys or {}).get(provider_name) if cli_api_keys else None,
                mode_config=mode_config,
            )
            cancel_result = await provider_instance.cancel(job_id)
            per_provider[provider_name] = cancel_result
            op.providers[provider_name]["status"] = "cancelled"
        except NotImplementedError as e:
            per_provider[provider_name] = {
                "status": "upstream_unsupported",
                "error": str(e),
            }
            op.providers[provider_name]["status"] = "cancelled"
        except Exception as e:  # noqa: BLE001 — best-effort, swallow and report
            per_provider[provider_name] = {
                "status": "permanent_error",
                "error": str(e),
                "error_class": type(e).__name__,
            }
            op.providers[provider_name]["status"] = "cancelled"

    op.transition_to("cancelled")
    await cpm.save(op)

    return {
        "status": "ok",
        "operation_id": op_id,
        "providers": per_provider,
    }


__all__ = [
    "CommandHandler",
    "cancel_operation",
    "get_init_data",
    "get_list_data",
    "get_providers_check_data",
    "get_providers_list_data",
    "get_providers_models_data",
    "get_status_data",
    "list_command",
    "list_operations",
    "providers_check",
    "providers_command",
    "providers_list",
    "providers_models",
    "show_status",
    "status_command",
]
