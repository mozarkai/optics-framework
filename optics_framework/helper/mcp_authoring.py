"""Interaction recording and suite authoring/storage for the Optics MCP server.

This is the "session as reusable artifact" layer. It has two halves:

- **Recording** — ``RecordedStep`` / ``SessionRecorder`` / ``RecorderRegistry``
  capture the action keywords an agent runs against a live session, so a casual
  exploration can be cherry-picked (drop the mis-fires, keep the real steps) into
  a replayable module. This mirrors the recording buffer of ``optics live`` but
  lives in-process, keyed by ``session_id``.

- **Storage** — ``SuiteStore`` reads and writes the canonical optics CSV project
  layout (``modules/modules.csv``, ``test_cases/test_cases.csv``,
  ``elements/elements.csv``) plus an MCP-side ``suites.json`` index. A suite built
  through the MCP therefore *is* an ``optics execute``-able project, and existing
  projects can be imported back for replay/editing.

Parameterization uses the framework's own ``${var}`` convention: at save time an
arg equal to a variable's default becomes ``${var}`` and the default is written to
``elements.csv``; at replay time ``${var}`` is resolved against the caller's params
(falling back to the stored default), so a module authored for one run replays with
new values. Nothing here imports fastmcp — ``helper/mcp_server.py`` drives it.
"""

from __future__ import annotations

import csv
import json
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from optics_framework.common.utils import escape_csv_value, unescape_csv_value

# CSV layout (canonical optics project — matches helper/live.py's /save and the
# batch runner's find_files content sniffing).
_MODULES_HEADER = ["module_name", "module_step"]
_TEST_CASES_HEADER = ["test_case", "test_step"]  # test_step holds the module name
_ELEMENTS_HEADER = ["Element_Name", "Element_ID"]

_MODULES_REL = os.path.join("modules", "modules.csv")
_TEST_CASES_REL = os.path.join("test_cases", "test_cases.csv")
_ELEMENTS_REL = os.path.join("test_data", "elements.csv")
_SUITES_REL = os.path.join(".optics_mcp", "suites.json")

_VAR_RE = re.compile(r"\$\{([^}]+)\}")
_NAME_RE = re.compile(r"[^A-Za-z0-9_ -]")

# Observer/setup keywords that never belong in a replayable recording.
NON_RECORDED_KEYWORDS = frozenset(
    {
        "capture_screenshot",
        "capture_pagesource",
        "get_interactive_elements",
        "get_screen_elements",
        "get_text",
        "get_driver_session_id",
        "get_app_version",
        "screenshot",
        "pagesource",
        "initialise_setup",
        "start_appium_session",
    }
)


class SuiteConflictError(Exception):
    """Raised when saving would overwrite an existing test case/module without opt-in."""


class SuiteNotFoundError(Exception):
    """Raised when a named test case or suite does not exist in the store."""


def keyword_to_title(slug: str) -> str:
    """``press_element`` -> ``Press Element`` (the modules.csv display form)."""
    return " ".join(word.capitalize() for word in slug.split("_"))


def title_to_slug(title: str) -> str:
    """``Press Element`` -> ``press_element`` (the keyword_map lookup key)."""
    return "_".join(title.split()).lower()


def sanitize_name(name: str) -> str:
    """Collapse a name to the CSV-safe ``[A-Za-z0-9_ -]`` set (mirrors live.py)."""
    return _NAME_RE.sub("", name or "").strip()


def format_step_params(
    param_specs: list[tuple[str, Optional[str]]], values: dict[str, str]
) -> list[str]:
    """Render provided keyword args as modules.csv/replay tokens, in signature order.

    ``param_specs`` is ``(name, default_str)`` per keyword param in signature order,
    where ``default_str`` is ``None`` for a required (no-default) parameter and the
    stringified default otherwise. A required param becomes a bare positional value.
    A defaulted param is emitted as ``name=value`` (so the runner's
    ``split_params_by_signature`` routes it back to a keyword on replay, e.g.
    ``index=2``) **only when the caller changed it** — the MCP boundary forwards
    every default, so a value equal to its default is dropped to keep the recorded
    step minimal.
    """
    tokens: list[str] = []
    for name, default_str in param_specs:
        if name not in values:
            continue
        value = values[name]
        if default_str is None:
            tokens.append(value)
        elif value != default_str:
            tokens.append(f"{name}={value}")
    return tokens


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #
@dataclass
class RecordedStep:
    """One recorded keyword call: its slug and modules.csv-ready arg tokens."""

    keyword: str
    params: list[str] = field(default_factory=list)

    @property
    def display(self) -> str:
        return keyword_to_title(self.keyword)

    def to_dict(self, index: Optional[int] = None) -> dict[str, Any]:
        row: dict[str, Any] = {
            "keyword": self.display,
            "slug": self.keyword,
            "params": list(self.params),
        }
        if index is not None:
            row["index"] = index
        return row


class SessionRecorder:
    """The recording buffer for a single session (active flag + ordered steps)."""

    def __init__(self) -> None:
        self.active = False
        self.steps: list[RecordedStep] = []

    def start(self, reset: bool = True) -> None:
        if reset:
            self.steps = []
        self.active = True

    def stop(self) -> None:
        self.active = False

    def clear(self) -> None:
        self.steps = []

    def append(self, step: RecordedStep) -> None:
        self.steps.append(step)

    def edit(self, index: int, keyword: Optional[str], params: Optional[list[str]]) -> RecordedStep:
        step = self._require(index)
        if keyword is not None:
            step.keyword = title_to_slug(keyword)
        if params is not None:
            step.params = list(params)
        return step

    def remove(self, index: int) -> RecordedStep:
        self._require(index)
        return self.steps.pop(index)

    def _require(self, index: int) -> RecordedStep:
        if index < 0 or index >= len(self.steps):
            raise IndexError(f"step index {index} out of range (0..{len(self.steps) - 1})")
        return self.steps[index]

    def status(self) -> dict[str, Any]:
        return {
            "recording": self.active,
            "step_count": len(self.steps),
            "steps": [s.to_dict(i) for i, s in enumerate(self.steps)],
        }


class RecorderRegistry:
    """Thread-safe registry of per-session recorders."""

    def __init__(self) -> None:
        self._recorders: dict[str, SessionRecorder] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> SessionRecorder:
        with self._lock:
            return self._recorders.setdefault(session_id, SessionRecorder())

    def peek(self, session_id: str) -> Optional[SessionRecorder]:
        """Return an existing recorder without creating one (for status reads)."""
        with self._lock:
            return self._recorders.get(session_id)

    def record(self, session_id: str, step: RecordedStep) -> None:
        """Append a step iff a recording is active for this session (else no-op)."""
        with self._lock:
            recorder = self._recorders.get(session_id)
        if recorder is not None and recorder.active:
            recorder.append(step)

    def discard(self, session_id: str) -> None:
        """Forget a session's recorder (call on session termination)."""
        with self._lock:
            self._recorders.pop(session_id, None)


# Module-level singleton shared by the MCP tool layer.
RECORDERS = RecorderRegistry()


# --------------------------------------------------------------------------- #
# Parameterization helpers
# --------------------------------------------------------------------------- #
def apply_variables(params: list[str], value_to_var: dict[str, str]) -> list[str]:
    """Replace any arg token exactly equal to a variable's default with ``${var}``."""
    return [f"${{{value_to_var[p]}}}" if p in value_to_var else p for p in params]


def resolve_variables(params: list[str], values: dict[str, str]) -> list[str]:
    """Substitute ``${name}`` occurrences in each token from ``values``.

    Works on whole tokens (``${Element}``) and substrings (``text=${msg}``). A
    ``${name}`` with no value raises ``KeyError`` so replay fails loudly rather
    than sending a literal ``${name}`` to the device.
    """

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise KeyError(name)
        return values[name]

    return [_VAR_RE.sub(repl, token) for token in params]


def referenced_variables(params: list[str]) -> list[str]:
    """Distinct ``${name}`` variable names referenced across arg tokens (in order)."""
    seen: list[str] = []
    for token in params:
        for name in _VAR_RE.findall(token):
            if name not in seen:
                seen.append(name)
    return seen


# --------------------------------------------------------------------------- #
# Suite storage (canonical CSV project layout)
# --------------------------------------------------------------------------- #
class SuiteStore:
    """Read/write a canonical optics CSV project rooted at ``root``.

    All writes are full-file rewrites under a shared class-level lock, so
    concurrent MCP calls do not clobber each other even though the tool layer
    constructs a fresh ``SuiteStore`` per call (the store is stateless between
    calls — it reads current CSVs each time).
    """

    # Class-level so every SuiteStore instance (one per tool call) shares it —
    # a per-instance lock would not serialize the read-modify-rewrite at all.
    _lock = threading.Lock()

    def __init__(self, root: str) -> None:
        self.root = root
        self.modules_path = os.path.join(root, _MODULES_REL)
        self.test_cases_path = os.path.join(root, _TEST_CASES_REL)
        self.elements_path = os.path.join(root, _ELEMENTS_REL)
        self.suites_path = os.path.join(root, _SUITES_REL)

    # -- low-level CSV ------------------------------------------------------ #
    @staticmethod
    def _read_rows(path: str) -> list[dict[str, str]]:
        if not os.path.isfile(path):
            return []
        with open(path, newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    @staticmethod
    def _write_rows(path: str, header: list[str], rows: list[dict[str, str]]) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: (row.get(key) or "") for key in header})

    @staticmethod
    def _module_rows_to_header(rows: list[dict[str, str]]) -> list[str]:
        max_params = 0
        for row in rows:
            for key, value in row.items():
                if key.startswith("param_") and value and key[len("param_"):].isdigit():
                    max_params = max(max_params, int(key[len("param_"):]))
        return _MODULES_HEADER + [f"param_{i}" for i in range(1, max_params + 1)]

    # -- introspection ------------------------------------------------------ #
    def _existing_names(self, path: str, column: str) -> set[str]:
        return {(row.get(column) or "").strip() for row in self._read_rows(path)}

    def _module_steps(self, module_name: str) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        for row in self._read_rows(self.modules_path):
            if (row.get("module_name") or "").strip() != module_name:
                continue
            params = []
            index = 1
            while True:
                key = f"param_{index}"
                if key not in row:
                    break
                value = row.get(key)
                if value:
                    # Mirror the runner (CSVDataReader.read_modules): params are
                    # written escaped and read back unescaped + stripped, so a value
                    # with a backslash/newline replays identically here and under
                    # `optics execute`.
                    params.append(unescape_csv_value(str(value).strip()))
                index += 1
            steps.append({"keyword": (row.get("module_step") or "").strip(), "params": params})
        return steps

    def _elements(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for row in self._read_rows(self.elements_path):
            name = (row.get("Element_Name") or "").strip()
            if name and name not in values:
                # Same escape/unescape contract as the runner's read_elements.
                values[name] = unescape_csv_value(str(row.get("Element_ID") or "").strip())
        return values

    # -- public API --------------------------------------------------------- #
    def save_test_case(
        self,
        name: str,
        steps: list[RecordedStep],
        variables: Optional[dict[str, str]] = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Persist ``steps`` as a module + test case named ``name``.

        One module per test case (``module_name == test_case == name``). ``variables``
        maps a variable name to the default value it should replace in the recorded
        args; matched args become ``${var}`` and the defaults are written to
        ``elements.csv`` so the module both replays here and runs under ``optics``.
        """
        clean = sanitize_name(name)
        if not clean:
            raise ValueError(f"Invalid name: {name!r}")
        if not steps:
            raise ValueError("No steps to save (record some actions first)")
        variables = variables or {}
        value_to_var = {default: var for var, default in variables.items()}

        with self._lock:
            existing_modules = self._existing_names(self.modules_path, "module_name")
            existing_cases = self._existing_names(self.test_cases_path, "test_case")
            if not overwrite and (clean in existing_modules or clean in existing_cases):
                raise SuiteConflictError(
                    f"Test case {clean!r} already exists; pass overwrite=true to replace it."
                )
            if overwrite:
                self._delete_locked(clean)

            module_rows = self._read_rows(self.modules_path)
            for step in steps:
                tokens = apply_variables(list(step.params), value_to_var)
                row: dict[str, str] = {
                    "module_name": clean,
                    "module_step": step.display,
                }
                for i, token in enumerate(tokens, start=1):
                    row[f"param_{i}"] = escape_csv_value(token)
                module_rows.append(row)
            self._write_rows(self.modules_path, self._module_rows_to_header(module_rows), module_rows)

            case_rows = self._read_rows(self.test_cases_path)
            pairs = {
                ((r.get("test_case") or "").strip(), (r.get("test_step") or "").strip())
                for r in case_rows
            }
            if (clean, clean) not in pairs:
                case_rows.append({"test_case": clean, "test_step": clean})
            self._write_rows(self.test_cases_path, _TEST_CASES_HEADER, case_rows)

            if variables:
                self._write_variable_defaults(variables)

        return {
            "test_case": clean,
            "module": clean,
            "step_count": len(steps),
            "variables": dict(variables),
            "root": self.root,
            "modules_path": self.modules_path,
            "test_cases_path": self.test_cases_path,
        }

    def _write_variable_defaults(self, variables: dict[str, str]) -> None:
        rows = self._read_rows(self.elements_path)
        existing = {(r.get("Element_Name") or "").strip().lower() for r in rows}
        for var, default in variables.items():
            if var.strip().lower() not in existing:
                rows.append(
                    {"Element_Name": escape_csv_value(var), "Element_ID": escape_csv_value(default)}
                )
        self._write_rows(self.elements_path, _ELEMENTS_HEADER, rows)

    def list_test_cases(self) -> list[dict[str, Any]]:
        cases: dict[str, list[str]] = {}
        for row in self._read_rows(self.test_cases_path):
            name = (row.get("test_case") or "").strip()
            module = (row.get("test_step") or "").strip()
            if not name:
                continue
            cases.setdefault(name, [])
            if module and module not in cases[name]:
                cases[name].append(module)
        result = []
        for name, modules in cases.items():
            step_count = sum(len(self._module_steps(m)) for m in modules)
            result.append({"test_case": name, "modules": modules, "step_count": step_count})
        return result

    def get_test_case(self, name: str) -> dict[str, Any]:
        clean = sanitize_name(name)
        modules = [
            (row.get("test_step") or "").strip()
            for row in self._read_rows(self.test_cases_path)
            if (row.get("test_case") or "").strip() == clean
        ]
        if not modules:
            raise SuiteNotFoundError(f"Test case {name!r} not found")
        elements = self._elements()
        steps: list[dict[str, Any]] = []
        variables: list[str] = []
        for module in modules:
            for step in self._module_steps(module):
                steps.append(step)
                for var in referenced_variables(step["params"]):
                    if var not in variables:
                        variables.append(var)
        return {
            "test_case": clean,
            "modules": modules,
            "steps": steps,
            "variables": {var: elements.get(var, "") for var in variables},
        }

    def resolve_steps(self, name: str, params: Optional[dict[str, str]] = None) -> list[tuple[str, list[str]]]:
        """Load a test case's steps as ``(slug, resolved_args)`` ready for replay.

        ``${var}`` tokens are resolved against ``params`` first, then the stored
        ``elements.csv`` defaults.
        """
        info = self.get_test_case(name)
        values = {**self._elements(), **(params or {})}
        resolved: list[tuple[str, list[str]]] = []
        for step in info["steps"]:
            slug = title_to_slug(step["keyword"])
            resolved.append((slug, resolve_variables(step["params"], values)))
        return resolved

    def delete_test_case(self, name: str) -> dict[str, Any]:
        clean = sanitize_name(name)
        with self._lock:
            if clean not in self._existing_names(self.test_cases_path, "test_case"):
                raise SuiteNotFoundError(f"Test case {name!r} not found")
            removed = self._delete_locked(clean)
        return {"test_case": clean, **removed}

    def _delete_locked(self, clean: str) -> dict[str, int]:
        """Remove a test case, its same-named module rows, and stale suite refs.

        Caller must hold ``self._lock``.
        """
        case_rows = self._read_rows(self.test_cases_path)
        kept_cases = [r for r in case_rows if (r.get("test_case") or "").strip() != clean]
        self._write_rows(self.test_cases_path, _TEST_CASES_HEADER, kept_cases)

        module_rows = self._read_rows(self.modules_path)
        kept_modules = [r for r in module_rows if (r.get("module_name") or "").strip() != clean]
        self._write_rows(
            self.modules_path, self._module_rows_to_header(kept_modules), kept_modules
        )

        suites = self._read_suites()
        changed = False
        for suite_name, members in list(suites.items()):
            filtered = [m for m in members if m != clean]
            if filtered != members:
                suites[suite_name] = filtered
                changed = True
        if changed:
            self._write_suites(suites)
        return {
            "removed_test_case_rows": len(case_rows) - len(kept_cases),
            "removed_module_rows": len(module_rows) - len(kept_modules),
        }

    # -- suites (MCP-side grouping, not a native optics concept) ------------ #
    def _read_suites(self) -> dict[str, list[str]]:
        if not os.path.isfile(self.suites_path):
            return {}
        try:
            with open(self.suites_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
        return {str(k): [str(v) for v in vals] for k, vals in data.items()} if isinstance(data, dict) else {}

    def _write_suites(self, suites: dict[str, list[str]]) -> None:
        os.makedirs(os.path.dirname(self.suites_path), exist_ok=True)
        with open(self.suites_path, "w", encoding="utf-8") as handle:
            json.dump(suites, handle, indent=2, sort_keys=True)

    def save_suite(self, name: str, test_cases: list[str]) -> dict[str, Any]:
        clean = sanitize_name(name)
        if not clean:
            raise ValueError(f"Invalid suite name: {name!r}")
        known = self._existing_names(self.test_cases_path, "test_case")
        members = [sanitize_name(tc) for tc in test_cases]
        missing = [tc for tc in members if tc not in known]
        if missing:
            raise SuiteNotFoundError(f"Unknown test case(s): {', '.join(missing)}")
        with self._lock:
            suites = self._read_suites()
            suites[clean] = members
            self._write_suites(suites)
        return {"suite": clean, "test_cases": members}

    def list_suites(self) -> list[dict[str, Any]]:
        return [
            {"suite": name, "test_cases": members}
            for name, members in sorted(self._read_suites().items())
        ]

    def get_suite(self, name: str) -> dict[str, Any]:
        clean = sanitize_name(name)
        suites = self._read_suites()
        if clean not in suites:
            raise SuiteNotFoundError(f"Suite {name!r} not found")
        return {"suite": clean, "test_cases": suites[clean]}

    # -- portability -------------------------------------------------------- #
    def export_project(self, dest: str, config_yaml: str) -> dict[str, Any]:
        """Write a standalone ``optics execute``-able project to ``dest``.

        Copies the three CSVs into the conventional layout and writes ``config.yaml``.
        The MCP-side ``suites.json`` is intentionally not exported (suites are not a
        native runner concept).
        """
        os.makedirs(dest, exist_ok=True)
        written: list[str] = []
        for rel, header, path in (
            (_MODULES_REL, None, self.modules_path),
            (_TEST_CASES_REL, _TEST_CASES_HEADER, self.test_cases_path),
            (_ELEMENTS_REL, _ELEMENTS_HEADER, self.elements_path),
        ):
            rows = self._read_rows(path)
            if not rows:
                continue
            out_path = os.path.join(dest, rel)
            out_header = self._module_rows_to_header(rows) if header is None else header
            self._write_rows(out_path, out_header, rows)
            written.append(out_path)
        config_path = os.path.join(dest, "config.yaml")
        with open(config_path, "w", encoding="utf-8") as handle:
            handle.write(config_yaml)
        written.append(config_path)
        return {"dest": dest, "files": written}

    def import_project(self, source: str) -> dict[str, Any]:
        """Merge an existing optics project's CSVs into this store (append/dedup).

        Locates modules/test-cases/elements CSVs by column signature anywhere under
        ``source``, so the conventional and flat layouts both import.
        """
        found = _discover_project_csvs(source)
        if not any(found.values()):
            raise FileNotFoundError(f"No optics CSVs found under {source!r}")
        summary: dict[str, Any] = {"source": source}
        with self._lock:
            if found["modules"]:
                merged = self._read_rows(self.modules_path) + [
                    row for path in found["modules"] for row in self._read_rows(path)
                ]
                self._write_rows(self.modules_path, self._module_rows_to_header(merged), merged)
                summary["modules"] = found["modules"]
            if found["test_cases"]:
                rows = self._read_rows(self.test_cases_path)
                pairs = {
                    ((r.get("test_case") or "").strip(), (r.get("test_step") or "").strip())
                    for r in rows
                }
                for path in found["test_cases"]:
                    for row in self._read_rows(path):
                        key = ((row.get("test_case") or "").strip(), (row.get("test_step") or "").strip())
                        if key[0] and key not in pairs:
                            rows.append({"test_case": key[0], "test_step": key[1]})
                            pairs.add(key)
                self._write_rows(self.test_cases_path, _TEST_CASES_HEADER, rows)
                summary["test_cases"] = found["test_cases"]
            if found["elements"]:
                rows = self._read_rows(self.elements_path)
                existing = {(r.get("Element_Name") or "").strip().lower() for r in rows}
                for path in found["elements"]:
                    for row in _read_elements_rows(path):
                        name = (row.get("Element_Name") or "").strip()
                        if name and name.lower() not in existing:
                            rows.append(row)
                            existing.add(name.lower())
                self._write_rows(self.elements_path, _ELEMENTS_HEADER, rows)
                summary["elements"] = found["elements"]
        return summary


def _read_elements_rows(path: str) -> list[dict[str, str]]:
    """Read an elements CSV tolerantly (handles the ``Element_Name,Element_ID`` header)."""
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _csv_header(path: str) -> list[str]:
    try:
        with open(path, newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            return next(reader, [])
    except (OSError, csv.Error):
        return []


def _discover_project_csvs(source: str) -> dict[str, list[str]]:
    """Bucket CSVs under ``source`` by column signature: modules/test_cases/elements."""
    found: dict[str, list[str]] = {"modules": [], "test_cases": [], "elements": []}
    for dirpath, _dirs, files in os.walk(source):
        if os.path.basename(dirpath) == ".optics_mcp":
            continue
        for filename in files:
            if not filename.lower().endswith(".csv"):
                continue
            path = os.path.join(dirpath, filename)
            header = [h.strip() for h in _csv_header(path)]
            if "module_name" in header and "module_step" in header:
                found["modules"].append(path)
            elif "test_case" in header and "test_step" in header:
                found["test_cases"].append(path)
            elif "Element_Name" in header and "Element_ID" in header:
                found["elements"].append(path)
    for key in found:
        found[key].sort()
    return found
