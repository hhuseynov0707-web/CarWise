"""Executable enforcement of the layer boundaries (audit §8).

Layering that lives only in a document decays within weeks. These tests parse
every module's imports and fail the build when a boundary is crossed.

The direction that actually matters is **engines must not depend on adapters**.
That is what keeps the entire analytical core free of I/O, clocks, databases and
language models, which in turn is what makes ground-truth testing (see
``test_valuation.py``) and future model monitoring possible at all.

The reverse direction is fine and expected: an adapter serializing an analysis
must know the analysis types.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"

#: Top-level modules that are not a layer: settings and the composition root.
#: Deliberately absent from the domain and engine allowances — a pure engine
#: that can read settings is no longer pure, and its behaviour stops being
#: reproducible from its arguments alone.
ROOT_MODULES = {"config", "container", "main"}

#: layer -> app packages it may import from.
ALLOWED_IMPORTS: dict[str, set[str]] = {
    "domain": {"domain"},
    "engines": {"domain", "engines"},
    "adapters": {"domain", "engines", "adapters"},
    "schemas": {"domain", "engines", "schemas"},
    "db": {"domain", "db"},
    "services": {"domain", "engines", "adapters", "services", "db", "schemas", "config"},
    "api": {
        "domain", "engines", "adapters", "services", "db", "api", "schemas",
        "config", "container",
    },
}

#: Concrete adapter implementations. Nothing outside the adapter layer and the
#: composition root may import these by name — everything else depends on the
#: abstract port so the implementation stays swappable (spec §72).
CONCRETE_ADAPTERS = {
    "app.adapters.llm.grok",
    "app.adapters.market.turbo",
    "app.adapters.vin.nhtsa",
}

#: The composition root is the one place allowed to know concrete types, because
#: something has to decide which implementation to build.
COMPOSITION_ROOTS = {"app.container", "app.main"}


def _modules() -> list[pathlib.Path]:
    return sorted(
        path
        for path in APP_ROOT.rglob("*.py")
        if path.name != "__init__.py"
    )


def _module_name(path: pathlib.Path) -> str:
    relative = path.relative_to(APP_ROOT.parent).with_suffix("")
    return ".".join(relative.parts)


def _layer(path: pathlib.Path) -> str | None:
    relative = path.relative_to(APP_ROOT)
    return relative.parts[0] if len(relative.parts) > 1 else None


def _app_imports(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names if alias.name.startswith("app."))
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("app.") and node.level == 0:
                out.append(node.module)
    return out


ALL_MODULES = _modules()


@pytest.mark.parametrize("path", ALL_MODULES, ids=_module_name)
def test_module_respects_layer_boundaries(path: pathlib.Path) -> None:
    layer = _layer(path)
    if layer is None or layer not in ALLOWED_IMPORTS:
        return

    allowed = ALLOWED_IMPORTS[layer]
    for imported in _app_imports(path):
        parts = imported.split(".")
        if len(parts) < 2:
            continue
        target_layer = parts[1]
        assert target_layer in allowed, (
            f"{_module_name(path)} (layer '{layer}') imports {imported} "
            f"(layer '{target_layer}'); allowed layers are {sorted(allowed)}"
        )


@pytest.mark.parametrize("path", ALL_MODULES, ids=_module_name)
def test_only_the_composition_root_names_concrete_adapters(path: pathlib.Path) -> None:
    """Engines and services depend on ports, never on implementations."""
    module = _module_name(path)
    if module in COMPOSITION_ROOTS or module in CONCRETE_ADAPTERS:
        return
    if module.startswith("app.adapters."):
        return  # an adapter package may wire its own internals

    for imported in _app_imports(path):
        assert imported not in CONCRETE_ADAPTERS, (
            f"{module} imports the concrete adapter {imported}; depend on the "
            f"abstract port instead so the implementation stays replaceable"
        )


@pytest.mark.parametrize(
    "path",
    [p for p in ALL_MODULES if _layer(p) in ("domain", "engines")],
    ids=_module_name,
)
def test_analytical_core_performs_no_io(path: pathlib.Path) -> None:
    """The domain and engine layers must have no I/O, no clock, no randomness.

    Every one of these would make results non-deterministic and therefore
    untestable against a known ground truth. ``datetime`` is importable — the
    engines take instants as parameters — but calling ``datetime.now()`` inside
    an engine is the thing being prevented.
    """
    forbidden_modules = {
        "httpx", "requests", "aiohttp", "urllib", "socket",
        "sqlalchemy", "psycopg", "redis", "asyncpg",
        "openai", "anthropic",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in forbidden_modules, (
                    f"{_module_name(path)} imports {alias.name}; the analytical core "
                    f"must stay free of I/O"
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            assert root not in forbidden_modules, (
                f"{_module_name(path)} imports from {node.module}; the analytical core "
                f"must stay free of I/O"
            )

    source = path.read_text(encoding="utf-8")
    for wall_clock in ("datetime.now(", "datetime.utcnow(", "time.time("):
        assert wall_clock not in source, (
            f"{_module_name(path)} reads the wall clock ({wall_clock}); engines must "
            f"receive the reference instant as a parameter so results are reproducible"
        )


def test_every_layer_has_at_least_one_module() -> None:
    """Guards against the boundary tests silently passing on an empty package."""
    populated = {_layer(path) for path in ALL_MODULES} - {None}
    for required in ("domain", "engines", "adapters", "services"):
        assert required in populated, f"layer '{required}' has no modules"


def test_no_circular_imports_between_layers() -> None:
    """Layer dependencies must form a DAG, not a cycle."""
    edges: set[tuple[str, str]] = set()
    for path in ALL_MODULES:
        layer = _layer(path)
        if layer is None:
            continue
        for imported in _app_imports(path):
            parts = imported.split(".")
            if len(parts) >= 2 and parts[1] != layer:
                edges.add((layer, parts[1]))

    for source, target in edges:
        assert (target, source) not in edges, (
            f"layers '{source}' and '{target}' import each other; the dependency "
            f"graph must stay acyclic"
        )
