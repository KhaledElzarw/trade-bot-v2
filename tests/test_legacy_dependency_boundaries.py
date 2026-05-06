import ast
from pathlib import Path

import dashboard_orchestrator


REPO_ROOT = Path(__file__).resolve().parents[1]

# gridMode=flexy is intentionally omitted; it remains active behavior for a later branch.
DEPRECATED_MODULES = set()
REMOVED_LEGACY_MODULES = {
    "advisor",
    "engine_trend",
    "grid_engine_honest",
    "grid_engine_honest_v2",
}
FORBIDDEN_LEGACY_MODULES = DEPRECATED_MODULES | REMOVED_LEGACY_MODULES
FORBIDDEN_LEGACY_SERVICE_MARKERS = tuple(sorted(FORBIDDEN_LEGACY_MODULES))

CORE_PYTHON_FILES = {
    "ai_sidecar.py",
    "dashboard_contracts.py",
    "dashboard_data.py",
    "dashboard_orchestrator.py",
    "dashboard_routes.py",
    "dashboard_server.py",
    "engine.py",
    "migrate_to_sqlite.py",
    "run_ai_sidecar_detached.py",
    "run_dashboard_detached.py",
    "run_engine_detached.py",
    "sqlite_store.py",
    "wrapper_runner.py",
}


def _forbidden_legacy_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in FORBIDDEN_LEGACY_MODULES:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.lineno} imports {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root in FORBIDDEN_LEGACY_MODULES:
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno} imports from {node.module}"
                )

    return offenders


def test_core_python_modules_do_not_import_deprecated_or_removed_legacy_modules():
    offenders = []

    for relative_path in sorted(CORE_PYTHON_FILES):
        offenders.extend(_forbidden_legacy_imports(REPO_ROOT / relative_path))

    assert offenders == [], "Forbidden legacy imports found in core files: " + ", ".join(offenders)


def test_dashboard_orchestrator_services_do_not_reference_deprecated_or_removed_workflows():
    offenders = []

    for service, command in dashboard_orchestrator.SERVICES.items():
        service_text = f"{service} {Path(command).name}"
        for marker in FORBIDDEN_LEGACY_SERVICE_MARKERS:
            if marker in service_text:
                offenders.append(f"{service}: {Path(command).name} references {marker}")

    assert offenders == [], "Forbidden legacy services referenced by orchestrator: " + ", ".join(offenders)
