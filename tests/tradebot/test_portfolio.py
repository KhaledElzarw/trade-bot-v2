import datetime as dt
from decimal import Decimal

import pytest

from tradebot.application.portfolio import (
    DARK_HORSE_DAILY_DISPLAY_NAME,
    DARK_HORSE_DISPLAY_NAME,
    display_name,
    seed_portfolio,
)
from tradebot.strategies.builtin import BUILTIN_STRATEGIES

NOW = dt.datetime(2026, 7, 17, 0, 0, 0)
NAMES = [cls().metadata().name for cls in BUILTIN_STRATEGIES]


def _seed():
    counter = iter(range(1000))
    return seed_portfolio(NAMES, now=NOW,
                          id_factory=lambda hint: f"{hint}-{next(counter):04d}")


def test_seed_counts_and_balances():
    p = _seed()
    assert len(p.active) == 12
    assert len(p.shadow) == 12
    assert p.dark_horse is not None
    assert p.dark_horse_daily is not None
    for slot in p.active + p.shadow + [p.dark_horse, p.dark_horse_daily]:
        assert slot.wallet.quote_cash == Decimal("10000.00")
        assert slot.wallet.base_qty == 0


def test_active_baseline_is_140k_and_shadow_separate():
    p = _seed()
    mark = Decimal("60000")
    # 12 active + Dark Horse + Darkhorse - Daily
    assert p.active_equity(mark) == Decimal("140000.00")
    assert p.shadow_equity(mark) == Decimal("120000.00")  # never mixed in


def test_wallet_ids_are_unique_and_stable():
    p = _seed()
    ids = [s.wallet.wallet_id
           for s in p.active + p.shadow + [p.dark_horse, p.dark_horse_daily]]
    assert len(set(ids)) == 26


def test_display_naming_rule():
    p = _seed()
    slot = p.active[0]
    assert display_name(slot, NOW) == f"{slot.strategy_name}_0"
    later = NOW + dt.timedelta(days=9, hours=5)
    assert display_name(slot, later) == f"{slot.strategy_name}_9"
    assert display_name(p.dark_horse, later) == DARK_HORSE_DISPLAY_NAME
    assert display_name(p.dark_horse_daily, later) == DARK_HORSE_DAILY_DISPLAY_NAME


def test_seed_rejects_wrong_or_duplicate_names():
    with pytest.raises(ValueError):
        seed_portfolio(NAMES[:11], now=NOW, id_factory=str)
    with pytest.raises(ValueError):
        seed_portfolio([NAMES[0]] * 12, now=NOW, id_factory=str)
