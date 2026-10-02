"""Interactive prompt interface with slash commands and prompt_toolkit UI.

Houses the interactive REPL components: slash-command registry and
completer, clarification-session state, the full-screen Application,
and the async entry point ``enter_interactive_mode``. prompt_toolkit
is imported optionally so doxa_research still loads on systems where it is
missing — the basic input fallback keeps working without it.
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx2
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI
from rich.console import Console
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from doxa_research.commands import show_status
from doxa_research.config import BUILTIN_MODES, ConfigManager, get_config
from doxa_research.models import InputMode, InteractiveInitialSettings
from doxa_research.run import run_research

console = Console()


try:
    from prompt_toolkit import Application
    from prompt_toolkit.application import run_in_terminal
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.document import Document
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import Frame, Label, TextArea

    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False


class SlashCommandRegistry:
    """Registry and handler for slash commands"""

    def __init__(self, console: Console):
        self.console = console
        self.commands = {
            "/help": self.show_help,
            "/mode": self.set_mode,
            "/provider": self.set_provider,
            "/async": self.toggle_async,
            "/status": self.check_status,
            "/exit": self.exit_interactive,
            "/quit": self.exit_interactive,
            "/multiline": self.toggle_multiline,
        }
        self.current_mode = "default"
        self.current_provider = None
        self.async_mode = False
        self.multiline_mode = True
        self.last_operation_id = None

    def parse_and_execute(self, input_text: str) -> str:
        """Parse and execute slash command, return action to take"""
        if not input_text.startswith("/"):
            return "continue"

        parts = input_text.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if command in self.commands:
            return self.commands[command](args)
        else:
            self.console.print(f"[red]Unknown command:[/red] {command}")
            self.console.print("Type /help for available commands")
            return "continue"

    def show_help(self, args: str) -> str:
        """Show available commands"""
        self.console.print("[cyan]Available commands:[/cyan]")
        self.console.print("  /help              - Show this help")
        self.console.print("  /mode <mode>       - Change research mode")
        self.console.print("  /provider <name>   - Set provider (openai, perplexity, gemini, mock)")
        self.console.print("  /async             - Toggle async mode")
        self.console.print("  /multiline         - Toggle multiline input mode")
        self.console.print("  /status            - Check last operation status")
        self.console.print("  /exit, /quit       - Exit interactive mode")
        self.console.print()
        self.console.print("[cyan]Unix shortcuts (always available):[/cyan]")
        self.console.print("  Ctrl+A             - Move to start of line")
        self.console.print("  Ctrl+E             - Move to end of line")
        self.console.print("  Ctrl+K             - Delete to end of line")
        self.console.print("  Ctrl+U             - Delete to start of line")
        self.console.print("  Ctrl+W             - Delete word backward")
        self.console.print()
        self.console.print("[dim]Current settings:[/dim]")
        self.console.print(f"  Mode: {self.current_mode}")
        self.console.print(f"  Provider: {self.current_provider or 'auto'}")
        self.console.print(f"  Async: {self.async_mode}")
        self.console.print(f"  Multiline: {self.multiline_mode}")
        self.console.print()
        return "continue"

    def set_mode(self, args: str) -> str:
        """Set research mode"""
        from doxa_research.modes_cmd import list_all_modes

        if not args:
            self.console.print("[cyan]Available modes:[/cyan]")
            cm = get_config()
            infos = list_all_modes(cm)
            for i, info in enumerate(infos, 1):
                desc = (info.description or "")[:60]
                current = " [green]← current[/green]" if info.name == self.current_mode else ""
                self.console.print(f"  {i}. {info.name:<15} [{info.kind:<10}] {desc}{current}")
            self.console.print("\n[dim]Usage: /mode <name> or /mode <number>[/dim]")
            self.console.print(f"[dim]Current mode: {self.current_mode}[/dim]")
        else:
            arg = args.strip()
            if arg.isdigit():
                modes = list(BUILTIN_MODES.keys())
                idx = int(arg) - 1
                if 0 <= idx < len(modes):
                    mode = modes[idx]
                    self.current_mode = mode
                    self.console.print(f"[green]Mode set to:[/green] {mode}")
                else:
                    self.console.print(f"[red]Invalid mode number:[/red] {arg}")
                    self.console.print(f"Please choose 1-{len(modes)}")
            else:
                mode = arg.lower()
                if mode in BUILTIN_MODES:
                    self.current_mode = mode
                    self.console.print(f"[green]Mode set to:[/green] {mode}")
                else:
                    self.console.print(f"[red]Unknown mode:[/red] {mode}")
                    self.console.print("Use /mode without arguments to see available modes")
        self.console.print()
        return "continue"

    def set_provider(self, args: str) -> str:
        """Set provider"""
        providers = ["openai", "perplexity", "gemini", "mock", "auto"]
        if not args:
            self.console.print("[cyan]Available providers:[/cyan]")
            for i, provider in enumerate(providers, 1):
                current = (
                    " [green]← current[/green]"
                    if provider == (self.current_provider or "auto")
                    else ""
                )
                desc = {
                    "openai": "OpenAI GPT models",
                    "perplexity": "Perplexity Sonar (web-grounded synchronous search)",
                    "gemini": "Gemini 2.5 (web-grounded synchronous search with optional thinking)",
                    "mock": "Mock provider for testing",
                    "auto": "Automatic provider selection",
                }.get(provider, "")
                self.console.print(f"  {i}. {provider:<12} - {desc}{current}")
            self.console.print("\n[dim]Usage: /provider <name> or /provider <number>[/dim]")
            self.console.print(f"[dim]Current provider: {self.current_provider or 'auto'}[/dim]")
        else:
            arg = args.strip()
            if arg.isdigit():
                idx = int(arg) - 1
                if 0 <= idx < len(providers):
                    provider = providers[idx]
                    self.current_provider = None if provider == "auto" else provider
                    self.console.print(f"[green]Provider set to:[/green] {provider}")
                else:
                    self.console.print(f"[red]Invalid provider number:[/red] {arg}")
                    self.console.print(f"Please choose 1-{len(providers)}")
            else:
                provider = arg.lower()
                if provider == "auto":
                    self.current_provider = None
                    self.console.print("[green]Provider set to:[/green] auto")
                elif provider in ["openai", "perplexity", "gemini", "mock"]:
                    self.current_provider = provider
                    self.console.print(f"[green]Provider set to:[/green] {provider}")
                else:
                    self.console.print(f"[red]Unknown provider:[/red] {provider}")
                    self.console.print("Valid providers: openai, perplexity, gemini, mock, auto")
        self.console.print()
        return "continue"

    def toggle_async(self, args: str) -> str:
        """Toggle async mode"""
        self.async_mode = not self.async_mode
        self.console.print(
            f"[green]Async mode:[/green] {'enabled' if self.async_mode else 'disabled'}"
        )
        self.console.print()
        return "continue"

    def check_status(self, args: str) -> str:
        """Check operation status"""
        if not self.last_operation_id:
            self.console.print("[yellow]No operations run in this session[/yellow]")
        else:
            self.console.print(f"[cyan]Last operation:[/cyan] {self.last_operation_id}")
            asyncio.run(show_status(self.last_operation_id))
        self.console.print()
        return "continue"

    def toggle_multiline(self, args: str) -> str:
        """Toggle multiline mode"""
        import platform

        self.multiline_mode = not self.multiline_mode
        if self.multiline_mode:
            newline_key = "Option+Return" if platform.system() == "Darwin" else "Alt+Enter"
            mode_text = f"enabled (Enter submits, {newline_key} for new line)"
        else:
            mode_text = "disabled (Enter submits immediately)"
        self.console.print(f"[green]Multiline mode:[/green] {mode_text}")
        self.console.print()
        return "continue"

    def exit_interactive(self, args: str) -> str:
        """Exit interactive mode"""
        return "exit"


if PROMPT_TOOLKIT_AVAILABLE:

    class SlashCommandCompleter(Completer):
        """Custom completer for slash commands with better partial matching"""

        def __init__(self, commands: list[str], meta_dict: dict[str, str] | None = None):
            self.commands = sorted(commands)
            self.meta_dict = meta_dict or {}

        def get_completions(self, document: Document, complete_event):
            """Get completions for the current document"""
            text = document.text_before_cursor

            if not text or text[-1] == " ":
                return

            word = document.get_word_before_cursor(WORD=True)

            if not word.startswith("/"):
                return

            for cmd in self.commands:
                if cmd.lower().startswith(word.lower()):
                    completion_text = cmd[len(word) :]
                    meta = self.meta_dict.get(cmd, "")

                    yield Completion(
                        text=completion_text,
                        start_position=0,
                        display=cmd,
                        display_meta=meta,
                    )
else:

    class SlashCommandCompleter:  # type: ignore[no-redef]
        """Fallback stub when prompt_toolkit is unavailable."""

        def __init__(self, commands: list[str], meta_dict: dict[str, str] | None = None):
            self.commands = sorted(commands)
            self.meta_dict = meta_dict or {}


class ClarificationSession:
    """Tracks clarification history and manages iterative refinement"""

    def __init__(self):
        self.history = []
        self.current_round = 0
        self.max_rounds = 5
        self.original_query = None
        self.last_failed_query = None
        self.last_error = None

    def add_round(self, query: str, response: str):
        """Add a clarification round to history"""
        from datetime import datetime

        self.history.append(
            {
                "round": self.current_round,
                "query": query,
                "response": response,
                "timestamp": datetime.now(),
            }
        )
        self.current_round += 1

    def can_continue(self) -> bool:
        """Check if more clarification rounds are allowed"""
        return self.current_round < self.max_rounds

    def get_context(self) -> str:
        """Get formatted history context for next clarification"""
        if not self.history:
            return ""

        context_parts = ["Previous clarification rounds:"]
        for entry in self.history:
            context_parts.append(f"\nRound {entry['round'] + 1}:")
            context_parts.append(f"Query: {entry['query']}")
            context_parts.append(f"Response: {entry['response'][:200]}...")

        return "\n".join(context_parts)

    def reset(self):
        """Reset the session for a new clarification"""
        self.history.clear()
        self.current_round = 0
        self.original_query = None
        self.last_failed_query = None
        self.last_error = None


class InteractiveSession:
    """Interactive mode session using Prompt Toolkit Application"""

    def __init__(
        self,
        console: Console,
        config: ConfigManager,
        initial_settings: InteractiveInitialSettings,
    ):
        self.console = console
        self.config = config
        self.initial_settings = initial_settings
        self.result = None
        self.should_exit = False

        self.slash_registry = SlashCommandRegistry(console)
        self.slash_registry.current_mode = initial_settings.mode or config.data["general"].get(
            "default_mode", "default"
        )
        self.slash_registry.current_provider = initial_settings.provider
        self.slash_registry.async_mode = initial_settings.async_mode

        self.current_provider = initial_settings.provider
        self.current_model = None
        self.pending_command = None

        self.cli_api_keys = initial_settings.cli_api_keys

        self.current_input_mode = (
            InputMode.CLARIFICATION_MODE if initial_settings.clarify_mode else InputMode.EDIT_MODE
        )
        self.original_query = None
        self.clarification_response = None
        self.clarification_in_progress = False
        self.clarification_session = ClarificationSession()

        self.supports_shift_enter = self._enable_extended_keyboard()

        import platform

        if self.supports_shift_enter:
            self.newline_key = "Shift+Return"
        else:
            self.newline_key = "Option+Return" if platform.system() == "Darwin" else "Alt+Enter"

        clarify_config = config.data.get("clarification", {}).get("interactive", {})
        input_height = clarify_config.get("input_height", 6)
        self.input_height = input_height
        self.max_input_height = clarify_config.get("max_input_height", 15)

        self.input_area = TextArea(
            text=initial_settings.prompt or "",
            multiline=True,
            prompt="❯ ",
            wrap_lines=True,
            scrollbar=True,
            height=self.input_height,
            completer=self._create_completer(),
        )

        help_html = self._create_help_text()

        self.help_label = Label(HTML(help_html), style="class:help")

        self.input_frame = Frame(self.input_area, title=" Prompt ", style="class:input-frame")

        self.layout = Layout(HSplit([self.help_label, self.input_frame]))

        self.kb = self._create_key_bindings()

        self.style = Style.from_dict(
            {
                "help": "#888888",
                "input-frame": "#0080ff",
                "prompt": "#00aa00 bold",
            }
        )

        self.app = Application(
            layout=self.layout,
            key_bindings=self.kb,
            style=self.style,
            enable_page_navigation_bindings=False,
            mouse_support=True,
            full_screen=False,
        )

    def _enable_extended_keyboard(self) -> bool:
        """
        Try to enable CSI-u extended keyboard mode for modern terminals.
        This allows capturing Shift+Enter as a distinct key combination.
        """
        if not sys.stdin.isatty():
            return False

        try:
            sys.stdout.write("\x1b[>4;2m")
            sys.stdout.flush()

            term = os.environ.get("TERM", "").lower()
            term_program = os.environ.get("TERM_PROGRAM", "").lower()

            if any(t in term for t in ["xterm-256color", "screen-256color", "tmux"]):
                return True
            if term_program.startswith("i" + "term") or any(
                t in term_program for t in ["warp", "vscode"]
            ):
                return True
            if "WT_SESSION" in os.environ:
                return True

            return True

        except Exception:
            return False

    def _create_help_text(self) -> str:
        """Create the help text HTML"""
        mode_text = f"Mode: <b>{self.slash_registry.current_mode}</b>"
        provider_text = f"Provider: <b>{self.current_provider or 'auto'}</b>"

        newline_help = (
            f"<b>{self.newline_key}</b>" if self.supports_shift_enter else "<b>Ctrl+J</b>"
        )

        if self.current_input_mode == InputMode.CLARIFICATION_MODE:
            if self.clarification_response:
                mode_indicator = "<b style='color:orange'>📝 Clarification Mode</b>"
                if self.clarification_session.can_continue():
                    round_info = f" (Round {self.clarification_session.current_round}/{self.clarification_session.max_rounds})"
                    action_help = "<b>Enter</b>: accept • Type 'continue' + <b>Enter</b>: more clarification • <b>Shift+Tab</b>: edit"
                else:
                    round_info = " (Final round)"
                    action_help = "<b>Enter</b>: accept • <b>Shift+Tab</b>: edit mode"
                mode_indicator += round_info
            else:
                mode_indicator = "<b style='color:yellow'>🔍 Clarification Mode</b>"
                if self.clarification_session.last_error:
                    action_help = (
                        "<b>Enter</b>: clarify • <b>Ctrl+R</b>: retry • <b>Shift+Tab</b>: edit mode"
                    )
                else:
                    action_help = "<b>Enter</b>: clarify • <b>Shift+Tab</b>: edit mode"
        else:
            mode_indicator = "<b style='color:green'>✏️ Edit Mode</b>"
            action_help = (
                f"<b>Enter</b>: submit • {newline_help}: new line • <b>Shift+Tab</b>: clarify"
            )

        clarify_config = self.config.data.get("clarification", {}).get("interactive", {})
        default_height = clarify_config.get("input_height", 6)
        current_height = self.input_height

        size_indicator = ""
        if isinstance(current_height, int) and current_height != default_height:
            size_indicator = f" • Size: {current_height} lines (<b>Ctrl+=/-</b>: resize)"

        return (
            f"{mode_indicator} | {mode_text} | {provider_text}\n"
            f"{action_help} • <b>/help</b>: commands{size_indicator}"
        )

    def _update_help_text(self):
        """Update help text with current state"""
        self.help_label.text = HTML(self._create_help_text())

    def _create_completer(self):
        """Create completer for slash commands"""
        commands = [
            "/help",
            "/keybindings",
            "/mode",
            "/provider",
            "/async",
            "/status",
            "/exit",
            "/quit",
        ]

        meta_dict = {
            "/help": "Show available commands",
            "/keybindings": "Show keyboard shortcuts",
            "/mode": "Change research mode",
            "/provider": "Set provider",
            "/async": "Toggle async mode",
            "/status": "Check operation status",
            "/exit": "Exit interactive mode",
            "/quit": "Exit interactive mode",
        }

        return SlashCommandCompleter(commands, meta_dict)

    def _create_key_bindings(self):
        """Create custom key bindings"""
        kb = KeyBindings()

        @kb.add("enter")
        def handle_enter(event):
            """Enter key: submit or process commands"""
            buffer = event.app.current_buffer
            text = buffer.text.strip()

            if not text:
                return

            if text.startswith("/"):
                self._handle_slash_command(text)
                buffer.text = ""
                return

            if self.pending_command:
                self._handle_command_continuation(text)
                buffer.text = ""
                return

            if self.current_input_mode == InputMode.CLARIFICATION_MODE:
                if self.clarification_response:
                    if (
                        text.lower().strip() == "continue"
                        and self.clarification_session.can_continue()
                    ):
                        refined_text = buffer.text.split("Refined query (edit as needed):\n")[
                            -1
                        ].strip()
                        self._process_clarification(refined_text)
                    else:
                        self.current_input_mode = InputMode.EDIT_MODE
                        self.clarification_response = None
                        self._update_help_text()
                else:
                    self._process_clarification(text)
            else:
                self.result = text
                event.app.exit()

        @kb.add("s-tab")
        def handle_shift_tab(event):
            """Shift+Tab: Toggle between Edit and Clarification modes"""
            if self.current_input_mode == InputMode.EDIT_MODE:
                self.current_input_mode = InputMode.CLARIFICATION_MODE
                self.original_query = event.current_buffer.text
            else:
                self.current_input_mode = InputMode.EDIT_MODE
                if not self.clarification_response and self.original_query:
                    event.current_buffer.text = self.original_query

            self.clarification_response = None
            self._update_help_text()

        try:

            @kb.add("backtab")
            def handle_shift_tab_alt(event):
                """Shift+Tab alternative: Toggle between Edit and Clarification modes"""
                handle_shift_tab(event)
        except Exception:
            pass

        if self.supports_shift_enter:
            try:

                @kb.add("\x1b[13;2u")
                def handle_shift_enter(event):
                    """Shift+Enter: insert newline (CSI-u mode)"""
                    event.current_buffer.insert_text("\n")
            except ValueError:
                self.supports_shift_enter = False

        @kb.add("c-j")
        def handle_ctrl_j(event):
            """Ctrl+J: insert newline (universal)"""
            event.current_buffer.insert_text("\n")

        @kb.add("escape", "enter")
        def handle_alt_enter(event):
            """Alt+Enter: insert newline (fallback)"""
            event.current_buffer.insert_text("\n")

        @kb.add("c-c")
        def handle_abort(event):
            """Ctrl+C: abort interactive mode"""
            self.result = None
            event.app.exit()

        @kb.add("tab")
        def handle_tab(event):
            """Tab: trigger completion"""
            event.current_buffer.complete_next()

        @kb.add("c-r")
        def handle_retry_clarification(event):
            """Ctrl+R: Retry last failed clarification"""
            if (
                self.current_input_mode == InputMode.CLARIFICATION_MODE
                and self.clarification_session.last_failed_query
            ):
                self._process_clarification(self.clarification_session.last_failed_query)
            elif self.clarification_session.last_error:
                event.app.layout.focus(self.input_area)
                event.current_buffer.text = f"No clarification to retry. Last error: {self.clarification_session.last_error}"

        def handle_increase_height(event):
            """Increase input area height"""
            current_height = self.input_height
            if current_height < self.max_input_height:
                self.input_height = current_height + 1
                self.input_area.window.height = self.input_height
                self._update_help_text()

        try:

            @kb.add("c-=")
            def handle_increase_height_primary(event):
                handle_increase_height(event)
        except Exception:
            pass

        try:

            @kb.add("c-+")
            def handle_increase_height_alt(event):
                """Ctrl++: Increase input area height"""
                handle_increase_height(event)
        except Exception:
            pass

        def handle_decrease_height(event):
            """Decrease input area height"""
            current_height = self.input_height
            if current_height > 3:
                self.input_height = current_height - 1
                self.input_area.window.height = self.input_height
                self._update_help_text()

        try:

            @kb.add("c--")
            def handle_decrease_height_primary(event):
                handle_decrease_height(event)
        except Exception:
            pass

        return kb

    def _handle_slash_command(self, text: str):
        """Handle slash commands"""
        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if command == "/help":
            self._show_help()
        elif command == "/keybindings":
            self._show_keybindings()
        elif command == "/mode":
            if args:
                if args in BUILTIN_MODES:
                    self.slash_registry.current_mode = args
                    self._update_help_text()
                    run_in_terminal(lambda: print(f"Mode set to: {args}"))
            else:
                self._show_mode_selection()
        elif command == "/provider":
            if args:
                self.current_provider = args
                self._update_help_text()
                run_in_terminal(lambda: print(f"Provider set to: {args}"))
            else:
                self._show_provider_selection()
        elif command == "/async":
            self.slash_registry.async_mode = not self.slash_registry.async_mode
            status = "enabled" if self.slash_registry.async_mode else "disabled"
            run_in_terminal(lambda: print(f"Async mode: {status}"))
        elif command == "/status":
            if self.slash_registry.last_operation_id:
                operation_id = self.slash_registry.last_operation_id
                run_in_terminal(lambda: asyncio.run(show_status(operation_id)))
            else:
                run_in_terminal(lambda: print("No operations yet"))
        elif command in ["/exit", "/quit"]:
            self.result = None
            self.app.exit()
        else:
            run_in_terminal(lambda: print(f"Unknown command: {command}"))

    def _show_help(self):
        """Display help information"""

        def print_help():
            print("\nAvailable commands:")
            print("  /help              - Show this help")
            print("  /keybindings       - Show keyboard shortcuts")
            print("  /mode [<mode>]     - Change or show research modes")
            print("  /provider [<name>] - Set or show providers")
            print("  /async             - Toggle async mode")
            print("  /status            - Check last operation status")
            print("  /exit, /quit       - Exit interactive mode")
            print("\nInput Modes:")
            print("  Edit Mode          - Write and submit prompts for research")
            print("  Clarification Mode - Get AI suggestions to refine your prompt")
            print("\nType /keybindings to see keyboard shortcuts")
            print()

        run_in_terminal(print_help)

    def _show_keybindings(self):
        """Display keyboard shortcuts"""

        def print_keybindings():
            print("\nKeyboard shortcuts:")
            print("  Enter              - Submit prompt (Edit) / Accept clarification (Clarify)")
            print("  Shift+Tab          - Toggle between Edit and Clarification modes")
            print("  Ctrl+R             - Retry failed clarification")
            print("  Ctrl+=             - Increase input area size")
            print("  Ctrl+-             - Decrease input area size")

            if self.supports_shift_enter:
                print("  Shift+Return       - Insert new line (detected support)")
            print(f"  {self.newline_key:18} - Insert new line")
            print("  Ctrl+J             - Insert new line (universal)")

            print("  Tab                - Complete slash commands")
            print("  Ctrl+A             - Go to line start")
            print("  Ctrl+E             - Go to line end")
            print("  Ctrl+K             - Delete to end of line")
            print("  Ctrl+U             - Delete to start of line")
            print("  Ctrl+W             - Delete word before cursor")
            print("  Ctrl+C             - Exit without submitting")
            print("  Arrow keys         - Navigate text")
            print()

            print("Mode-specific behavior:")
            print("  Edit Mode:")
            print("    - Enter submits prompt for research")
            print("    - Shift+Tab switches to Clarification Mode")
            print("  Clarification Mode:")
            print("    - Enter processes clarification")
            print("    - After clarification, Enter accepts refined query")
            print("    - Shift+Tab returns to Edit Mode")
            print()

            if self.supports_shift_enter:
                print("Note: Your terminal supports Shift+Return!")
                print("You can also use Ctrl+J or Option+Return as alternatives.")
            else:
                print("Note: Shift+Return is not supported by your terminal.")
                print(
                    "Use Ctrl+J (recommended) or Option+Return (Mac) / Alt+Enter (Linux/Windows)."
                )
            print()

        run_in_terminal(print_keybindings)

    def _show_mode_selection(self):
        """Show available modes for selection"""
        from doxa_research.modes_cmd import list_all_modes

        def print_modes():
            cm = get_config()
            infos = list_all_modes(cm)
            print("\nAvailable modes:")
            for i, info in enumerate(infos, 1):
                desc = (info.description or "")[:60]
                current = " ← current" if info.name == self.slash_registry.current_mode else ""
                print(f"  {i}. {info.name:15} [{info.kind:<10}] {desc}{current}")
            print("\nType: /mode <name> to select a mode")
            print()

        run_in_terminal(print_modes)

    def _show_provider_selection(self):
        """Show available providers for selection"""

        def print_providers():
            print("\nAvailable providers:")
            providers = [
                ("openai", "OpenAI GPT models"),
                ("perplexity", "Perplexity Sonar (web-grounded synchronous search)"),
                (
                    "gemini",
                    "Gemini 2.5 (web-grounded synchronous search with optional thinking)",
                ),
                ("mock", "Mock provider for testing"),
                ("auto", "Automatic provider selection"),
            ]
            for i, (name, desc) in enumerate(providers, 1):
                current = " ← current" if name == (self.current_provider or "auto") else ""
                print(f"  {i}. {name:12} - {desc}{current}")
            print("\nType: /provider <name> to select a provider")
            print()

        run_in_terminal(print_providers)

    def _handle_command_continuation(self, text: str):
        """Handle continuation of multi-step commands"""
        self.pending_command = None

    def _process_clarification(self, query: str):
        """Process query through clarification mode"""
        from prompt_toolkit.application import get_app

        self.original_query = query
        self.clarification_in_progress = True

        async def run_clarification():
            try:
                clarification_text = await self._get_clarification_suggestions(query)

                app = get_app()
                if app and app.current_buffer:
                    app.current_buffer.text = clarification_text
                    self.clarification_response = clarification_text
                    self.clarification_in_progress = False
                    self._update_help_text()

            except Exception as e:
                app = get_app()
                if app and app.current_buffer:
                    app.current_buffer.text = (
                        f"Error getting clarification: {str(e)}\n\nOriginal query: {query}"
                    )
                    self.current_input_mode = InputMode.EDIT_MODE
                    self.clarification_in_progress = False
                    self._update_help_text()

        app = get_app()
        app.current_buffer.text = "🔍 Getting clarification suggestions..."

        asyncio.ensure_future(run_clarification())

    async def _get_clarification_suggestions(self, query: str) -> str:
        """Get clarification suggestions from the LLM with retry logic"""
        clarify_config = self.config.data.get("clarification", {}).get("interactive", {})

        model = clarify_config.get("model", "gpt-4o-mini")
        temperature = clarify_config.get("temperature", 0.7)
        max_tokens = clarify_config.get("max_tokens", 800)
        system_prompt = clarify_config.get(
            "system_prompt",
            """I don't want you to follow the above question and instructions; I want you to tell me the ways this is unclear, point out any ambiguities or anything you don't understand. Follow that by asking questions to help clarify the ambiguous points. Once there are no more unclear, ambiguous or not understood portions, help me draft a clear version of the question/instruction.""",
        )
        retry_attempts = clarify_config.get("retry_attempts", 3)
        retry_delay = clarify_config.get("retry_delay", 2.0)

        self.clarification_session.last_failed_query = query

        api_keys = self.cli_api_keys or {}

        openai_key = (
            api_keys.get("openai")
            or self.config.data.get("providers", {}).get("openai", {}).get("api_key")
            or os.environ.get("OPENAI_API_KEY")
        )

        if not openai_key:
            raise ValueError(
                "OpenAI API key not found. Please set OPENAI_API_KEY or configure in ~/.config/doxa_research/doxa.config.toml"
            )

        context = self.clarification_session.get_context()
        if context:
            query = f"{context}\n\nCurrent query: {query}"

        @retry(
            stop=stop_after_attempt(retry_attempts),
            wait=wait_exponential(multiplier=retry_delay, min=retry_delay, max=retry_delay * 4),
            retry=retry_if_exception_type((APITimeoutError, APIConnectionError)),
            before_sleep=lambda retry_state: self._log_retry_attempt(retry_state),
        )
        async def make_clarification_request():
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": query})

            # Tenacity owns the configured retry count. The SDK translates
            # transport failures into APIConnectionError/APITimeoutError.
            async with AsyncOpenAI(
                api_key=openai_key, timeout=httpx2.Timeout(30.0, connect=5.0), max_retries=0
            ) as client:
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content

        try:
            clarification_text = await make_clarification_request()

            self.clarification_session.add_round(query, clarification_text)

            round_indicator = (
                f" (Round {self.clarification_session.current_round}/{self.clarification_session.max_rounds})"
                if self.clarification_session.current_round > 1
                else ""
            )

            formatted_response = f"""📝 Clarification Suggestions{round_indicator}:

{clarification_text}

---
✏️ Refined query (edit as needed):
{query.split("Current query: ")[-1] if "Current query: " in query else query}"""

            self.clarification_session.last_error = None

            return formatted_response

        except Exception as e:
            self.clarification_session.last_error = str(e)
            error_msg = f"❌ Failed to get clarification after {retry_attempts} attempts: {str(e)}"

            error_msg += "\n\nPress Ctrl+R to retry or Shift+Tab to return to Edit Mode."
            raise Exception(error_msg)

    def _log_retry_attempt(self, retry_state):
        """Log retry attempts for clarification"""
        attempt = retry_state.attempt_number
        if attempt > 1:
            from prompt_toolkit.application import get_app

            app = get_app()
            app.current_buffer.text = f"🔄 Retrying clarification... (Attempt {attempt})"

    async def run_async(self) -> str | None:
        """Run the interactive application asynchronously and return the prompt"""
        if not sys.stdin.isatty():
            console = self.console
            console.print("[yellow]Warning: Not in terminal, using basic input mode[/yellow]")

            while True:
                try:
                    prompt = input("> ")
                    if not prompt:
                        continue

                    if prompt.startswith("/"):
                        if prompt in ["/exit", "/quit"]:
                            return None
                        elif prompt == "/help":
                            print("Available commands:")
                            print("  /help - Show this help")
                            print("  /keybindings - Show keyboard shortcuts")
                            print("  /exit, /quit - Exit")
                            print("  (Other commands not available in basic mode)")
                            continue
                        elif prompt == "/keybindings":
                            print("Keyboard shortcuts (basic mode):")
                            print("  Enter - Submit prompt")
                            print("  Ctrl+C - Exit")
                            print("  (Advanced shortcuts not available in basic mode)")
                            continue
                        else:
                            print(f"Command not available in basic mode: {prompt}")
                            continue

                    return prompt

                except (EOFError, KeyboardInterrupt):
                    return None

        await self.app.run_async()
        return self.result

    def run(self) -> str | None:
        """Run the interactive application synchronously"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run_async())
        raise RuntimeError("Cannot use run() in async context, use run_async() instead")


async def enter_interactive_mode(
    initial_settings: InteractiveInitialSettings,
    project: str | None,
    output_dir: str | None,
    config_path: str | None,
    verbose: bool,
    quiet: bool,
    no_metadata: bool,
    timeout: float | None,
    profile: str | None = None,
):
    """Enter interactive prompt mode with Prompt Toolkit"""
    if not PROMPT_TOOLKIT_AVAILABLE:
        console.print(
            "[yellow]Warning: prompt_toolkit not available, falling back to basic input[/yellow]"
        )
        config = get_config(profile=profile)
        console.print("[bold cyan]Interactive Mode (Basic)[/bold cyan]")
        console.print("[dim]Enter prompt • /help: commands • /exit: quit[/dim]")
        console.print()

        slash_registry = SlashCommandRegistry(console)
        slash_registry.current_mode = initial_settings.mode or config.data["general"].get(
            "default_mode", "default"
        )
        slash_registry.current_provider = initial_settings.provider
        slash_registry.async_mode = initial_settings.async_mode

        try:
            while True:
                prompt = input("> ")

                if prompt.startswith("/"):
                    action = slash_registry.parse_and_execute(prompt)
                    if action == "exit":
                        console.print("[yellow]Exiting interactive mode[/yellow]")
                        return
                    continue

                if prompt.strip():
                    console.print(f"[green]Processing prompt:[/green] {prompt}")
                    slash_registry.last_operation_id = await run_research(
                        mode=slash_registry.current_mode,
                        prompt=prompt,
                        project=project,
                        output_dir=output_dir,
                        provider=slash_registry.current_provider,
                        input_file=None,
                        auto=False,
                        verbose=verbose,
                        async_mode=slash_registry.async_mode,
                        cli_api_keys=initial_settings.cli_api_keys
                        if initial_settings.cli_api_keys
                        else {},
                        combined=False,
                        quiet=quiet,
                        no_metadata=no_metadata,
                        timeout_override=timeout,
                        profile=profile,
                    )
                    break
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Interactive mode cancelled[/yellow]")
        return

    config = get_config(profile=profile)

    session = InteractiveSession(console, config, initial_settings)

    prompt = await session.run_async()

    if prompt is None:
        console.print("\n[yellow]Interactive mode cancelled[/yellow]")
        return

    console.print(f"\n[green]Processing prompt:[/green] {prompt}")
    console.print(
        f"[dim]Mode: {session.slash_registry.current_mode} | Provider: {session.current_provider or 'auto'} | Async: {session.slash_registry.async_mode}[/dim]"
    )
    console.print()

    try:
        api_keys = initial_settings.cli_api_keys if initial_settings.cli_api_keys else {}

        session.slash_registry.last_operation_id = await run_research(
            mode=session.slash_registry.current_mode,
            prompt=prompt,
            project=project,
            output_dir=output_dir,
            provider=session.current_provider,
            input_file=None,
            auto=False,
            verbose=verbose,
            async_mode=session.slash_registry.async_mode,
            cli_api_keys=api_keys,
            combined=False,
            quiet=quiet,
            no_metadata=no_metadata,
            timeout_override=timeout,
            profile=profile,
        )

    except Exception as e:
        console.print(f"[red]Error:[/red] {str(e)}")


__all__ = [
    "ClarificationSession",
    "InteractiveSession",
    "PROMPT_TOOLKIT_AVAILABLE",
    "SlashCommandCompleter",
    "SlashCommandRegistry",
    "enter_interactive_mode",
]
