"""Novelty fingerprinting, thresholds, and lineage tests (Phase 9b)."""

import inspect
from decimal import Decimal

import pytest

from tradebot.application.evolution import BanRegistry
from tradebot.application.novelty import (
    NOVELTY_POLICY_VERSION,
    NoveltyPolicy,
    evaluate_mutation_candidate,
    evaluate_novel_candidate,
    fingerprint_source,
    structural_similarity,
)
from tradebot.domain.lineage import (
    MUTATION,
    NOVEL,
    LineageEdge,
    LineageGraph,
)
from tradebot.strategies.builtin import BUILTIN_STRATEGIES

POLICY = NoveltyPolicy()


def fp_of(cls):
    source = inspect.getsource(inspect.getmodule(cls))
    return fingerprint_source(source, family=cls().metadata().family)


# ---- fingerprinting ---------------------------------------------------------

def test_policy_is_single_versioned_source():
    assert POLICY.version == NOVELTY_POLICY_VERSION
    assert POLICY.novel_max_structural_similarity == Decimal("0.65")
    assert POLICY.novel_max_signal_correlation == Decimal("0.75")
    assert POLICY.mutation_min_structural_similarity == Decimal("0.65")
    assert POLICY.mutation_max_structural_similarity == Decimal("0.95")


def test_identical_source_is_self_similar():
    fp = fp_of(BUILTIN_STRATEGIES[0])
    assert structural_similarity(fp, fp) == Decimal("1")


def test_all_twelve_builtins_have_distinct_fingerprints():
    digests = {fp_of(cls).digest() for cls in BUILTIN_STRATEGIES}
    assert len(digests) == 12


def test_builtins_are_structurally_dissimilar_pairwise():
    """No two built-ins exceed the novel similarity threshold."""
    fps = [(cls.__name__, fp_of(cls)) for cls in BUILTIN_STRATEGIES]
    for i, (name_a, a) in enumerate(fps):
        for name_b, b in fps[i + 1:]:
            sim = structural_similarity(a, b)
            assert sim <= POLICY.novel_max_structural_similarity, (
                f"{name_a} vs {name_b} too similar: {sim}")


def test_fingerprint_captures_indicator_families():
    fp = fp_of(BUILTIN_STRATEGIES[6])  # MacdMomentum
    assert "macd" in fp.indicator_families


# ---- novel candidate checks -------------------------------------------------

def _existing():
    return [(f"v{i}", f"hash{i}", fp_of(cls))
            for i, cls in enumerate(BUILTIN_STRATEGIES)]


def test_novel_candidate_accepted_when_distinct():
    source = '''
from tradebot.strategies.indicators import efficiency_ratio


class Novel:
    aroon_period = 25
    entry_threshold = 70

    def signal(self, context, candles, state, *, holding):
        er = efficiency_ratio([c.close for c in candles], self.aroon_period)
        if er and not holding:
            return [self.buy_intent(context, "aroon_cross")]
        return [self.sell_all_intent(context, "aroon_exit")]
'''
    fp = fingerprint_source(source, family="aroon_novel")
    verdict = evaluate_novel_candidate(
        fp, "newhash", _existing(), policy=POLICY,
        is_banned=BanRegistry().is_banned)
    assert verdict.accepted, verdict.reasons


def test_novel_candidate_rejected_when_duplicate_hash():
    fp = fp_of(BUILTIN_STRATEGIES[0])
    verdict = evaluate_novel_candidate(
        fp, "hash0", _existing(), policy=POLICY, is_banned=BanRegistry().is_banned)
    assert not verdict.accepted
    assert "duplicate_code_hash" in verdict.reasons
    assert "duplicate_structural_fingerprint" in verdict.reasons


def test_novel_candidate_rejected_when_banned():
    bans = BanRegistry()
    fp = fp_of(BUILTIN_STRATEGIES[1])
    bans.ban("badhash", fp.digest())
    verdict = evaluate_novel_candidate(
        fp, "badhash", _existing(), policy=POLICY, is_banned=bans.is_banned)
    assert not verdict.accepted
    assert verdict.reasons == ("permanently_banned",)


def test_novel_candidate_rejected_by_banned_fingerprint_alone():
    """A reskinned clone (new hash, same structure) is still blocked."""
    bans = BanRegistry()
    fp = fp_of(BUILTIN_STRATEGIES[2])
    bans.ban("originalhash", fp.digest())
    verdict = evaluate_novel_candidate(
        fp, "different_hash", [], policy=POLICY, is_banned=bans.is_banned)
    assert not verdict.accepted
    assert verdict.reasons == ("permanently_banned",)


def test_novel_candidate_rejected_on_high_signal_correlation():
    source = "class N:\n    aroon_period = 5\n"
    fp = fingerprint_source(source, family="unique_family")
    verdict = evaluate_novel_candidate(
        fp, "h", _existing(), policy=POLICY, is_banned=BanRegistry().is_banned,
        signal_correlation=Decimal("0.9"))
    assert not verdict.accepted
    assert "signal_correlation_above_threshold" in verdict.reasons


def test_novel_candidate_rejected_on_negative_correlation_magnitude():
    """|correlation| is used — an inverted clone is not novel."""
    source = "class N:\n    aroon_period = 5\n"
    fp = fingerprint_source(source, family="unique_family")
    verdict = evaluate_novel_candidate(
        fp, "h", _existing(), policy=POLICY, is_banned=BanRegistry().is_banned,
        signal_correlation=Decimal("-0.95"))
    assert not verdict.accepted
    assert "signal_correlation_above_threshold" in verdict.reasons


def test_novel_candidate_rejected_on_trade_entry_overlap():
    source = "class N:\n    aroon_period = 5\n"
    fp = fingerprint_source(source, family="unique_family")
    verdict = evaluate_novel_candidate(
        fp, "h", _existing(), policy=POLICY, is_banned=BanRegistry().is_banned,
        trade_entry_overlap=Decimal("0.9"))
    assert not verdict.accepted
    assert "trade_entry_overlap_above_threshold" in verdict.reasons


def test_model_claim_of_novelty_is_not_accepted_as_evidence():
    """A candidate identical to an incumbent is rejected regardless of any
    'novelty_explanation' the model supplies — only the fingerprint counts."""
    fp = fp_of(BUILTIN_STRATEGIES[3])
    verdict = evaluate_novel_candidate(
        fp, "hash3", _existing(), policy=POLICY, is_banned=BanRegistry().is_banned)
    assert not verdict.accepted


# ---- mutation candidate checks ---------------------------------------------

PARENT_SRC = '''
from tradebot.strategies.indicators import rsi


class Parent:
    rsi_period = 14
    entry_threshold = 30
    time_stop = 30

    def signal(self, context, candles, state, *, holding):
        if not holding:
            return [self.buy_intent(context, "rsi_entry")]
        return [self.sell_all_intent(context, "rsi_exit")]
'''

MUTANT_SRC = '''
from tradebot.strategies.indicators import rsi


class Mutant:
    rsi_period = 21
    entry_threshold = 25
    time_stop = 30
    atr_trail_mult = 2

    def signal(self, context, candles, state, *, holding):
        if not holding:
            return [self.buy_intent(context, "rsi_entry")]
        return [self.sell_all_intent(context, "atr_trail_stop")]
'''


def test_mutation_accepted_when_related_but_changed():
    parent = fingerprint_source(PARENT_SRC, family="oscillator_reversal")
    mutant = fingerprint_source(MUTANT_SRC, family="oscillator_reversal")
    verdict = evaluate_mutation_candidate(
        mutant, "mhash", "pv1", "phash", parent, policy=POLICY,
        is_banned=BanRegistry().is_banned, eligible_parent_ids=frozenset({"pv1"}),
        mutation_description="widen rsi period; add ATR trailing stop")
    assert verdict.accepted, verdict.reasons
    assert POLICY.mutation_min_structural_similarity <= verdict.max_similarity


def test_mutation_rejected_when_parent_eliminated():
    parent = fingerprint_source(PARENT_SRC, family="oscillator_reversal")
    mutant = fingerprint_source(MUTANT_SRC, family="oscillator_reversal")
    verdict = evaluate_mutation_candidate(
        mutant, "mhash", "pv1", "phash", parent, policy=POLICY,
        is_banned=BanRegistry().is_banned,
        eligible_parent_ids=frozenset(),  # parent is being eliminated
        mutation_description="x")
    assert not verdict.accepted
    assert "parent_not_eligible" in verdict.reasons


def test_mutation_rejected_when_identical_to_parent():
    parent = fingerprint_source(PARENT_SRC, family="oscillator_reversal")
    verdict = evaluate_mutation_candidate(
        parent, "same", "pv1", "same", parent, policy=POLICY,
        is_banned=BanRegistry().is_banned, eligible_parent_ids=frozenset({"pv1"}),
        mutation_description="none")
    assert not verdict.accepted
    assert "identical_to_parent" in verdict.reasons
    assert "mutation_too_similar_to_parent" in verdict.reasons


def test_mutation_rejected_when_unrelated_to_parent():
    parent = fingerprint_source(PARENT_SRC, family="oscillator_reversal")
    unrelated = fp_of(BUILTIN_STRATEGIES[0])  # grid — different family
    verdict = evaluate_mutation_candidate(
        unrelated, "uhash", "pv1", "phash", parent, policy=POLICY,
        is_banned=BanRegistry().is_banned, eligible_parent_ids=frozenset({"pv1"}),
        mutation_description="rewrite")
    assert not verdict.accepted
    assert "mutation_unrelated_to_parent" in verdict.reasons


def test_mutation_requires_description():
    parent = fingerprint_source(PARENT_SRC, family="oscillator_reversal")
    mutant = fingerprint_source(MUTANT_SRC, family="oscillator_reversal")
    verdict = evaluate_mutation_candidate(
        mutant, "mhash", "pv1", "phash", parent, policy=POLICY,
        is_banned=BanRegistry().is_banned, eligible_parent_ids=frozenset({"pv1"}),
        mutation_description="")
    assert not verdict.accepted
    assert "missing_mutation_description" in verdict.reasons


# ---- lineage ----------------------------------------------------------------

def test_lineage_tracks_ancestry_and_generation():
    g = LineageGraph()
    g.add(LineageEdge("v1", None, NOVEL))
    g.add(LineageEdge("v2", "v1", MUTATION, "widen period"))
    g.add(LineageEdge("v3", "v2", MUTATION, "add trailing stop"))
    assert g.generation("v1") == 0
    assert g.generation("v3") == 2
    assert g.ancestors("v3") == ["v2", "v1"]
    assert g.children_of("v1") == ["v2"]
    assert g.parent_of("v1") is None


def test_lineage_rejects_invalid_edges():
    g = LineageGraph()
    with pytest.raises(ValueError, match="unknown relationship"):
        g.add(LineageEdge("v1", None, "telepathy"))
    with pytest.raises(ValueError, match="requires a parent"):
        g.add(LineageEdge("v1", None, MUTATION, "x"))
    with pytest.raises(ValueError, match="requires a description"):
        g.add(LineageEdge("v1", "v0", MUTATION, ""))
    with pytest.raises(ValueError, match="own parent"):
        g.add(LineageEdge("v1", "v1", MUTATION, "x"))
    g.add(LineageEdge("v1", None, NOVEL))
    with pytest.raises(ValueError, match="duplicate lineage"):
        g.add(LineageEdge("v1", None, NOVEL))


def test_lineage_survives_elimination_and_is_cycle_safe():
    g = LineageGraph()
    g.add(LineageEdge("v1", None, NOVEL))
    g.add(LineageEdge("v2", "v1", MUTATION, "tweak"))
    # v1 eliminated/banned — its lineage record persists as evidence.
    bans = BanRegistry()
    bans.ban("v1hash")
    assert g.parent_of("v2") == "v1"
    assert g.describe("v2").mutation_description == "tweak"
    assert bans.is_banned("v1hash")
    # Unknown version walks to an empty ancestry rather than looping.
    assert g.ancestors("unknown") == []
