import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_EXACT_PATHS = {
    ".env",
    ".env.grid",
    ".env.local",
    ".env.trend",
    "dashboard_intelligence.json",
}

FORBIDDEN_SUFFIXES = (
    ".sqlite3",
    ".sqlite3-wal",
    ".sqlite3-shm",
    ".log",
    ".pid",
    ".nohup.out",
)

FORBIDDEN_PATH_PARTS = {
    "__pycache__",
    "ai_agents",
    ".pytest_cache",
    ".venv",
}


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _has_forbidden_path_part(path: str) -> bool:
    return any(part in FORBIDDEN_PATH_PARTS for part in Path(path).parts)


def test_runtime_artifacts_and_local_secrets_are_not_tracked():
    tracked_files = _tracked_files()

    offenders = sorted(
        path
        for path in tracked_files
        if path in FORBIDDEN_EXACT_PATHS
        or path.endswith(FORBIDDEN_SUFFIXES)
        or _has_forbidden_path_part(path)
    )

    assert offenders == [], "Tracked local-only files must be removed: " + ", ".join(offenders)
