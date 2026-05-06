from collections import Counter
from pathlib import Path


ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"

REQUIRED_KEYS = {
    "BINANCE_BASE_URL",
    "BINANCE_MARKETDATA_URL",
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_SYMBOL",
    "TRADEBOT_DB_PATH",
    "TRADEBOT_BOT_KEY",
    "TRADEBOT_BOT_NAME",
    "TRADEBOT_ENGINE_HEARTBEAT_LOG_SECONDS",
    "TRADEBOT_DASHBOARD_HOST",
    "TRADEBOT_DASHBOARD_PORT",
    "TRADEBOT_DASHBOARD_TOKEN",
    "TRADEBOT_AI_BASE_URL",
    "TRADEBOT_AI_PROVIDER",
    "TRADEBOT_AI_MODEL",
}

SECRET_KEYS = {
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "TRADEBOT_DASHBOARD_TOKEN",
}

PLACEHOLDER_SAFE_VALUES = {
    "",
    "changeme",
    "change-me",
    "example",
    "placeholder",
    "<key>",
    "<secret>",
    "<token>",
}


def _env_example_entries() -> list[tuple[str, str]]:
    entries = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, value = stripped.split("=", 1)
        entries.append((key.strip(), value.strip().strip("\"'")))
    return entries


def test_env_example_has_no_duplicate_keys():
    keys = [key for key, _value in _env_example_entries()]
    counts = Counter(keys)
    duplicates = sorted(key for key, count in counts.items() if count > 1)

    assert duplicates == [], "Duplicate .env.example keys: " + ", ".join(duplicates)


def test_env_example_contains_required_keys():
    keys = {key for key, _value in _env_example_entries()}
    missing = sorted(REQUIRED_KEYS - keys)

    assert missing == [], "Missing .env.example keys: " + ", ".join(missing)


def test_env_example_secret_values_are_placeholder_safe():
    values = dict(_env_example_entries())
    unsafe = sorted(
        key
        for key in SECRET_KEYS
        if values.get(key, "").lower() not in PLACEHOLDER_SAFE_VALUES
    )

    assert unsafe == [], "Unsafe .env.example secret placeholders: " + ", ".join(unsafe)
