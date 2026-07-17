"""Structural novelty evaluation (product spec <novelty_and_lineage>).

A canonical fingerprint is derived from the strategy SOURCE by AST analysis —
never from the model's own claim of novelty, which is not accepted as evidence.

The fingerprint captures the dimensions the spec requires: indicator families,
required timeframes, feature transformations, entry/exit condition structure,
position-sizing method, stop/trailing method, holding-period behaviour, order
types, state-machine structure, normalized parameter families, and conceptual
family.

All thresholds live in ONE versioned policy object (`NoveltyPolicy`) — they are
not hardcoded across the codebase.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal

NOVELTY_POLICY_VERSION = "novelty-policy-v1"

# Indicator call names grouped into conceptual families.
INDICATOR_FAMILIES: dict[str, str] = {
    "sma": "moving_average", "ema": "moving_average", "ema_series": "moving_average",
    "stddev": "dispersion", "zscore": "dispersion",
    "atr": "volatility", "true_range": "volatility",
    "rsi": "oscillator", "stochastic_k": "oscillator",
    "macd_histogram": "macd",
    "donchian_high": "channel", "donchian_low": "channel",
    "rolling_vwap": "vwap",
    "obv_series": "volume_flow", "relative_volume": "volume_flow",
    "efficiency_ratio": "regime",
}

SIZING_MARKERS = {"buy_intent": "fractional_cash", "entry_fraction": "fractional_cash",
                  "max_fraction": "inverse_vol"}
STOP_MARKERS = {"chandelier", "trail", "stop", "atr_trail_mult", "atr_mult"}
HOLDING_MARKERS = {"time_stop", "max_hold", "candles_held", "cooldown_candles"}


@dataclass(frozen=True, slots=True)
class NoveltyPolicy:
    """The single versioned source of novelty thresholds."""

    version: str = NOVELTY_POLICY_VERSION
    novel_max_structural_similarity: Decimal = Decimal("0.65")
    novel_max_signal_correlation: Decimal = Decimal("0.75")
    mutation_min_structural_similarity: Decimal = Decimal("0.65")
    mutation_max_structural_similarity: Decimal = Decimal("0.95")
    max_trade_entry_overlap: Decimal = Decimal("0.75")


@dataclass(frozen=True, slots=True)
class Fingerprint:
    family: str
    indicator_families: frozenset[str]
    timeframes: frozenset[str]
    entry_structure: frozenset[str]
    exit_structure: frozenset[str]
    sizing_method: frozenset[str]
    stop_method: frozenset[str]
    holding_behaviour: frozenset[str]
    order_types: frozenset[str]
    state_keys: frozenset[str]
    parameter_families: frozenset[str]

    def digest(self) -> str:
        """Stable hash of the normalized structure (not the raw source)."""

        parts = [
            self.family,
            *sorted(self.indicator_families), *sorted(self.timeframes),
            *sorted(self.entry_structure), *sorted(self.exit_structure),
            *sorted(self.sizing_method), *sorted(self.stop_method),
            *sorted(self.holding_behaviour), *sorted(self.order_types),
            *sorted(self.state_keys), *sorted(self.parameter_families),
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    def _dimensions(self) -> list[frozenset[str]]:
        return [
            self.indicator_families, self.timeframes, self.entry_structure,
            self.exit_structure, self.sizing_method, self.stop_method,
            self.holding_behaviour, self.order_types, self.state_keys,
            self.parameter_families,
        ]


@dataclass
class _Extractor(ast.NodeVisitor):
    indicators: set[str] = field(default_factory=set)
    calls: set[str] = field(default_factory=set)
    reasons: set[str] = field(default_factory=set)
    attrs: set[str] = field(default_factory=set)
    state_keys: set[str] = field(default_factory=set)
    params: set[str] = field(default_factory=set)
    order_types: set[str] = field(default_factory=set)
    timeframes: set[str] = field(default_factory=set)

    def visit_Call(self, node: ast.Call) -> None:
        name = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name:
            self.calls.add(name)
            if name in INDICATOR_FAMILIES:
                self.indicators.add(INDICATOR_FAMILIES[name])
        for kw in node.keywords:
            if kw.arg == "reason" and isinstance(kw.value, ast.Constant):
                self.reasons.add(str(kw.value.value))
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if arg.value in ("MARKET", "LIMIT"):
                    self.order_types.add(arg.value)
                elif arg.value.endswith(("m", "h", "d")) and arg.value[:-1].isdigit():
                    self.timeframes.add(arg.value)
                else:
                    self.reasons.add(arg.value)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.attrs.add(node.attr)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            self.state_keys.add(node.slice.value)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        self.params.add(target.id)
        self.generic_visit(node)


def fingerprint_source(source: str, *, family: str,
                       timeframes: tuple[str, ...] = ()) -> Fingerprint:
    """Derive the canonical structural fingerprint from strategy source."""

    tree = ast.parse(source)
    ex = _Extractor()
    ex.visit(tree)

    entry = {r for r in ex.reasons if not _is_exit_reason(r)}
    exit_ = {r for r in ex.reasons if _is_exit_reason(r)}
    sizing = {SIZING_MARKERS[m] for m in SIZING_MARKERS
              if m in ex.calls or m in ex.attrs or m in ex.params}
    stops = {m for m in STOP_MARKERS
             if any(m in s for s in ex.params | ex.attrs | ex.reasons)}
    holding = {m for m in HOLDING_MARKERS if m in ex.params or m in ex.state_keys
               or m in ex.attrs}

    return Fingerprint(
        family=family,
        indicator_families=frozenset(ex.indicators),
        timeframes=frozenset(timeframes) | frozenset(ex.timeframes),
        entry_structure=frozenset(entry),
        exit_structure=frozenset(exit_),
        sizing_method=frozenset(sizing or {"fractional_cash"}),
        stop_method=frozenset(stops),
        holding_behaviour=frozenset(holding),
        order_types=frozenset(ex.order_types or {"MARKET"}),
        state_keys=frozenset(ex.state_keys),
        parameter_families=frozenset(_normalize_param(p) for p in ex.params),
    )


def _is_exit_reason(reason: str) -> bool:
    return any(k in reason for k in ("exit", "stop", "sell", "reversal",
                                     "invalidated", "failure", "divergence",
                                     "breakdown", "target", "overbought",
                                     "recovered", "deceleration", "shock",
                                     "nonpositive", "lost_", "back_inside"))


def _normalize_param(name: str) -> str:
    """Collapse a parameter to its family (period/threshold/multiplier/…)."""

    lowered = name.lower()
    for suffix, family in (
        ("period", "period"), ("mult", "multiplier"), ("threshold", "threshold"),
        ("fraction", "sizing"), ("stop", "stop"), ("z", "threshold"),
        ("volume", "volume"), ("warmup", "warmup"), ("hold", "holding"),
        ("cooldown", "holding"),
    ):
        if suffix in lowered:
            return family
    return "other"


def structural_similarity(a: Fingerprint, b: Fingerprint) -> Decimal:
    """Mean Jaccard similarity across dimensions, plus the family match.

    Returns a value in [0, 1]. Two identical fingerprints score 1.
    """

    scores: list[Decimal] = [Decimal("1") if a.family == b.family else Decimal("0")]
    for da, db in zip(a._dimensions(), b._dimensions()):
        union = da | db
        if not union:
            scores.append(Decimal("1"))  # both absent = same on this dimension
            continue
        scores.append(Decimal(len(da & db)) / Decimal(len(union)))
    return sum(scores, start=Decimal("0")) / Decimal(len(scores))


@dataclass(frozen=True, slots=True)
class NoveltyVerdict:
    accepted: bool
    reasons: tuple[str, ...] = ()
    max_similarity: Decimal = Decimal("0")
    closest_version_id: str | None = None


def evaluate_novel_candidate(
    candidate: Fingerprint,
    candidate_code_hash: str,
    existing: list[tuple[str, str, Fingerprint]],  # (version_id, code_hash, fp)
    *,
    policy: NoveltyPolicy,
    is_banned,
    signal_correlation: Decimal | None = None,
    trade_entry_overlap: Decimal | None = None,
) -> NoveltyVerdict:
    """Novel-candidate checks 1-7. The model's novelty claim is never evidence."""

    reasons: list[str] = []
    if is_banned(candidate_code_hash, candidate.digest()):
        return NoveltyVerdict(False, ("permanently_banned",))
    if any(code_hash == candidate_code_hash for _, code_hash, _ in existing):
        reasons.append("duplicate_code_hash")
    if any(fp.digest() == candidate.digest() for _, _, fp in existing):
        reasons.append("duplicate_structural_fingerprint")

    max_sim = Decimal("0")
    closest = None
    for version_id, _, fp in existing:
        sim = structural_similarity(candidate, fp)
        if sim > max_sim:
            max_sim, closest = sim, version_id
    if max_sim > policy.novel_max_structural_similarity:
        reasons.append("structural_similarity_above_threshold")
    if (signal_correlation is not None
            and abs(signal_correlation) > policy.novel_max_signal_correlation):
        reasons.append("signal_correlation_above_threshold")
    if (trade_entry_overlap is not None
            and trade_entry_overlap > policy.max_trade_entry_overlap):
        reasons.append("trade_entry_overlap_above_threshold")

    return NoveltyVerdict(not reasons, tuple(reasons), max_sim, closest)


def evaluate_mutation_candidate(
    candidate: Fingerprint,
    candidate_code_hash: str,
    parent_version_id: str,
    parent_code_hash: str,
    parent_fp: Fingerprint,
    *,
    policy: NoveltyPolicy,
    is_banned,
    eligible_parent_ids: frozenset[str],
    mutation_description: str,
) -> NoveltyVerdict:
    """Mutation checks 1-7: eligible surviving parent, related but changed."""

    reasons: list[str] = []
    if parent_version_id not in eligible_parent_ids:
        reasons.append("parent_not_eligible")  # e.g. parent is being eliminated
    if is_banned(candidate_code_hash, candidate.digest()):
        return NoveltyVerdict(False, ("permanently_banned",))
    if candidate_code_hash == parent_code_hash:
        reasons.append("identical_to_parent")
    if not mutation_description:
        reasons.append("missing_mutation_description")

    sim = structural_similarity(candidate, parent_fp)
    if sim < policy.mutation_min_structural_similarity:
        reasons.append("mutation_unrelated_to_parent")
    if sim > policy.mutation_max_structural_similarity:
        reasons.append("mutation_too_similar_to_parent")
    return NoveltyVerdict(not reasons, tuple(reasons), sim, parent_version_id)
