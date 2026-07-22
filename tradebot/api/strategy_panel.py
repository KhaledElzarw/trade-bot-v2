"""Strategy-specific metric blocks for the wallet drill-down.

Every wallet's panel follows its *current* strategy: a grid shows ladder depth,
spacing, inventory and regime; a mean-reversion wallet shows its z-score bands
and time stop; the committee wallets show their cadence and sizing. The caller
passes the current strategy name and live wallet state, so excluding or
reassigning a wallet changes the panel automatically — nothing is hardcoded to a
specific wallet id.

Returns a list of ``{"title", "rows": [{"label", "value"}]}`` blocks that the
front end renders generically. Values are plain strings (already formatted);
``None`` becomes an em dash in the UI.
"""

from __future__ import annotations

from decimal import Decimal

from ..domain.market import MarketSnapshot
from ..strategies import indicators as ind
from ..strategies.builtin import BUILTIN_STRATEGIES


def _instance(name: str):
    for factory in BUILTIN_STRATEGIES:
        obj = factory()
        if obj.name == name:
            return obj
    return None


def _row(label: str, value) -> dict:
    if value is None:
        return {"label": label, "value": None}
    if isinstance(value, Decimal):
        value = f"{value:f}"
    return {"label": label, "value": str(value)}


def _pct(value: Decimal) -> str:
    return f"{value:.1f}%"


def _block(title: str, rows: list[dict]) -> dict:
    return {"title": title, "rows": rows}


def _grid_blocks(s, candles, base_qty, avg_cost, mark_price, quote_cash):
    params = _block("Grid parameters", [
        _row("Ladder depth (per side)", s.n_levels),
        _row("Spacing", f"{s.spacing_atr_mult}× ATR({s.atr_period})"),
        _row("Deploy fraction", _pct(s.deploy_fraction * 100)),
        _row("Min edge above cost", _pct(s.min_edge * 100)),
        _row("Recenter displacement", _pct(s.recenter_displacement * 100)),
        _row("Regime gate", f"SMA {s.trend_fast} vs {s.trend_slow}"),
        _row("Max inventory", _pct(s.max_inventory_fraction * 100)),
    ])
    rows = []
    vol = ind.atr(candles, s.atr_period) if candles else None
    if vol is not None:
        rows.append(_row("Current ATR", f"{vol:.2f}"))
        rows.append(_row("Current spacing", f"{vol * s.spacing_atr_mult:.2f}"))
    equity = quote_cash + base_qty * mark_price
    if equity > 0:
        rows.append(_row("Inventory", _pct(base_qty * mark_price / equity * 100)))
    if candles:
        closes = ind.closes(candles)
        fast = ind.sma(closes, s.trend_fast)
        slow = ind.sma(closes, s.trend_slow)
        if fast is not None and slow is not None:
            rows.append(_row("Regime", "paused (downtrend)" if fast < slow
                             else "accumulating (range/up)"))
    rows.append(_row("Avg cost", f"{avg_cost:.2f}" if avg_cost > 0 else None))
    return [params, _block("Grid — now", rows)]


def _bollinger_blocks(s, candles, *_):
    now = []
    if candles:
        z = ind.zscore(ind.closes(candles), s.z_period)
        now.append(_row("Current z-score", f"{z:.2f}" if z is not None else None))
    return [
        _block("Parameters", [
            _row("Z window", s.z_period),
            _row("Entry z", s.entry_z),
            _row("Deep entry z", s.deep_z),
            _row("Trend filter", f"{s.trend_period} candles"),
            _row("Time stop", f"{s.time_stop} candles"),
            _row("Entry limit offset", f"{s.entry_limit_bps} bps"),
        ]),
        _block("Now", now),
    ]


def _oscillator_blocks(s, candles, *_):
    now = []
    if candles:
        r = ind.rsi(ind.closes(candles), s.rsi_period)
        k = ind.stochastic_k(candles, s.stoch_period)
        now.append(_row("RSI", f"{r:.1f}" if r is not None else None))
        now.append(_row("Stochastic %K", f"{k:.1f}" if k is not None else None))
    return [
        _block("Parameters", [
            _row("RSI period", s.rsi_period),
            _row("Stoch period", s.stoch_period),
            _row("RSI oversold / exit", f"{s.rsi_oversold} / {s.rsi_exit}"),
            _row("Stoch oversold / overbought", f"{s.stoch_oversold} / {s.stoch_overbought}"),
            _row("ATR target", f"{s.atr_target_mult}× ATR"),
            _row("Time stop", f"{s.time_stop} candles"),
        ]),
        _block("Now", now),
    ]


def _vwap_blocks(s, candles, *_):
    now = []
    if candles:
        vwap = ind.rolling_vwap(candles, s.vwap_period)
        if vwap is not None:
            now.append(_row("Rolling VWAP", f"{vwap:.2f}"))
            dev = (candles[-1].close - vwap) / vwap * 100
            now.append(_row("Deviation", _pct(dev)))
    return [
        _block("Parameters", [
            _row("VWAP period", s.vwap_period),
            _row("Entry deviation", _pct(s.entry_deviation * 100)),
            _row("Target deviation", _pct(s.target_deviation * 100)),
            _row("Min rel. volume", s.min_rel_volume),
            _row("Time stop", f"{s.time_stop} candles"),
        ]),
        _block("Now", now),
    ]


def _simple_params(title, rows):
    def build(s, candles, *_):
        return [_block(title, [_row(label, getattr(s, attr, None))
                               for label, attr in rows])]
    return build


_DONCHIAN = _simple_params("Parameters", [
    ("Entry channel", "entry_period"), ("Exit channel", "exit_period"),
    ("Min rel. volume", "min_rel_volume"), ("Cooldown", "cooldown_candles")])
_EMA = _simple_params("Parameters", [
    ("Fast EMA", "fast"), ("Slow EMA", "slow"),
    ("Pullback tolerance", "pullback_tolerance"), ("Profit target", "profit_target")])
_MACD = _simple_params("Parameters", [
    ("Deceleration exit", "decel_streak_exit"), ("Max hold", "max_hold")])
_SQUEEZE = _simple_params("Parameters", [
    ("Period", "period"), ("BB mult", "bb_mult"), ("KC mult", "kc_mult"),
    ("Min squeeze", "min_squeeze_candles"), ("Min rel. volume", "min_rel_volume")])
_CHANDELIER = _simple_params("Parameters", [
    ("Breakout channel", "breakout_period"), ("ATR period", "atr_period"),
    ("ATR mult", "atr_mult"), ("Trend EMA", "trend_ema")])
_MTF = _simple_params("Parameters", [
    ("Horizons", "horizons"), ("Min agreeing", "min_agreeing"),
    ("Vol period", "vol_period"), ("Max fraction", "max_fraction")])
_OBV = _simple_params("Parameters", [
    ("Resistance period", "resistance_period"), ("OBV slope window", "obv_slope_window"),
    ("Min rel. volume", "min_rel_volume"), ("ATR trail", "atr_trail_mult")])
_REGIME = _simple_params("Parameters", [
    ("Efficiency period", "er_period"), ("Trend / range threshold",
     "trend_threshold"), ("Entry z", "z_entry"), ("Time stop", "time_stop")])


_BUILDERS = {
    "VolAdaptiveGrid": _grid_blocks,
    "BollingerZScore": _bollinger_blocks,
    "OscillatorExhaustion": _oscillator_blocks,
    "VwapReversion": _vwap_blocks,
    "DonchianBreakout": _DONCHIAN,
    "EmaPullback": _EMA,
    "MacdMomentum": _MACD,
    "SqueezeExpansion": _SQUEEZE,
    "ChandelierTrend": _CHANDELIER,
    "MtfMomentum": _MTF,
    "ObvBreakout": _OBV,
    "RegimeEnsemble": _REGIME,
}

_PERMANENT = {
    "DarkHorse": [_block("Committee", [
        {"label": "Cadence", "value": "4 hours"},
        {"label": "Accumulate fraction", "value": "25% of cash"},
        {"label": "Reduce fraction", "value": "50% of position"},
        {"label": "Domains", "value": "technical, liquidity, macro, fundamental, on-chain"},
        {"label": "Execution", "value": "market (conviction)"},
    ])],
    "DarkhorseDaily": [_block("Committee (daily-adaptive)", [
        {"label": "Cadence", "value": "re-tuned daily"},
        {"label": "Sizing", "value": "LLM-adapted within guardrails"},
        {"label": "Limit offsets", "value": "entry/exit bps, adaptive"},
        {"label": "Domains", "value": "same five-domain committee"},
    ])],
}


def strategy_metrics(strategy_name: str,
                     candles: tuple[MarketSnapshot, ...],
                     *,
                     base_qty: Decimal,
                     avg_cost: Decimal,
                     mark_price: Decimal,
                     quote_cash: Decimal) -> list[dict]:
    """Metric blocks for the wallet's current strategy (possibly empty)."""

    if strategy_name in _PERMANENT:
        return _PERMANENT[strategy_name]
    builder = _BUILDERS.get(strategy_name)
    strategy = _instance(strategy_name)
    if builder is None or strategy is None:
        return []
    return builder(strategy, candles, base_qty, avg_cost, mark_price, quote_cash)
