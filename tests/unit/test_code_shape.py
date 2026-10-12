"""Checks on the shape of the backend source: the layering of CLAUDE.md §4, file size."""
import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
CORE = BACKEND / "core"

MAX_FILE_LINES = 400
# Files allowed over the limit, each until the plan that splits it.
OVERSIZED = {"backend/core/orchestrator/orchestrator.py"}

OUTSIDE_CORE = ("backend.providers", "backend.runtime", "backend.features")
ORCHESTRATOR = ("backend.core.orchestrator",)


def imported_modules(source: str, package: str = "") -> set[str]:
    """Every module a source imports, as absolute names: both forms, at any nesting
    level. `package` is the dotted package of the file, which relative imports start from."""
    modules = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            parts = [node.module] if node.module else []
            if node.level:  # one dot is the package itself, each further dot its parent
                base = package.split(".")
                parts = base[: len(base) - (node.level - 1)] + parts
            module = ".".join(parts)
            modules.add(module)
            # `from backend import providers` imports backend.providers.
            modules.update(f"{module}.{alias.name}" for alias in node.names)
    return modules


def forbidden_imports(folder: Path, forbidden: tuple[str, ...]) -> list[str]:
    found = []
    for path in sorted(folder.rglob("*.py")):
        package = ".".join(path.relative_to(REPO_ROOT).parent.parts)
        for module in sorted(imported_modules(path.read_text(encoding="utf-8"), package)):
            if any(module == name or module.startswith(name + ".") for name in forbidden):
                found.append(f"{path.relative_to(REPO_ROOT).as_posix()} imports {module}")
    return found


def line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_the_import_scan_sees_both_forms_at_any_level():
    source = (
        "import json\n"
        "from backend.core import contracts\n"
        "def build():\n"
        "    import backend.providers.catalog\n"
        "    from backend.features.knowledge.store import SqliteVecStore\n"
    )

    modules = imported_modules(source)

    assert {"json", "backend.core.contracts"} <= modules
    assert {"backend.providers.catalog", "backend.features.knowledge.store"} <= modules


@pytest.mark.parametrize(
    "source", ["from ..orchestrator import state\n", "from .. import orchestrator\n"]
)
def test_the_import_scan_resolves_relative_imports(source):
    modules = imported_modules(source, package="backend.core.perception")

    assert "backend.core.orchestrator" in modules


@pytest.mark.parametrize(
    "folder, forbidden",
    [
        (CORE, OUTSIDE_CORE),
        (CORE / "perception", ORCHESTRATOR),
        (CORE / "conversation", ORCHESTRATOR),
    ],
    ids=["core", "perception", "conversation"],
)
def test_the_core_keeps_its_layering(folder, forbidden):
    assert any(folder.rglob("*.py")), f"{folder} holds no source: the check reads nothing"

    assert forbidden_imports(folder, forbidden) == []


def test_no_backend_file_is_over_the_size_limit():
    lines = {
        path.relative_to(REPO_ROOT).as_posix(): line_count(path)
        for path in BACKEND.rglob("*.py")
    }
    too_long = {name: count for name, count in lines.items() if count > MAX_FILE_LINES}

    assert set(too_long) - OVERSIZED == set(), f"over {MAX_FILE_LINES} lines: {too_long}"
    # A listed file that was split or deleted leaves the list: no stale exception.
    assert OVERSIZED - set(too_long) == set(), "listed as oversized but within the limit"
