from __future__ import annotations

"""Authority-driven roadmap source resolver (subject-neutral).

The roadmap (the path) must come from an AUTHORITATIVE curriculum, not from
whatever the learner happened to upload — learner/NotebookLM sources ground the
LESSONS, they do not define the sequence. This resolver says, for a topic, where
the authoritative backbone comes from:

  * programming / tech  -> roadmap.sh (a known slug, else its roadmaps index)
  * every other field   -> expert research (university syllabi, standards bodies,
                            recognized expert curricula), triangulated

The host agent then fetches that backbone (via Agent-Reach or the web), and the
Curriculum Authority Gate keeps the LLM an adapter of it, not an unconstrained
author. Nothing here fetches anything; it only resolves the source.
"""

import unicodedata


# Keyword -> roadmap.sh slug. Covers the common role/skill roadmaps; anything not
# matched falls back to the roadmap.sh index so the agent picks the closest one.
ROADMAP_SH_SLUGS: dict[str, str] = {
    "frontend": "frontend", "front end": "frontend", "backend": "backend", "back end": "backend",
    "full stack": "full-stack", "fullstack": "full-stack", "devops": "devops",
    "python": "python", "java": "java", "javascript": "javascript", "typescript": "typescript",
    "react": "react", "vue": "vue", "angular": "angular", "node": "nodejs", "nodejs": "nodejs",
    "android": "android", "ios": "ios", "flutter": "flutter", "kotlin": "android", "swift": "ios",
    "go": "golang", "golang": "golang", "rust": "rust", "c++": "cpp", "cpp": "cpp",
    "sql": "sql", "postgres": "postgresql", "postgresql": "postgresql",
    "docker": "docker", "kubernetes": "kubernetes", "k8s": "kubernetes",
    "system design": "system-design", "data structures": "datastructures-and-algorithms",
    "algorithms": "datastructures-and-algorithms", "dsa": "datastructures-and-algorithms",
    "machine learning": "ai-data-scientist", "ml": "ai-data-scientist", "data science": "ai-data-scientist",
    "ai": "ai-engineer", "ai engineer": "ai-engineer", "cyber security": "cyber-security",
    "cybersecurity": "cyber-security", "blockchain": "blockchain", "game": "game-developer",
    "ux": "ux-design", "ux design": "ux-design", "product manager": "product-manager",
    "qa": "qa", "data analyst": "data-analyst", "mlops": "mlops", "api design": "api-design",
    "software architect": "software-architect", "graphql": "graphql", "spring": "spring-boot",
    "spring boot": "spring-boot", "web": "frontend",
}

PROGRAMMING_DOMAINS = {"programming", "software", "tech", "coding", "development", "cs", "computer science"}
# Lightweight signal that a free-text topic is a programming one.
_PROGRAMMING_SIGNALS = set(ROADMAP_SH_SLUGS) | {
    "code", "coding", "programming", "developer", "software", "api", "database", "framework",
}


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold().strip()


def _tokens(topic_n: str) -> set[str]:
    return {t for t in "".join(c if c.isalnum() else " " for c in topic_n).split() if t}


def _matches(kw: str, topic_n: str, tokens: set[str]) -> bool:
    # Word-boundary match: multi-word keyword -> phrase; single word -> exact token.
    # Prevents short signals ("ai", "go", "web") matching inside unrelated words
    # ("ai" in "renaissance", "go" in "mango").
    if " " in kw:
        return f" {kw} " in f" {topic_n} "
    return kw in tokens


def _is_programming(topic_n: str, domain: str | None) -> bool:
    if domain is not None:
        return _norm(domain) in PROGRAMMING_DOMAINS
    tokens = _tokens(topic_n)
    return any(_matches(sig, topic_n, tokens) for sig in _PROGRAMMING_SIGNALS)


def _best_slug(topic_n: str) -> str | None:
    # Prefer the longest matching keyword so "spring boot" beats a bare "spring".
    tokens = _tokens(topic_n)
    for kw in sorted(ROADMAP_SH_SLUGS, key=len, reverse=True):
        if _matches(kw, topic_n, tokens):
            return ROADMAP_SH_SLUGS[kw]
    return None


def resolve_roadmap_source(topic: str, domain: str | None = None) -> dict:
    """Resolve the authoritative roadmap backbone for a topic (no fetching)."""
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("topic required")
    topic_n = _norm(topic)

    if _is_programming(topic_n, domain):
        slug = _best_slug(topic_n)
        reference = f"https://roadmap.sh/{slug}" if slug else "https://roadmap.sh/roadmaps"
        return {
            "domain": "programming",
            "authority": "roadmap.sh",
            "slug": slug,
            "reference": reference,
            "fetch_via": ["agent-reach", "web"],
            "resource_independent": True,
            "instruction": (
                f"Use {reference} as the roadmap backbone (prerequisites + branches). "
                "Fetch it (Agent-Reach or web), then adapt to the learner's goal/level. "
                "Do not copy the site or claim affiliation; preserve the source URL. "
                "Learner/NotebookLM uploads ground the lessons, they do not define the path."
            ),
        }

    return {
        "domain": _norm(domain) if domain else "general",
        "authority": "expert_research",
        "slug": None,
        "reference": None,
        "fetch_query": (
            f"authoritative learning curriculum, university syllabus, standards-body or recognized "
            f"expert consensus sequence for: {topic.strip()}"
        ),
        "fetch_via": ["agent-reach", "web"],
        "resource_independent": True,
        "instruction": (
            "No single canonical site like roadmap.sh here: research 2-3 authoritative sources "
            "(university syllabi, standards bodies, recognized expert curricula), triangulate the "
            "prerequisite order, then adapt. The path comes from the field's authority, not from the "
            "learner's own uploads (those ground the lessons)."
        ),
    }
