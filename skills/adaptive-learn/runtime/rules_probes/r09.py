"""R9 honest measurement: no forbidden claims in learner-visible text, declared counts equal derived counts, the
handover keeps its caveats, and a receipt never claims quality."""
from __future__ import annotations

import re
import unicodedata

from .base import RECEIPT_CLAIM, RUNTIME_DIR, Ctx, Result, na, probe, verdict

# (Azerbaijani patterns: native review advisable.) Matching is on lower-cased text with the dotted capital I folded.
FORBIDDEN = [
    ("proven", re.compile(r"\bproven\b")), ("guarantee", re.compile(r"\bguarantee(?:s|d|ing)?\b")),
    ("will master", re.compile(r"(?:\bwill|'ll)\s+(?:definitely\s+|surely\s+|certainly\s+)?master\b")),
    ("assured", re.compile(r"\b(?:is|are|be|fully|completely)\s+assured\b")),
    ("certain pass", re.compile(r"\b(?:100\s?%|completely|absolutely)\s+certain\b")),
    ("make you an expert", re.compile(r"\bmake\s+you\s+(?:an?\s+)?expert\b")),
    ("everyone passes", re.compile(r"\b(?:everyone|every\s+learner|all\s+learners)\b[^.!?\n]{0,60}\bpass(?:es)?\b")),
    ("numeric efficacy claim", re.compile(r"\b(?:doubles?|triples?|\d+(?:\.\d+)?\s?(?:x|times))\s+(?:your\s+|the\s+)?(?:retention|learning|speed|scores?|results?)\b"
                                          r"|\b(?:success|pass|retention|completion)\s+rate\s*:?\s*(?:is\s+)?\d")),
    ("expert-quality", re.compile(r"\bexpert[- ]quality\b")),
    ("unqualified effective", re.compile(r"\b(?:is|are|very|highly|most|extremely|truly)\s+effective\b")),
    ("percentage claim", re.compile(r"\b\d{1,3}\s?(?:%|percent)\s+(?:more|faster|better|higher|improvement|effective|success|mastery)\b")),
    ("sübut olunmuş", re.compile(r"\bsübut\s+(?:olunmuş|edilmiş)\b")), ("zəmanət", re.compile(r"\bzəmanət[a-zəıöüğşç]*")),
    ("ustalıq zəmanəti", re.compile(r"\bustalıq\s+zəmanəti\b")),
    ("mütləq öyrənəcəksən", re.compile(r"\bmütləq\s+(?:öyrən|ustalaş|mənimsə|keç|uğur|nail)[a-zəıöüğşç]*")),
    ("hər kəs mütləq", re.compile(r"\bhər\s+kəs\s+mütləq\b")), ("təminatlı", re.compile(r"\btəminat[a-zəıöüğşç]*")),
    ("ekspert edəcək", re.compile(r"\bekspert\s+ed(?:ir|əcək|əcəyik|ər)[a-zəıöüğşç]*")),
    ("ekspert keyfiyyəti", re.compile(r"\bekspert\s+(?:səviyyəsində|keyfiyyətində)\b")),
    ("100% effektiv", re.compile(r"\b100\s?%\s*(?:effektiv|uğur|ustalıq)")),
    ("ən effektiv", re.compile(r"\b(?:çox|ən|son dərəcə)\s+effektiv(?:dir)?\b")),
]
CONTENT_ONLY = [("mastered", re.compile(r"\bmastered\b")), ("ustalaşdın", re.compile(r"\bustalaş(?:dın|mısan|mısınız)\b")),
                ("ustalaşacaqsan", re.compile(r"\bustalaş(?:acaq|dır|ır|ar|ıb|dırır)[a-zəıöüğşç]*")),
                ("mənimsəyib", re.compile(r"\bmənimsə(?:yib|yəcək|yirsən|yirsiniz|yəcəksən|yəcəksiniz|dir)[a-zəıöüğşç]*")),
                ("mastery qazanacaq", re.compile(r"\bmastery\s+qazan[a-zəıöüğşç]*"))]
# A negator only cancels a claim it is grammatically bound to: right after the claim, at most two words later and in the
# same clause ('zəmanət deyil', 'sübut olunmuş optimal interval deyil'), or within one word before it ('not proven', 'no guarantee', 'cannot be guaranteed'). Clause punctuation and any further words break
# the binding, so 'guaranteed, and you do not need ...' or 'no doubt you will master' stay claims.
NEGATION_AFTER = re.compile(r"^\s*(?:\w+\s+){0,2}?(?:deyil|etmir|etmədi|yoxdur|olmur|olmaz|verilmir|vermir|vermirik|vermirəm|isn't|doesn't)\b")
NEGATION_BEFORE = re.compile(r"\b(?:not|no|never|cannot|without|nor)\s+(?:\w+\s+)?$|\b(?:heç|nə)\s+(?:\w+\s+)?$")
SKIP_KEYS = frozenset({"code", "starter_code", "reference_solution", "wrong_code", "template", "blanks", "detector",
                       "quote", "expected_output", "source_id", "label"})
REQUIRED_NOT_VERIFIED = ("lexical_grounding", "fresh_v1_uncalibrated", "free_text_provisional", "no_efficacy_evidence")


# Look-alike Cyrillic/Greek letters folded to Latin so a homoglyph-spelled claim ('guаrantee') cannot slip past the lexicon.
_HOMOGLYPHS = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ѕ": "s",
                             "ј": "j", "һ": "h", "ԁ": "d", "ɡ": "g", "ν": "v", "ο": "o", "α": "a", "ρ": "p", "ι": "i"})


def fold(text: str) -> str:
    """Lower-case, NFKC-normalise, drop format characters (zero-width, bidi, soft hyphen) and fold homoglyphs."""
    text = "".join(c for c in unicodedata.normalize("NFKC", text) if unicodedata.category(c) != "Cf")
    return text.lower().replace("i̇", "i").translate(_HOMOGLYPHS)


def lint_text(text: str, *, content: bool = True) -> list[tuple[str, str]]:
    """(claim, context) for every forbidden claim that is not itself negated ('... zəmanəti deyil', 'not proven')."""
    low = fold(text)
    found = []
    for name, pattern in FORBIDDEN + (CONTENT_ONLY if content else []):
        for m in pattern.finditer(low):
            if NEGATION_AFTER.match(low[m.end():m.end() + 48]) or NEGATION_BEFORE.search(low[max(0, m.start() - 30):m.start()]):
                continue
            found.append((name, low[max(0, m.start() - 20):m.end() + 20].replace("\n", " ")))
    return found


def _strings(obj):
    stack = [("", obj)]
    while stack:
        path, node = stack.pop()
        if isinstance(node, str):
            yield path, node
        elif isinstance(node, dict):
            stack.extend((k, v) for k, v in node.items() if k not in SKIP_KEYS)
        elif isinstance(node, list):
            stack.extend((path, v) for v in node)


def corpus(ctx: Ctx) -> list[tuple[str, str, bool]]:
    """(label, text, content?) of every learner-visible or exported string we can read offline."""
    rows = []
    for s in ctx.subjects():
        for part in ("lesson", "items"):
            if s.get(part):
                for key, text in _strings(s[part]):
                    rows.append((f"{s['node']}.{part}.{key}", text, True))
    handover = ctx.handover()
    if handover:
        rows.append(("handover.md", handover["md"], True))
        for key, text in _strings(handover["json"]):
            rows.append((f"handover.json.{key}", text, True))
    for name, text in ctx.hook("templates", _template_texts)().items():
        rows.append((f"template:{name}", text, False))
    rows.append(("receipt claim (en)", RECEIPT_CLAIM["text_en"], True))
    rows.append(("receipt claim (az)", RECEIPT_CLAIM["text_az"], True))
    return rows


def _template_texts() -> dict:
    src = RUNTIME_DIR.parent / "templates" / "react" / "src"
    out = {}
    if src.is_dir():
        for path in sorted(src.rglob("*")):
            if path.is_file() and path.suffix in {".jsx", ".js", ".md", ".html"}:
                out[path.relative_to(src).as_posix()] = path.read_text(encoding="utf-8", errors="replace")
    return out


def _claims(ctx: Ctx) -> Result:
    rows = ctx.hook("corpus", lambda: corpus(ctx))()
    problems = []
    for label, text, content in rows:
        for name, context in lint_text(text, content=content):
            problems.append(f"{label}: forbidden claim '{name}' (…{context}…)")
    return verdict(problems, f"{len(rows)} learner-visible string group(s) linted: no forbidden claim", strings=len(rows))


# Every sentence below carries a real claim next to a negator that is NOT bound to it (negation-bypass control).
BYPASS_SENTENCES = (
    "Mastery is guaranteed, and you do not need prior knowledge.",
    "This method is proven; it is not hard to follow.",
    "There is no doubt you will master vectors.",
    "Bu kurs ustalıq zəmanəti verir, çətin deyil.",
    "Results are guaranteed for everyone, not only beginners.",
)


def _control_claims():
    lesson = {"explanation": {"short": " ".join(BYPASS_SENTENCES), "body": "b"}}
    return Ctx.synthetic(subjects=[{"node": "n", "origin": "staged", "lesson": lesson, "items": None}], handover=None,
                         hooks={"templates": lambda: {}})


@probe("r09.claim_lint", "R9", control=_control_claims, title="no forbidden claim in lessons, items, handover or templates")
def claim_lint(ctx: Ctx) -> Result:
    return _claims(ctx)


def _counts(ctx: Ctx) -> Result:
    handover = ctx.handover()
    if handover is None:
        return na("no handover exists for this build yet; counts and caveats are checked once it does")
    from .. import studio_handover as ho
    declared, derived = handover["json"].get("nodes"), ho.derive_nodes(ctx.subjects())
    problems = []
    if declared != derived:
        diff = sorted(n for n in set(derived) | set(declared or {}) if (declared or {}).get(n) != derived.get(n))
        problems.append(f"declared counts differ from the counts derived from the staged artifacts: {diff[:6]}")
    verified = {item.get("id") if isinstance(item, dict) else item for item in handover["json"].get("not_verified") or []}
    for needed in REQUIRED_NOT_VERIFIED:
        if needed not in verified:
            problems.append(f"the 'not verified' list lacks '{needed}'")
    if not handover["json"].get("absent"):
        problems.append("the handover does not list what is absent")
    if handover["json"].get("receipt_claim") != RECEIPT_CLAIM["id"]:
        problems.append("the handover's receipt statement is not the invariants-under-probes claim")
    return verdict(problems, "declared counts equal the derived counts; the not-verified and absent lists are present")


def _control_counts():
    handover = {"json": {"nodes": {"n": {"items": 99}}, "not_verified": [], "absent": [], "receipt_claim": "quality_proven"},
                "md": ""}
    return Ctx.synthetic(handover=handover, subjects=[{"node": "n", "origin": "staged", "lesson": None, "items": []}])


@probe("r09.counts_and_caveats", "R9", control=_control_counts, title="declared == derived counts; caveats present")
def counts_and_caveats(ctx: Ctx) -> Result:
    return _counts(ctx)


def _receipt_claim(ctx: Ctx) -> Result:
    claim = ctx.hook("receipt_claim", RECEIPT_CLAIM)
    problems = []
    for lang in ("text_en", "text_az"):
        for name, _context in lint_text(claim[lang]):
            problems.append(f"the receipt statement ({lang}) makes a forbidden claim: {name}")
    low = claim["text_en"].lower()
    if "invariants hold" not in low and "not a statement of course quality" not in low:
        problems.append("the receipt statement does not say it only attests invariants under probes")
    if re.search(r"quality\s+(?:is\s+)?(?:proven|verified|assured|guaranteed)", low):
        problems.append("the receipt statement claims quality")
    return verdict(problems, "a passing receipt reads 'invariants hold under these probes', never 'quality proven'")


def _control_receipt_claim():
    return Ctx.synthetic(hooks={"receipt_claim": {"id": "x", "text_en": "Course quality is proven and guaranteed.",
                                                  "text_az": "Kursun keyfiyyəti sübut olunmuş və zəmanətlidir."}})


@probe("r09.receipt_statement", "R9", control=_control_receipt_claim, title="a receipt never claims quality")
def receipt_statement(ctx: Ctx) -> Result:
    return _receipt_claim(ctx)


PROBES = [claim_lint, counts_and_caveats, receipt_statement]
