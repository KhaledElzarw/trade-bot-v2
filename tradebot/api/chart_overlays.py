"""Per-strategy chart overlays + trade markers for the wallet drill-down.

The price chart shows *the indicators the wallet's own strategy uses*. Rather
than push charting concerns into the signal code (the built-ins must stay pure
per the plugin contract), this dashboard-side module maps a strategy to the
overlay series it trades on, computed with the same deterministic functions in
``tradebot.strategies.indicators`` that the strategies themselves use — so the
overlay is faithful to the live parameters, not a hand-copied approximation.

Everything is data-driven off the wallet's *current* strategy name: if a wallet
is excluded or reassigned, the caller passes the new strategy name and the
overlays change with it. Unknown strategies (e.g. the committee wallets) simply
yield no indicator overlays — candles + trade markers still render.
"""

from __future__ import annotations

from decimal import Decimal

from ..domain.market import MarketSnapshot
from ..strategies import indicators as ind
from ..strategies.builtin import BUILTIN_STRATEGIES

# Colours chosen to read on the dark dashboard theme; kept explicit because
# Lightweight Charts needs a concrete colour per series.
C_BASIS = "#8a94a6"
C_BAND = "#5b8def"
C_FAST = "#f5a623"
C_SLOW = "#4a90d9"
C_VWAP = "#c56be6"
C_CHANNEL = "#54c17a"
C_STOP = "#e0653a"
C_OSC = "#5b8def"
C_OSC2 = "#f5a623"
C_THRESH = "#6b7280"


def _sec(candle: MarketSnapshot) -> int:
    return candle.close_time_ms // 1000


def _instance(name: str):
    """A built-in strategy instance by name, or None (committee/plugin wallets)."""

    for factory in BUILTIN_STRATEGIES:
        obj = factory()
        if obj.name == name:
            return obj
    return None


def _line(candles, valuefn, *, id: str, label: str, pane: str, color: str,
          start: int = 1) -> dict | None:
    """A line overlay from a rolling indicator ``valuefn(prefix) -> Decimal|None``.

    ``prefix`` is ``candles[:i+1]`` so each point uses only data available at that
    candle — no lookahead. Points where the indicator is undefined are skipped.
    """

    points = []
    for i in range(start - 1, len(candles)):
        v = valuefn(candles[: i + 1])
        if v is not None:
            points.append({"time": _sec(candles[i]), "value": float(v)})
    if not points:
        return None
    return {"id": id, "label": label, "pane": pane, "kind": "line",
            "color": color, "points": points}


def _threshold(id: str, label: str, pane: str, value, color: str = C_THRESH) -> dict:
    return {"id": id, "label": label, "pane": pane, "kind": "threshold",
            "color": color, "value": float(value)}


# -- per-strategy overlay builders -------------------------------------------
#
# Each builder reads the LIVE parameters off the strategy instance so the drawn
# indicator matches exactly what the wallet trades on.


def _bollinger(s, candles):
    p, m = s.z_period, s.z_period
    closes = lambda c: ind.closes(c)  # noqa: E731
    basis = lambda c: ind.sma(closes(c), p)  # noqa: E731

    def band(mult):
        def f(c):
            b = ind.sma(closes(c), p)
            sd = ind.stddev(closes(c), p)
            return None if b is None or sd is None else b + sd * mult
        return f

    out = [
        _line(candles, basis, id="bb_basis", label=f"SMA {p}", pane="price", color=C_BASIS),
        _line(candles, band(Decimal("2")), id="bb_up", label="+2σ", pane="price", color=C_BAND),
        _line(candles, band(Decimal("-2")), id="bb_dn", label="-2σ", pane="price", color=C_BAND),
        _line(candles, lambda c: ind.zscore(closes(c), m),
              id="zscore", label="z-score", pane="lower", color=C_OSC),
        _threshold("z_entry", f"entry {s.entry_z}", "lower", s.entry_z),
        _threshold("z_deep", f"deep {s.deep_z}", "lower", s.deep_z, "#e0653a"),
    ]
    return out


def _vwap(s, candles):
    return [
        _line(candles, lambda c: ind.rolling_vwap(c, s.vwap_period),
              id="vwap", label=f"VWAP {s.vwap_period}", pane="price", color=C_VWAP),
    ]


def _oscillator(s, candles):
    return [
        _line(candles, lambda c: ind.rsi(ind.closes(c), s.rsi_period),
              id="rsi", label=f"RSI {s.rsi_period}", pane="lower", color=C_OSC),
        _line(candles, lambda c: ind.stochastic_k(c, s.stoch_period),
              id="stoch", label=f"%K {s.stoch_period}", pane="lower", color=C_OSC2),
        _threshold("rsi_os", f"oversold {s.rsi_oversold}", "lower", s.rsi_oversold),
        _threshold("rsi_ob", f"exit {s.rsi_exit}", "lower", s.rsi_exit),
    ]


def _donchian(s, candles):
    return [
        _line(candles, lambda c: ind.donchian_high(c, s.entry_period, exclude_last=False),
              id="dc_up", label=f"Donchian {s.entry_period} high", pane="price", color=C_CHANNEL),
        _line(candles, lambda c: ind.donchian_low(c, s.exit_period, exclude_last=False),
              id="dc_dn", label=f"Donchian {s.exit_period} low", pane="price", color=C_STOP),
    ]


def _ema_pullback(s, candles):
    def ema_last(period):
        return lambda c: ind.ema(ind.closes(c), period)
    return [
        _line(candles, ema_last(s.fast), id="ema_fast", label=f"EMA {s.fast}",
              pane="price", color=C_FAST),
        _line(candles, ema_last(s.slow), id="ema_slow", label=f"EMA {s.slow}",
              pane="price", color=C_SLOW),
    ]


def _macd(s, candles):
    def hist(c):
        h = ind.macd_histogram(ind.closes(c))
        return h[-1] if h else None
    return [
        _line(candles, hist, id="macd", label="MACD histogram", pane="lower", color=C_OSC),
        _threshold("macd_zero", "0", "lower", 0),
    ]


def _squeeze(s, candles):
    p = s.period
    closes = lambda c: ind.closes(c)  # noqa: E731

    def bb(mult):
        def f(c):
            m = ind.sma(closes(c), p)
            sd = ind.stddev(closes(c), p)
            return None if m is None or sd is None else m + sd * mult
        return f

    def kc(mult):
        def f(c):
            m = ind.sma(closes(c), p)
            rng = ind.atr(c, p)
            return None if m is None or rng is None else m + rng * mult
        return f

    return [
        _line(candles, bb(s.bb_mult), id="bb_up", label="BB upper", pane="price", color=C_BAND),
        _line(candles, bb(-s.bb_mult), id="bb_dn", label="BB lower", pane="price", color=C_BAND),
        _line(candles, kc(s.kc_mult), id="kc_up", label="KC upper", pane="price", color=C_CHANNEL),
        _line(candles, kc(-s.kc_mult), id="kc_dn", label="KC lower", pane="price", color=C_CHANNEL),
    ]


def _chandelier(s, candles):
    def stop(c):
        vol = ind.atr(c, s.atr_period)
        hi = ind.donchian_high(c, s.breakout_period, exclude_last=False)
        return None if vol is None or hi is None else hi - vol * s.atr_mult
    return [
        _line(candles, lambda c: ind.ema(ind.closes(c), s.trend_ema),
              id="trend", label=f"EMA {s.trend_ema}", pane="price", color=C_SLOW),
        _line(candles, lambda c: ind.donchian_high(c, s.breakout_period, exclude_last=False),
              id="breakout", label=f"{s.breakout_period} high", pane="price", color=C_CHANNEL),
        _line(candles, stop, id="stop", label="chandelier stop", pane="price", color=C_STOP),
    ]


def _mtf(s, candles):
    def score(c):
        values = ind.closes(c)
        if len(values) <= max(s.horizons):
            return None
        total = Decimal(0)
        for horizon, weight in zip(s.horizons, s.weights):
            total += (values[-1] - values[-horizon]) / values[-horizon] * weight
        return total * Decimal("100")  # percent
    return [
        _line(candles, score, id="mtf", label="momentum score %", pane="lower", color=C_OSC),
        _threshold("mtf_zero", "0", "lower", 0),
    ]


def _obv(s, candles):
    def obv_last(c):
        series = ind.obv_series(c)
        return series[-1] if series else None
    return [
        _line(candles, lambda c: ind.donchian_high(c, s.resistance_period, exclude_last=False),
              id="resistance", label=f"{s.resistance_period} resistance", pane="price", color=C_CHANNEL),
        _line(candles, obv_last, id="obv", label="OBV", pane="lower", color=C_OSC),
    ]


def _regime(s, candles):
    return [
        _line(candles, lambda c: ind.ema(ind.closes(c), s.trend_ema),
              id="trend", label=f"EMA {s.trend_ema}", pane="price", color=C_SLOW),
        _line(candles, lambda c: ind.efficiency_ratio(ind.closes(c), s.er_period),
              id="er", label=f"efficiency {s.er_period}", pane="lower", color=C_OSC),
        _threshold("er_trend", f"trend {s.trend_threshold}", "lower", s.trend_threshold),
        _threshold("er_range", f"range {s.range_threshold}", "lower", s.range_threshold),
    ]


def _grid(s, candles):
    # A grid trades a range gated by a fast/slow SMA regime filter; show both.
    return [
        _line(candles, lambda c: ind.sma(ind.closes(c), s.trend_fast),
              id="sma_fast", label=f"SMA {s.trend_fast}", pane="price", color=C_FAST),
        _line(candles, lambda c: ind.sma(ind.closes(c), s.trend_slow),
              id="sma_slow", label=f"SMA {s.trend_slow}", pane="price", color=C_SLOW),
    ]


_BUILDERS = {
    "VolAdaptiveGrid": _grid,
    "BollingerZScore": _bollinger,
    "VwapReversion": _vwap,
    "OscillatorExhaustion": _oscillator,
    "DonchianBreakout": _donchian,
    "EmaPullback": _ema_pullback,
    "MacdMomentum": _macd,
    "SqueezeExpansion": _squeeze,
    "ChandelierTrend": _chandelier,
    "MtfMomentum": _mtf,
    "ObvBreakout": _obv,
    "RegimeEnsemble": _regime,
}


def overlays_for(strategy_name: str,
                 candles: tuple[MarketSnapshot, ...]) -> list[dict]:
    """Indicator overlays for a wallet's current strategy (may be empty)."""

    if not candles:
        return []
    builder = _BUILDERS.get(strategy_name)
    if builder is None:
        return []
    strategy = _instance(strategy_name)
    if strategy is None:
        return []
    return [o for o in builder(strategy, candles) if o is not None]


def ladder_lines(open_orders: list[dict], avg_cost: str | None) -> list[dict]:
    """The wallet's live resting ladder + average cost, as price-pane lines.

    A grid (or any resting strategy) rests bids below and asks above; drawing the
    open limit prices puts the live book straight onto the price chart.
    """

    out: list[dict] = []
    if avg_cost is not None and Decimal(str(avg_cost)) > 0:
        out.append(_threshold("avg_cost", "avg cost", "price", avg_cost, "#c56be6"))
    for o in open_orders:
        price = o.get("limit_price")
        if price is None:
            continue
        side = o.get("side")
        color = C_CHANNEL if side == "BUY" else C_STOP
        out.append(_threshold(
            f"rest_{o.get('order_id')}",
            f"{'bid' if side == 'BUY' else 'ask'}",
            "price", price, color))
    return out


def markers_from_fills(fills: list[dict]) -> list[dict]:
    """Trade placements for the price chart.

    Buys are neutral opens; a sell is classified win/loss by its realized P&L so
    "successful vs bad trades" reads straight off the chart.
    """

    out: list[dict] = []
    for f in fills:
        if f.get("status") != "filled":
            continue
        ms = _fill_ms(f.get("filled_at"))
        if ms is None:
            continue
        side = f.get("side")
        if side == "SELL":
            pnl = Decimal(str(f.get("realized_pnl") or "0"))
            result = "win" if pnl > 0 else "loss" if pnl < 0 else "flat"
        else:
            result = "open"
        out.append({
            "time": ms // 1000,
            "side": side,
            "result": result,
            "price": f.get("price"),
            "reason": f.get("reason"),
            "realized_pnl": f.get("realized_pnl"),
        })
    out.sort(key=lambda m: m["time"])
    return out


def _fill_ms(iso: str | None) -> int | None:
    from .timeseries import _parse_ms
    return _parse_ms(iso)
