"""Epistemic Gate: credibility + freshness, and the combined source gate."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.source_epistemics import (  # noqa: E402
    SourceCredibility, evaluate_source_trust, source_gate,
)
from runtime.source_governance import SourceRecord, SourceRights, evaluate_source_use  # noqa: E402

NOW = "2026-10-07"


def trust(**kw):
    vol = kw.pop("claim_volatility", "slow")
    as_of = kw.pop("as_of", NOW)
    kw.setdefault("source_id", "s1")
    return evaluate_source_trust(SourceCredibility(**kw), vol, as_of=as_of)


def test_fresh_official_source_is_trusted():
    d = trust(source_type="official", publication_date="2026-01-01", claim_volatility="volatile")
    assert d.status == "trusted"
    assert d.trust_tier == "high"
    assert d.freshness == "fresh"


def test_stale_official_doc_on_volatile_claim_is_stale_not_trusted():
    # A high-tier source does not get a pass on freshness for a volatile claim.
    d = trust(source_type="official", publication_date="2015-01-01", claim_volatility="volatile")
    assert d.status == "stale"
    assert d.freshness == "stale"


def test_stable_claim_never_expires_on_age():
    d = trust(source_type="peer_reviewed", publication_date="1950-01-01", claim_volatility="stable")
    assert d.status == "trusted"
    assert d.freshness == "not_applicable"


def test_low_tier_source_needs_corroboration():
    d = trust(source_type="forum", publication_date="2026-09-01", claim_volatility="slow")
    assert d.status == "corroborate_required"


def test_low_tier_with_two_corroborators_is_trusted():
    d = trust(source_type="forum", publication_date="2026-09-01",
              corroborating_source_count=2, claim_volatility="slow")
    assert d.status == "trusted"


def test_retracted_source_is_rejected():
    d = trust(source_type="peer_reviewed", retracted=True, publication_date="2026-01-01")
    assert d.status == "reject"


def test_ai_derived_without_provenance_needs_review():
    d = trust(source_type="ai_derived")
    assert d.status == "review_required"
    assert "ai_derived_without_provenance" in d.reasons


def test_unknown_freshness_on_volatile_claim_needs_review():
    d = trust(source_type="official", publication_date=None, claim_volatility="breaking")
    assert d.status == "review_required"
    assert d.freshness == "unknown"


def test_last_verified_refreshes_an_old_source():
    # Old publication but recently re-verified -> fresh.
    d = trust(source_type="official", publication_date="2010-01-01",
              last_verified="2026-09-15", claim_volatility="volatile")
    assert d.status == "trusted"
    assert d.freshness == "fresh"


def test_invalid_volatility_raises():
    with pytest.raises(ValueError):
        trust(source_type="official", claim_volatility="nonsense")


# --- combined gate ---------------------------------------------------------

def _rights(status):
    src = SourceRecord(source_id="s1", uri=None, source_type="official",
                       rights=SourceRights(rights_status=status))
    return evaluate_source_use(src, "quote")


def test_combined_gate_allows_only_when_both_clear():
    epi = trust(source_type="official", publication_date="2026-01-01", claim_volatility="volatile")
    src = SourceRecord(source_id="s1", uri=None, source_type="official",
                       rights=SourceRights(permitted_operations={"quote": True}))
    gate = source_gate(epi, evaluate_source_use(src, "quote"))
    assert gate.status == "allow"


def test_combined_gate_denies_when_rights_prohibited():
    epi = trust(source_type="official", publication_date="2026-01-01", claim_volatility="volatile")
    gate = source_gate(epi, _rights("prohibited"))
    assert gate.status == "deny"


def test_combined_gate_denies_when_epistemic_rejects():
    epi = trust(source_type="peer_reviewed", retracted=True)
    src = SourceRecord(source_id="s1", uri=None, source_type="official",
                       rights=SourceRights(permitted_operations={"quote": True}))
    gate = source_gate(epi, evaluate_source_use(src, "quote"))
    assert gate.status == "deny"


def test_combined_gate_review_when_epistemic_uncertain():
    epi = trust(source_type="forum", publication_date="2026-09-01")  # corroborate_required
    src = SourceRecord(source_id="s1", uri=None, source_type="forum",
                       rights=SourceRights(permitted_operations={"quote": True}))
    gate = source_gate(epi, evaluate_source_use(src, "quote"))
    assert gate.status == "review_required"
