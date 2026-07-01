"""Full-screen interactive terminal UI for ``optics live``.

Built on ``prompt_toolkit``'s full-screen :class:`Application` (alternate screen
buffer, redrawn in place) — like Claude Code, vim or lazygit, not a scrolling REPL.
A persistent input box and status bar are pinned at the bottom; executed actions
accumulate in a scrollable history pane above that auto-scrolls to the newest entry.
"""

import asyncio
import functools
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from prompt_toolkit.application import Application, get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.data_structures import Point
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
    Window,
    WindowAlign,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import AfterInput
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame

from optics_framework.helper.live import (
    LiveController, ActionResult, NLStep, NLSummary,
    ActionStatus, NLRunStatus, NLStepKind, SaveConflictError,
)


_STATUS_HINT = "Tab complete · Ctrl-K keywords · Ctrl-N AI mode · /help · /quit"

# Style class for secondary / muted text, reused across history and overlays.
_META = "class:meta"

# Style class for in-progress / AI-mode indicators, reused across history and overlays.
_RUNNING = "class:running"

_HELP_TEXT = """\
Optics Live — command reference

  Type a keyword call and press Enter to run it against the target, e.g.
      launch_app
      press_element ${login_btn} index=0
      enter_text ${username} "hello world"

  The target (appium/selenium/playwright/…) comes from the project's config.yaml.
  Recording is always on — every successful action is buffered. Use /save to persist.

Natural-language mode (Ctrl-N)
  Toggle AI mode and type an instruction in plain English, e.g.
      type "movies for kids" in the search bar
      click the home button
  An LLM reads the screen and drives keywords step-by-step until the goal is reached.
  Each executed keyword is recorded (so a successful run is /save-able). Ctrl-X aborts.
  Requires an enabled 'llm_models' entry (e.g. gemini) in config.yaml.

Slash commands (work in both modes)
  /save <test_case> <module_name>
                 Save recorded actions as <module_name> in modules/modules.csv and a
                 <test_case> row in test_cases/test_cases.csv (appends if they exist;
                 clears the buffer so the next actions form the next module)
  /device [id]   List/switch connected Android + iOS devices (Appium sessions only)
  /elements      Show named elements and their locators (read-only)
  /screenshot    Capture the device screen to a file
  /help          Show this reference
  /quit          End the session (driver teardown) and exit

Keys
  Enter          Run the command / accept the highlighted completion
  Tab / S-Tab    Cycle completions
  ${             Suggest element names
  Ctrl-K         Toggle the keyword browser (Up/Down to move, Enter to pick)
  Ctrl-N         Toggle natural-language (AI) mode
  Ctrl-X         Abort a running AI run (stops at the next step)
  Esc            Close any popup or the keyword browser
  Ctrl-C         Quit

Press Esc to close this help.
"""

_STYLE = Style.from_dict(
    {
        "pass": "#00cc66",
        "fail": "#ff5555",
        "running": "#ffcc00",
        "info": "#55ccff",
        "cmd": "bold",
        "ghost": "#777777 italic",
        "meta": "#999999",
        "sep": "#444444",
        "status": "#888888",
        "status.rec": "fg:#ff5555 bold",
        "status.device": "fg:#cccccc bold",
        "status.sep": "fg:#444444",
        "overlay.title": "bold",
        "overlay.sel": "reverse",
        "frame.border": "#5588cc",
    }
)


class LiveCompleter(Completer):
    """Completes keyword names (first token) and element names (inside ``${...}``)."""

    def __init__(self, controller: LiveController, is_nl_mode: Optional[Callable[[], bool]] = None):
        self.controller = controller
        self.is_nl_mode = is_nl_mode

    def _element_completions(self, prefix: str) -> Iterable[Completion]:
        """Element names matching ``prefix`` (cursor sits inside an unclosed ``${...}``)."""
        for name in self.controller.element_names():
            if name.lower().startswith(prefix.lower()):
                locator = self.controller.element_first_locator(name) or ""
                yield Completion(
                    name + "}",
                    start_position=-len(prefix),
                    display=name,
                    display_meta=(locator[:60] if locator else ""),
                )

    def _keyword_completions(self, before: str) -> Iterable[Completion]:
        """Keyword names — only while typing the first token, never after a slash command."""
        if before.startswith("/"):
            return
        parts = before.split()
        typing_first = len(parts) == 0 or (len(parts) == 1 and not before.endswith(" "))
        if not typing_first:
            return
        word = parts[0] if parts else ""
        for keyword in self.controller.keyword_names():
            if keyword.startswith(word.lower()):
                signature = self.controller.keyword_signature(keyword) or ""
                yield Completion(
                    keyword,
                    start_position=-len(word),
                    display=keyword,
                    display_meta=signature,
                )

    def get_completions(self, document: Document, complete_event) -> Iterable[Completion]:
        # In natural-language mode the input is English prose; keyword/${} completion
        # would only get in the way, so suppress it entirely.
        if self.is_nl_mode is not None and self.is_nl_mode():
            return
        before = document.text_before_cursor
        marker = before.rfind("${")
        if marker != -1 and "}" not in before[marker:]:
            yield from self._element_completions(before[marker + 2:])
            return
        yield from self._keyword_completions(before)


class LiveTUI:
    """Wires the controller to a full-screen prompt_toolkit application."""

    def __init__(self, controller: LiveController):
        self.controller = controller
        self.entries: List[ActionResult] = []
        self._busy = False
        self._quit_armed = False
        # Set to the (test_case, module_name) of the last /save that hit a name
        # conflict; re-running the identical /save then confirms the append.
        self._save_armed: Optional[Tuple[str, str]] = None
        self._known_devices: List[str] = []

        # Natural-language mode: when on, the whole input box is treated as English and
        # routed through the LLM agent. Off by default — keyword-first flow is unchanged.
        self._nl_mode = False
        self._nl_running = False
        self._nl_abort = False

        # Overlay = navigable selection list (keyword browser / device picker).
        self.overlay_title = ""
        self.overlay_items: List[Tuple[str, str]] = []
        self.overlay_index = 0
        self.overlay_on_select: Optional[Callable[[str], None]] = None
        self.overlay_active = False

        # Popup = static read-only text (help / elements).
        self.popup_title = ""
        self.popup_text = ""
        self.popup_active = False

        self.input_buffer = Buffer(
            completer=LiveCompleter(controller, is_nl_mode=lambda: self._nl_mode),
            complete_while_typing=True,
            multiline=False,
            history=InMemoryHistory(),
        )
        self.app = self._build_application()

    # -- History rendering --------------------------------------------------------

    def _render_history(self) -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = []
        if not self.entries:
            fragments.append((_META, "  No actions yet. Type a keyword and press Enter, or /help.\n"))
        for result in self.entries:
            fragments.extend(self._render_entry(result))
        return fragments

    @staticmethod
    def _render_entry(result: ActionResult) -> StyleAndTextTuples:
        # Muted "thinking" line emitted between agent steps.
        if result.nl_thinking:
            return [("class:ghost", f"     thinking: {result.raw}\n")]

        icon_map = {
            "PASS": ("class:pass", "✓"),
            "FAIL": ("class:fail", "✗"),
            "RUNNING": (_RUNNING, "⋯"),
            "INFO": ("class:info", "•"),
        }
        style, icon = icon_map.get(result.status, ("class:info", "•"))
        # Agent-driven keyword results are indented as children under their instruction.
        icon_lead = "   " if result.nl_child else " "
        detail_lead = "       " if result.nl_child else "     "
        out: StyleAndTextTuples = [
            (style, f" {icon_lead}{icon} "),
            ("class:cmd", result.raw),
            ("", "\n"),
        ]
        if result.status == ActionStatus.RUNNING:
            out.append((_RUNNING, f"{detail_lead}running…\n"))
            return out
        if result.status == ActionStatus.INFO:
            if result.message:
                out.append((_META, f"{detail_lead}{result.message}\n"))
            return out
        detail = f"{detail_lead}{result.elapsed:.2f}s"
        if result.status == ActionStatus.PASS and result.strategy:
            detail += f"  [{result.strategy}]"
        out.append((_META, detail))
        if result.message:
            msg_style = "class:fail" if result.status == ActionStatus.FAIL else _META
            out.append((msg_style, f"  {result.message}"))
        out.append(("", "\n"))
        return out

    def _history_cursor(self) -> Point:
        text = "".join(t for _s, t in self._render_history())
        return Point(x=0, y=max(0, text.count("\n")))

    # -- Ghost text ---------------------------------------------------------------

    def _ghost_text(self) -> str:
        text = self.input_buffer.text
        if not text or text.startswith("/") or self._nl_mode:
            return ""
        parts = text.split()
        if not parts:
            return ""
        signature = self.controller.keyword_signature(parts[0].lower())
        if signature is None:
            return ""
        hint_tokens = signature.split(" ")[1:]  # drop the keyword name itself
        typed_params = len(parts) - 1
        remaining = hint_tokens[typed_params:]
        if not remaining:
            return ""
        lead = "" if text.endswith(" ") else " "
        return lead + " ".join(remaining)

    # -- Status bar ---------------------------------------------------------------

    def _render_status(self) -> StyleAndTextTuples:
        device = self.controller.active_target()
        if self._nl_mode:
            mode = (_RUNNING, "AI …" if self._nl_running else "AI ●")
        else:
            mode = ("class:status.rec", "rec ●")
        return [
            ("class:status.device", device),
            ("class:status.sep", "  ·  "),
            mode,
            ("class:status.sep", "  ·  "),
            ("class:status", _STATUS_HINT),
        ]

    # -- Overlay (selection list) -------------------------------------------------

    def _render_overlay(self) -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = []
        for i, (label, _value) in enumerate(self.overlay_items):
            if i == self.overlay_index:
                fragments.append(("class:overlay.sel", f" {label} \n"))
            else:
                fragments.append(("", f" {label} \n"))
        if not self.overlay_items:
            fragments.append((_META, " (none) \n"))
        return fragments

    def _overlay_cursor(self) -> Point:
        return Point(x=0, y=self.overlay_index)

    def open_overlay(self, title: str, items: List[Tuple[str, str]], on_select: Callable[[str], None]) -> None:
        self.overlay_title = title
        self.overlay_items = items
        self.overlay_index = 0
        self.overlay_on_select = on_select
        self.overlay_active = True
        self.popup_active = False

    def close_overlays(self) -> None:
        self.overlay_active = False
        self.popup_active = False
        get_app().invalidate()

    def open_popup(self, title: str, text: str) -> None:
        self.popup_title = title
        self.popup_text = text
        self.popup_active = True
        self.overlay_active = False

    # -- Layout / application -----------------------------------------------------

    def _build_application(self) -> Application:
        history_window = Window(
            content=FormattedTextControl(
                text=self._render_history,
                focusable=False,
                get_cursor_position=self._history_cursor,
            ),
            wrap_lines=True,
            always_hide_cursor=True,
        )

        input_control = BufferControl(
            buffer=self.input_buffer,
            input_processors=[AfterInput(self._ghost_text, style="class:ghost")],
        )
        input_window = Window(
            content=input_control,
            height=1,
            get_line_prefix=self._input_prefix,
        )

        status_window = Window(
            content=FormattedTextControl(self._render_status),
            height=1,
            align=WindowAlign.LEFT,
        )

        body = HSplit(
            [
                history_window,
                Window(height=1, char="─", style="class:sep"),
                input_window,
                Window(height=1, char="─", style="class:sep"),
                status_window,
            ]
        )

        overlay_float = Float(
            content=ConditionalContainer(
                content=Frame(
                    body=Window(
                        content=FormattedTextControl(
                            text=self._render_overlay,
                            focusable=False,
                            get_cursor_position=self._overlay_cursor,
                        ),
                        width=Dimension(min=30, preferred=50),
                        height=Dimension(min=1, max=18),
                        always_hide_cursor=True,
                    ),
                    title=lambda: self.overlay_title,
                ),
                filter=Condition(lambda: self.overlay_active),
            ),
            top=2,
        )

        popup_float = Float(
            content=ConditionalContainer(
                content=Frame(
                    body=Window(
                        content=FormattedTextControl(text=lambda: self.popup_text),
                        width=Dimension(min=40, preferred=72),
                        height=Dimension(min=1, max=30),
                        wrap_lines=True,
                    ),
                    title=lambda: self.popup_title,
                ),
                filter=Condition(lambda: self.popup_active),
            ),
        )

        completions_float = Float(
            xcursor=True,
            ycursor=True,
            content=CompletionsMenu(max_height=12, scroll_offset=1),
        )

        root = FloatContainer(
            content=body,
            floats=[completions_float, overlay_float, popup_float],
        )

        return Application(
            layout=Layout(root, focused_element=input_window),
            key_bindings=self._build_key_bindings(),
            style=_STYLE,
            full_screen=True,
            mouse_support=True,
        )

    def _input_prefix(self, lineno, wrap_count):
        if self._nl_mode:
            return [(_RUNNING, "ai › ")]
        return [("class:cmd", "› ")]

    # -- Key bindings -------------------------------------------------------------

    def _on_enter(self) -> None:
        buf = self.input_buffer
        if buf.complete_state and buf.complete_state.current_completion:
            buf.apply_completion(buf.complete_state.current_completion)
            return
        text = buf.text
        if text.strip():
            buf.append_to_history()
        buf.reset()
        self._submit(text)

    def _on_tab(self) -> None:
        buf = self.input_buffer
        if buf.complete_state:
            buf.complete_next()
        else:
            buf.start_completion(select_first=False)

    def _on_shift_tab(self) -> None:
        buf = self.input_buffer
        if buf.complete_state:
            buf.complete_previous()

    def _move_overlay(self, delta: int) -> None:
        if self.overlay_items:
            self.overlay_index = (self.overlay_index + delta) % len(self.overlay_items)

    def _recall_history(self, backward: bool) -> None:
        buf = self.input_buffer
        buf.cancel_completion()
        if backward:
            buf.history_backward(count=1)
        else:
            buf.history_forward(count=1)

    def _on_overlay_enter(self) -> None:
        if self.overlay_items and self.overlay_on_select:
            _label, value = self.overlay_items[self.overlay_index]
            callback = self.overlay_on_select
            self.close_overlays()
            callback(value)

    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()
        blocking = Condition(lambda: self.overlay_active or self.popup_active)
        overlay_on = Condition(lambda: self.overlay_active)
        not_blocking = ~blocking

        @kb.add("enter", filter=~blocking)
        def _(event):
            self._on_enter()

        @kb.add("tab", filter=~blocking)
        def _(event):
            self._on_tab()

        @kb.add("s-tab", filter=~blocking)
        def _(event):
            self._on_shift_tab()

        @kb.add("c-k", filter=~blocking)
        def _(event):
            self._open_keyword_browser()

        @kb.add("c-n", filter=~blocking)
        def _(event):
            self._toggle_nl_mode()

        @kb.add("c-x", filter=Condition(lambda: self._nl_running))
        def _(event):
            self._abort_nl()

        @kb.add("up", filter=overlay_on)
        def _(event):
            self._move_overlay(-1)

        @kb.add("down", filter=overlay_on)
        def _(event):
            self._move_overlay(1)

        @kb.add("up", filter=not_blocking & ~overlay_on)
        def _(event):
            self._recall_history(backward=True)

        @kb.add("down", filter=not_blocking & ~overlay_on)
        def _(event):
            self._recall_history(backward=False)

        @kb.add("enter", filter=overlay_on)
        def _(event):
            self._on_overlay_enter()

        @kb.add("escape", filter=blocking)
        def _(event):
            self.close_overlays()

        @kb.add("c-c")
        @kb.add("c-d")
        def _(event):
            self._request_quit()

        return kb

    # -- Command handling ---------------------------------------------------------

    def _append(self, result: ActionResult) -> None:
        self.entries.append(result)
        get_app().invalidate()

    def _info(self, message: str, raw: str = "") -> None:
        self._append(ActionResult(raw=raw or message, status=ActionStatus.INFO, message=message if raw else None))

    def _submit(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self._busy:
            self._info("Busy — wait for the current action to finish.")
            return
        if text.startswith("/"):
            self._handle_command(text)
        elif self._nl_mode:
            self._run_nl_async(text)
        else:
            self._run_keyword_async(text)

    def _run_keyword_async(self, text: str) -> None:
        pending = ActionResult(raw=text, status=ActionStatus.RUNNING)
        self.entries.append(pending)
        self._busy = True
        app = get_app()

        async def task() -> None:
            loop = asyncio.get_event_loop()
            try:
                result = await loop.run_in_executor(None, self.controller.run_keyword, text)
                pending.status = result.status
                pending.keyword = result.keyword
                pending.params = result.params
                pending.elapsed = result.elapsed
                pending.strategy = result.strategy
                pending.message = result.message
            except Exception as exc:  # noqa: BLE001 - never crash the UI
                pending.status = ActionStatus.FAIL
                pending.message = f"{type(exc).__name__}: {exc}"
            finally:
                self._busy = False
                app.invalidate()

        app.create_background_task(task())
        app.invalidate()

    # -- Natural-language mode ----------------------------------------------------

    def _toggle_nl_mode(self) -> None:
        if self._nl_running:
            self._info("Can't switch modes while a natural-language run is active (Ctrl-X to abort).")
            return
        self._nl_mode = not self._nl_mode
        self.input_buffer.cancel_completion()
        if self._nl_mode:
            self._info("Natural-language mode ON — describe an action in English. Ctrl-N to exit.")
            if not self.controller.natural_language_available():
                self._info(
                    "No LLM engine is enabled. Add an enabled 'llm_models' entry (e.g. gemini) "
                    "to your config.yaml and restart optics live."
                )
        else:
            self._info("Natural-language mode OFF — back to keyword input.")
        get_app().invalidate()

    def _abort_nl(self) -> None:
        if not self._nl_running:
            return
        self._nl_abort = True
        self._info("Aborting natural-language run — stopping at the next step…")
        get_app().invalidate()

    @staticmethod
    def _nl_summary_message(summary: NLSummary) -> str:
        if summary.status == NLRunStatus.PASS:
            label = "done"
        elif summary.status == NLRunStatus.ABORTED:
            label = "aborted"
        elif summary.status == NLRunStatus.MAX_STEPS:
            label = "stopped (max steps)"
        else:
            label = "failed"
        parts = [f"{label} · {summary.steps} step(s) · {summary.elapsed:.1f}s"]
        if summary.status == NLRunStatus.PASS and summary.steps:
            parts.append(f"recorded {summary.steps} (/save to keep)")
        if summary.message and summary.status != "PASS":
            parts.append(summary.message)
        return " · ".join(parts)

    def _run_nl_async(self, instruction: str) -> None:
        header = ActionResult(raw=f"[ai] {instruction}", status=ActionStatus.RUNNING)
        self.entries.append(header)
        self._busy = True
        self._nl_running = True
        self._nl_abort = False
        app = get_app()
        loop = asyncio.get_event_loop()

        def on_step(step: NLStep) -> None:
            # Runs on the executor thread — build the entry here, but schedule the list
            # mutation + redraw on the event-loop thread to avoid a render-race.
            if step.kind == NLStepKind.KEYWORD and step.result is not None:
                entry = step.result
                entry.nl_child = True
            else:  # thinking / note
                entry = ActionResult(raw=step.text, status=ActionStatus.INFO, nl_thinking=True)

            def _apply() -> None:
                self.entries.append(entry)
                app.invalidate()

            loop.call_soon_threadsafe(_apply)

        async def task() -> None:
            try:
                summary = await loop.run_in_executor(
                    None,
                    functools.partial(
                        self.controller.run_natural_language,
                        instruction,
                        on_step,
                        lambda: self._nl_abort,
                    ),
                )
                header.status = ActionStatus.PASS if summary.status == NLRunStatus.PASS else ActionStatus.FAIL
                header.elapsed = summary.elapsed
                header.message = self._nl_summary_message(summary)
            except Exception as exc:  # noqa: BLE001 - never crash the UI
                header.status = ActionStatus.FAIL
                header.message = f"{type(exc).__name__}: {exc}"
            finally:
                self._busy = False
                self._nl_running = False
                app.invalidate()

        app.create_background_task(task())
        app.invalidate()

    def _handle_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        handlers: Dict[str, Callable[[str], None]] = {
            "/save": self._cmd_save,
            "/device": self._cmd_device,
            "/elements": self._cmd_elements,
            "/screenshot": self._cmd_screenshot,
            "/help": self._cmd_help,
            "/quit": self._cmd_quit,
            "/exit": self._cmd_quit,
        }
        handler = handlers.get(command)
        if handler is None:
            self._info(f"Unknown command: {command}  (try /help)")
            return
        handler(arg)

    def _cmd_save(self, arg: str) -> None:
        tokens = arg.split()
        if len(tokens) != 2:
            self._info("Usage: /save <test_case> <module_name>")
            return
        test_case, module_name = tokens
        allow_append = self._save_armed == (test_case, module_name)
        try:
            result = self.controller.save(
                test_case, module_name, allow_append=allow_append
            )
        except SaveConflictError as conflict:
            self._save_armed = (test_case, module_name)
            existing = ", ".join(f"{kind} '{name}'" for kind, name in conflict.conflicts)
            self._info(
                f"{existing} already exist(s). Re-run the same /save to append, "
                "or run /save with a different name."
            )
            return
        except Exception as exc:  # noqa: BLE001
            self._save_armed = None
            self._info(f"Save failed: {exc}")
            return
        self._save_armed = None
        import os as _os  # local: avoid widening module imports for one count
        verb = "Appended" if (result.appended_module or result.appended_test_case) else "Saved"
        self._info(
            f"{verb} {result.step_count} step(s) → module '{result.module_name}' in "
            f"{result.modules_path}, test case '{result.test_case}' in {result.test_cases_path}"
        )
        if result.artifacts_path:
            count = len(_os.listdir(result.artifacts_path))
            self._info(f"Snapshotted {count} artifact(s) → {result.artifacts_path}")

    def _cmd_device(self, arg: str) -> None:
        if not self.controller.supports_device_switching():
            self._info(
                f"Device switching is available for Appium sessions only — this session "
                f"uses {self.controller.driver_type}."
            )
            return
        if arg:
            self._switch_device(arg)
            return
        devices = self.controller.list_devices()  # list of (udid, platform)
        if not devices:
            self._info("No connected devices found (adb / idevice_id).")
            return
        active = self.controller.active_target()
        items: List[Tuple[str, str]] = []
        for udid, platform in devices:
            label = f"{udid}  ({platform})"
            if active.endswith(udid):
                label += "  (active)"
            items.append((label, udid))
        self.open_overlay("Select device", items, self._switch_device)
        get_app().invalidate()

    def _switch_device(self, serial: str) -> None:
        self._info(f"Switching to device {serial}…")
        try:
            self.controller.switch_device(serial)
        except Exception as exc:  # noqa: BLE001
            self._info(f"Device switch failed: {exc}")
            return
        self._info(f"Active device is now {serial}")

    def _cmd_elements(self, arg: str) -> None:
        names = self.controller.element_names()
        if not names:
            self.open_popup("Elements", " No named elements found in this project. ")
            get_app().invalidate()
            return
        lines = ["Named elements (read-only):", ""]
        for name in names:
            locator = self.controller.element_first_locator(name) or ""
            lines.append(f"  {name}")
            if locator:
                lines.append(f"      → {locator}")
        lines.append("")
        lines.append("Press Esc to close.")
        self.open_popup("Elements", "\n".join(lines))
        get_app().invalidate()

    def _cmd_screenshot(self, arg: str) -> None:
        if self._busy:
            self._info("Busy — wait for the current action to finish.")
            return
        self._busy = True
        app = get_app()
        pending = ActionResult(raw="/screenshot", status=ActionStatus.RUNNING)
        self.entries.append(pending)

        async def task() -> None:
            loop = asyncio.get_event_loop()
            try:
                path = await loop.run_in_executor(None, self.controller.capture_screenshot)
                pending.status = ActionStatus.INFO
                pending.message = f"Screenshot saved → {path}"
            except Exception as exc:  # noqa: BLE001
                pending.status = ActionStatus.FAIL
                pending.message = f"Screenshot failed: {exc}"
            finally:
                self._busy = False
                app.invalidate()

        app.create_background_task(task())
        app.invalidate()

    def _cmd_help(self, arg: str) -> None:
        self.open_popup("Help", _HELP_TEXT)
        get_app().invalidate()

    def _cmd_quit(self, arg: str) -> None:
        self._request_quit()

    def _request_quit(self) -> None:
        if not self.controller.saved and self.controller.recorded and not self._quit_armed:
            self._quit_armed = True
            self._info(
                f"{len(self.controller.recorded)} recorded step(s) are unsaved. "
                "Use /save <test_case> <module_name> to keep them, or /quit again to "
                "discard and exit."
            )
            return
        get_app().exit()

    # -- Keyword browser ----------------------------------------------------------

    def _open_keyword_browser(self) -> None:
        items = [(kw, kw) for kw in self.controller.keyword_names()]
        self.open_overlay("Keywords (Enter to pick)", items, self._pick_keyword)
        get_app().invalidate()

    def _pick_keyword(self, keyword: str) -> None:
        self.input_buffer.text = keyword + " "
        self.input_buffer.cursor_position = len(self.input_buffer.text)

    # -- Run ----------------------------------------------------------------------

    async def _auto_init_device(self, serial: str, loop, app) -> None:
        """Switch to a freshly connected device and launch the app (best effort)."""
        if self._busy:
            self._info(
                f"New device detected: {serial} — run /device to switch when ready"
            )
            app.invalidate()
            return
        self._info(f"New device connected: {serial} — initializing…")
        app.invalidate()
        try:
            await loop.run_in_executor(None, self.controller.switch_device, serial)
            self._info(f"Active device: {serial}")
            self._run_keyword_async("launch_app")
        except Exception as exc:  # noqa: BLE001 - reported to the user and never crashes
            self._info(f"Auto-init for {serial} failed: {exc}")
        app.invalidate()

    def _start_device_monitor(self) -> None:
        """Poll adb every 3 s; auto-initialize any newly connected device."""
        app = get_app()

        async def _poll_devices(loop) -> Optional[List[str]]:
            try:
                return await loop.run_in_executor(None, self.controller.list_android_devices)
            except Exception:  # noqa: BLE001 - transient adb hiccup retried next tick
                return None

        async def _monitor() -> None:
            loop = asyncio.get_running_loop()
            self._known_devices = await _poll_devices(loop) or []
            while True:
                await asyncio.sleep(3)
                devices = await _poll_devices(loop)
                if devices is None:
                    continue
                new_serials = [d for d in devices if d not in self._known_devices]
                self._known_devices = list(devices)
                for serial in new_serials:
                    await self._auto_init_device(serial, loop, app)

        app.create_background_task(_monitor())

    def _on_startup(self) -> None:
        """Show where the session log is going, then open the configured session."""
        log_path = getattr(self.controller, "live_log_path", None)
        if log_path:
            self._info(f"Session log: {log_path}")
        self._run_keyword_async("launch_app")
        # adb hot-plug auto-init only applies to Android Appium sessions; iOS devices
        # are still listed/switchable via /device.
        if self.controller.supports_adb_hotplug():
            self._start_device_monitor()

    def run(self) -> None:
        self.app.run(pre_run=self._on_startup)
