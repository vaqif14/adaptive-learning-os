"""TEST FIXTURE — synthetic, non-sensitive course data for course-runtime tests.

The answer keys below are deliberately public test data. Real learner courses
keep reference solutions and hidden checks in each session's private vault and
never in the repository.
"""
import copy
import os
import tempfile
from pathlib import Path

ROADMAP = {
    "sources": ["https://example.org/vectors"],
    "status": "proposed",
    "nodes": [
        {"id": "vec-sim", "title": "Vektor oxşarlığı", "task": "İki vektorun istiqamət oxşarlığını hesabla.",
         "requires": [], "outcomes": ["Kosinus oxşarlığını hesablaya bilir"], "skills": ["dot product", "norm"],
         "practice": "dot və norm funksiyalarını yaz", "source_refs": ["r1"], "exam_domain": "Fixture domain",
         "pitfalls": ["Normallaşdırmanı unutmaq"], "stage": "Əsaslar"},
        {"id": "vec-search", "title": "Top-k axtarış", "task": "Sorğuya ən yaxın sənədləri tap.",
         "requires": ["vec-sim"], "outcomes": ["Top-k nəticə qaytarır"], "skills": ["ranking"],
         "practice": "kiçik axtarış funksiyası", "source_refs": ["r1"]},
    ],
}

SOURCE_TEXT = ("Fixture source. The cosine of the angle between two vectors is their dot product divided by the "
               "product of their lengths. Cosine similarity ignores vector length and compares direction only. "
               + "Padding sentence for length. " * 30)
QUOTE = "dot product divided by the product of their lengths"

LESSON = {
    "node_id": "vec-sim",
    "objective": "İki vektorun istiqamət oxşarlığını kosinusla ölçmək.",
    "success_criteria": ["Uzunluqdan asılı olmayan nəticə", "Sıfır vektorda xəta yox"],
    "explanation": {"short": "Kosinus oxşarlığı iki vektorun eyni istiqamətə nə qədər baxdığını ölçür, uzunluğu nəzərə almır.",
                    "body": ("Əvvəl hər cüt elementi vurub toplayırıq (dot product). Sonra bunu hər vektorun uzunluğunun "
                             "hasilinə bölürük. Nəticə -1 ilə 1 arasındadır: 1 eyni istiqamət, 0 perpendikulyar."),
                    "refs": [{"source_id": "src-fixture-001", "quote": QUOTE}]},
    "why": {"text": "Axtarışda sənədin uzunluğu yox, mənası (istiqaməti) vacibdir; kosinus bunu ayırır.",
            "refs": [{"supplementary": True, "note": "Fixture: pedagogical framing, not from the source."}]},
    "worked_example": {"title": "Əl ilə nümunə", "problem": "a=[1,0] və b=[1,1] üçün kosinusu hesabla.",
                       "steps": ["dot = 1*1 + 0*1 = 1", "|a| = 1, |b| = 1.414", "1 / 1.414 = 0.707"],
                       "result": "0.707", "refs": [{"source_id": "src-fixture-001", "quote": QUOTE}]},
    "alternative_explanation": {"text": ("Vektoru ox kimi düşün: iki ox eyni tərəfə baxırsa bucaq kiçikdir və kosinus 1-ə yaxındır. "
                                         "Oxun uzunluğu bucağı dəyişmir."),
                                "example": "[2,0] və [5,0] eyni istiqamətdədir: kosinus 1.",
                                "refs": [{"supplementary": True, "note": "Fixture analogy."}]},
    "self_explanation_prompt": "Öz sözlərinlə izah et: niyə dot product-u uzunluqlara bölürük?",
    "self_explanation_key_terms": ["dot", "uzunluq", "istiqamət", "bölmək"],
    "practice": {
        "task_id": "dot-norm", "entry": "practice.py",
        "prompt_md": "`dot(a, b)` və `norm(a)` funksiyalarını yaz. `dot` cüt elementləri vurub toplayır, `norm` vektorun uzunluğudur.",
        "starter_code": "import math\n\ndef dot(a, b):\n    pass\n\ndef norm(a):\n    pass\n",
        "hints": ["dot üçün zip(a, b) istifadə et.", "norm = sqrt(dot(a, a))."],
        "checks": [
            {"id": "dot-basic", "name": "dot([1,2],[3,4]) = 11", "code": "assert dot([1,2],[3,4]) == 11",
             "fail_feedback": "Cüt elementləri vurub topla.", "probe": "dot([1,2],[3,4])", "expected_text": "11"},
            {"id": "norm-basic", "name": "norm([3,4]) = 5", "code": "assert abs(norm([3,4]) - 5) < 1e-9",
             "fail_feedback": "Uzunluq kökaltıdır.", "probe": "norm([3,4])", "expected_text": "5.0"},
        ],
        "misconceptions": [
            {"id": "add-not-multiply", "name": "Vurmaq əvəzinə toplamaq",
             "detector": "assert dot([1,2],[3,4]) == 10",
             "feedback": "Görünür, elementləri vurmaq əvəzinə hamısını toplamısan (1+2+3+4=10)."},
            {"id": "norm-no-sqrt", "name": "Kökaltını unutmaq",
             "detector": "assert norm([3,4]) == 25",
             "feedback": "norm 25 qaytarır: kvadratların cəmindən kök almağı unutmusan."},
        ],
        "reference_solution": ("import math\n\ndef dot(a, b):\n    return sum(x * y for x, y in zip(a, b))\n\n"
                               "def norm(a):\n    return math.sqrt(dot(a, a))\n"),
        "wrong_examples": {"add-not-multiply": "def dot(a, b):\n    return sum(a) + sum(b)\n\ndef norm(a):\n    return 0\n"},
    },
}

COSINE_REF = ("import math\n\ndef cosine(a, b):\n    d = sum(x * y for x, y in zip(a, b))\n"
              "    na = math.sqrt(sum(x * x for x in a))\n    nb = math.sqrt(sum(y * y for y in b))\n"
              "    if na == 0 or nb == 0:\n        return 0.0\n    return d / (na * nb)\n")
NEAREST_REF = (COSINE_REF + "\n\ndef most_similar(query, docs):\n"
               "    scores = [cosine(query, d) for d in docs]\n    return scores.index(max(scores))\n")

ITEMS = {
    "node_id": "vec-sim",
    "items": [
        {"item_id": "cosine-a", "title": "Kosinus funksiyası", "scope": "independent_reproduction", "difficulty": 1,
         "entry": "solution.py",
         "prompt_md": "Yeni tapşırıq: `cosine(a, b)` funksiyasını yaz. Sıfır uzunluqlu vektor üçün 0.0 qaytarsın.",
         "starter_code": "import math\n\ndef cosine(a, b):\n    pass\n",
         "checks": [
             {"id": "same", "name": "Eyni istiqamət", "category": "core",
              "code": "assert abs(cosine([1,0],[1,0]) - 1) < 1e-9", "fail_feedback": "Eyni vektor 1 verməlidir."},
             {"id": "ortho", "name": "Perpendikulyar", "category": "core",
              "code": "assert abs(cosine([1,0],[0,1])) < 1e-9", "fail_feedback": "Perpendikulyar vektor 0 verməlidir."},
             {"id": "zero", "name": "Sıfır vektor", "category": "edge",
              "code": "assert cosine([0,0],[1,1]) == 0.0", "fail_feedback": "Sıfır vektor üçün 0.0 qaytar."},
             {"id": "scale", "name": "Görünməyən miqyas halı", "category": "transfer",
              "code": "assert abs(cosine([3,4],[6,8]) - 1) < 1e-9 and abs(cosine([1,0],[-2,0]) + 1) < 1e-9",
              "fail_feedback": "Uzunluq nəticəyə təsir etməməlidir."},
         ],
         "misconceptions": [{"id": "no-normalize", "name": "Normallaşdırma yoxdur",
                             "detector": "assert abs(cosine([3,0],[2,0]) - 6) < 1e-9",
                             "feedback": "Funksiya xam dot product qaytarır; uzunluqlara bölmək lazımdır."}],
         "reference_solution": COSINE_REF,
         "wrong_examples": {"no-normalize": "def cosine(a, b):\n    return sum(x * y for x, y in zip(a, b))\n"}},
        {"item_id": "nearest-b", "title": "Ən oxşar sənəd", "scope": "near_transfer", "difficulty": 2,
         "entry": "solution.py",
         "prompt_md": "Yeni tapşırıq: `most_similar(query, docs)` sorğuya istiqamətcə ən yaxın sənədin indeksini qaytarsın.",
         "starter_code": "import math\n\ndef most_similar(query, docs):\n    pass\n",
         "checks": [
             {"id": "basic", "name": "Sadə hal", "category": "core",
              "code": "assert most_similar([1,0], [[0,1],[1,0]]) == 1", "fail_feedback": "Ən oxşar indeksi qaytar."},
             {"id": "magnitude", "name": "Böyük uzunluqlu yanlış sənəd", "category": "transfer",
              "code": "assert most_similar([1,0], [[0.9,0.1],[5,5]]) == 0",
              "fail_feedback": "Uzun vektor istiqamətcə uzaq olsa qazanmamalıdır."},
         ],
         "misconceptions": [{"id": "dot-ranking", "name": "Dot product ilə sıralama",
                             "detector": "assert most_similar([1,0], [[0.9,0.1],[5,5]]) == 1",
                             "feedback": "Sıralamada xam dot product istifadə olunub; uzun vektor üstünlük qazanır."}],
         "reference_solution": NEAREST_REF},
    ],
}

PRACTICE_WRONG_ADD = "import math\n\ndef dot(a, b):\n    return sum(a) + sum(b)\n\ndef norm(a):\n    return math.sqrt(dot(a, a))\n"
PRACTICE_CORRECT = LESSON["practice"]["reference_solution"]
EXAM_CORRECT = COSINE_REF
EXAM_NO_NORMALIZE = "def cosine(a, b):\n    return sum(x * y for x, y in zip(a, b))\n"


def manifest_for(cache_dir: Path) -> dict:
    """A validated single-source manifest with its cached text (mirrors validate_resources output)."""
    import hashlib
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "src-fixture-001.txt").write_text(SOURCE_TEXT, encoding="utf-8")
    return {"schema_version": 1, "provider": "notebooklm", "cli_version": "fixture", "manifest_version": "sm-fixture",
            "notebook_id": "00000000-0000-4000-8000-000000000000", "validated_at": "2026-10-12T00:00:00+00:00",
            "complete": True, "summary": {"validated": 1, "expected": 1}, "unexpected_sources": [],
            "resources": [{"key": "r1", "expected": {"kind": "url", "locator": "https://example.org/vectors"},
                           "source_id": "src-fixture-001", "title": "Fixture source", "status": "validated",
                           "issues": [], "duplicates": [], "char_count": len(SOURCE_TEXT.strip()),
                           "content_sha256": hashlib.sha256(SOURCE_TEXT.encode()).hexdigest(), "stale": False}]}


def roadmap():
    return copy.deepcopy(ROADMAP)


def lesson():
    return copy.deepcopy(LESSON)


def items():
    return copy.deepcopy(ITEMS)


class IsolatedState:
    """Context manager: keyed-ledger trust heads go to a temp dir, not ~/.config."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("ALEARN_STATE_DIR")
        os.environ["ALEARN_STATE_DIR"] = self._tmp.name
        return self

    def __exit__(self, *exc):
        if self._old is None:
            os.environ.pop("ALEARN_STATE_DIR", None)
        else:
            os.environ["ALEARN_STATE_DIR"] = self._old
        self._tmp.cleanup()
