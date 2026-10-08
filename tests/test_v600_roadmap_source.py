"""Authority-driven roadmap source resolver (resource-independent)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "adaptive-learn"
sys.path.insert(0, str(SKILL))

from runtime.roadmap_source import resolve_roadmap_source  # noqa: E402


def test_programming_topic_maps_to_roadmap_sh():
    r = resolve_roadmap_source("Learn Python for data work")
    assert r["authority"] == "roadmap.sh"
    assert r["slug"] == "python"
    assert r["reference"] == "https://roadmap.sh/python"
    assert r["resource_independent"] is True


def test_kotlin_android_maps_to_android_roadmap():
    r = resolve_roadmap_source("Kotlin for Android apps")
    assert r["authority"] == "roadmap.sh"
    assert r["slug"] == "android"


def test_unknown_programming_falls_back_to_index():
    r = resolve_roadmap_source("coding in some niche language", domain="programming")
    assert r["authority"] == "roadmap.sh"
    assert r["reference"] == "https://roadmap.sh/roadmaps"


def test_non_programming_uses_expert_research():
    r = resolve_roadmap_source("Renaissance art history")
    assert r["authority"] == "expert_research"
    assert r["slug"] is None
    assert "art history" in r["fetch_query"].lower()
    assert r["resource_independent"] is True


def test_domain_hint_overrides_topic_text():
    r = resolve_roadmap_source("music theory", domain="music")
    assert r["authority"] == "expert_research"
    r2 = resolve_roadmap_source("history of jazz", domain="programming")
    assert r2["authority"] == "roadmap.sh"


def test_empty_topic_raises():
    with pytest.raises(ValueError):
        resolve_roadmap_source("  ")


def test_agent_reach_is_a_fetch_option():
    r = resolve_roadmap_source("Learn SQL")
    assert "agent-reach" in r["fetch_via"]


def test_cpp_and_csharp_recognized():
    assert resolve_roadmap_source("Learn C++")["slug"] == "cpp"
    assert resolve_roadmap_source("C# for web APIs")["slug"] == "aspnet-core"


def test_ambiguous_words_do_not_misroute_to_programming():
    for t in ["Game theory", "AI ethics and policy", "Taylor Swift lyrics", "Go the board game"]:
        assert resolve_roadmap_source(t)["authority"] == "expert_research", t


def test_unambiguous_programming_still_detected():
    assert resolve_roadmap_source("Learn Python")["authority"] == "roadmap.sh"
    assert resolve_roadmap_source("Go programming language")["slug"] == "golang"
