"""Resting order book: fills-through, expiry, one-order-per-wallet."""

from decimal import Decimal

from tradebot.application.order_book import RestingBook, RestingOrder
from tradebot.domain.ledger import Side
from tradebot.domain.market import MarketSnapshot


def snap(i: int, hi: str, lo: str) -> MarketSnapshot:
    c = Decimal("60000")
    return MarketSnapshot(
        snapshot_id=f"s{i}", source="test", symbol="BTCUSDT", interval="5m",
        open_time_ms=i * 300_000, close_time_ms=(i + 1) * 300_000, is_closed=True,
        open=c, high=Decimal(hi), low=Decimal(lo), close=c, volume=Decimal("1"),
        retrieved_at_ms=(i + 1) * 300_000, source_time_ms=(i + 1) * 300_000,
    )


def buy_order(i: int, limit: str, wallet="w1", expiry=12) -> RestingOrder:
    return RestingOrder(
        order_id=f"o{i}", wallet_id=wallet, strategy_version_id="v1",
        side=Side.BUY, limit_price=Decimal(limit), quantity=Decimal("0.01"),
        reason_code="entry", placed_open_ms=i * 300_000,
        expires_after_candles=expiry)


def test_buy_limit_fills_only_when_candle_trades_through():
    book = RestingBook()
    book.rest(buy_order(0, "59500"))
    # Candle low 59600 never reaches 59500 -> not fillable, stays resting.
    assert book.due_fills(snap(1, "60100", "59600")) == []
    assert book.has_order("w1")
    # Next candle dips to 59400 -> fillable.
    due = book.due_fills(snap(2, "60000", "59400"))
    assert [o.order_id for o in due] == ["o0"]
    assert not book.has_order("w1")


def test_sell_limit_fills_on_high_through():
    book = RestingBook()
    book.rest(RestingOrder("o1", "w1", "v1", Side.SELL, Decimal("60500"),
                           Decimal("0.01"), "target", 0))
    assert book.due_fills(snap(1, "60400", "60000")) == []  # high below limit
    assert [o.order_id for o in book.due_fills(snap(2, "60600", "60000"))] == ["o1"]


def test_wallet_holds_a_ladder_of_resting_orders():
    book = RestingBook()
    book.rest(buy_order(0, "59500"))
    book.rest(buy_order(1, "59000"))
    assert book.count("w1") == 2
    prices = {r["limit_price"] for r in book.snapshot_open()["w1"]}
    assert prices == {"59500", "59000"}


def test_ladder_is_capped_dropping_the_oldest():
    book = RestingBook()
    for i in range(7):  # cap is 5
        book.rest(buy_order(i, str(59000 - i)))
    assert book.count("w1") == 5
    ids = {r["order_id"] for r in book.snapshot_open()["w1"]}
    assert ids == {"o2", "o3", "o4", "o5", "o6"}  # o0, o1 dropped


def test_at_most_one_fill_per_wallet_per_candle():
    book = RestingBook()
    book.rest(buy_order(0, "59500"))
    book.rest(buy_order(1, "59400"))
    # Candle dips through both limits, but only one may fill this candle.
    due = book.due_fills(snap(3, "60000", "59000"))
    assert len(due) == 1
    assert book.count("w1") == 1  # the other stays resting
    # Next candle the remaining one fills.
    assert len(book.due_fills(snap(4, "60000", "59000"))) == 1


def test_orders_expire_after_ttl_candles():
    book = RestingBook()
    book.observe_spacing(snap(1, "60100", "59900"), snap(0, "60100", "59900"))
    book.rest(buy_order(0, "50000", expiry=3))  # far below; never fills
    assert book.expire(snap(3, "60100", "59900")) == []   # age 3, not > 3
    expired = book.expire(snap(4, "60100", "59900"))       # age 4 > 3
    assert [o.order_id for o in expired] == ["o0"]
    assert not book.has_order("w1")


def test_snapshot_open_is_json_ready():
    book = RestingBook()
    book.rest(buy_order(0, "59500"))
    rows = book.snapshot_open()
    assert rows["w1"][0] == {
        "order_id": "o0", "side": "BUY", "order_type": "LIMIT",
        "limit_price": "59500", "quantity": "0.01",
        "reason_code": "entry", "status": "open",
    }
