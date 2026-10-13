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


CONCEPT_NODE = {"id": "vec-ideas", "title": "Oxşarlıq anlayışları", "task": "Kosinus oxşarlığının mənasını izah et.",
                "requires": [], "outcomes": ["Kosinusun istiqaməti ölçdüyünü izah edə bilir"],
                "skills": ["istiqamət", "normallaşdırma"], "practice": "seçim və sıralama tapşırıqları",
                "source_refs": ["r1"], "exam_domain": "Fixture concept domain"}

CONCEPT_ITEMS = {
    "node_id": "vec-ideas",
    "items": [
        {"item_id": "ideas-check-a", "kind": "concept", "title": "Anlayış yoxlaması", "scope": "near_transfer",
         "difficulty": 1,
         "prompt_md": "Yeni tapşırıq: kosinus oxşarlığı haqqında aşağıdakı suallara cavab ver.",
         "questions": [
             {"id": "q1", "type": "single_choice", "category": "core", "prompt": "Kosinus oxşarlığı nəyi ölçür?",
              "options": [{"id": "a", "text": "Vektorların uzunluq fərqini"}, {"id": "b", "text": "Ədədlərin cəmini"},
                          {"id": "c", "text": "Vektorların istiqamət yaxınlığını"}],
              "answer": "c", "fail_feedback": "Kosinus uzunluğu yox, istiqaməti müqayisə edir.",
              "misconceptions": {"a": "length-not-direction"}},
             {"id": "q2", "type": "multi_choice", "category": "core", "prompt": "Hansı ifadələr doğrudur?",
              "options": [{"id": "a", "text": "Eyni istiqamətli vektorların kosinusu 1-dir"},
                          {"id": "b", "text": "Kosinus həmişə müsbətdir"},
                          {"id": "c", "text": "Perpendikulyar vektorların kosinusu 0-dır"}],
              "answer": ["a", "c"], "fail_feedback": "Əks istiqamətdə kosinus mənfi olur.",
              "misconceptions": {"b": "always-positive"}},
             {"id": "q3", "type": "ordering", "category": "transfer",
              "prompt": "Axtarışda sənədləri seçmək addımlarını sırala.",
              "options": [{"id": "s1", "text": "Hər sənədlə kosinusu hesabla"},
                          {"id": "s2", "text": "Sorğunu vektora çevir"},
                          {"id": "s3", "text": "Ən yüksək nəticələri seç"}],
              "answer": ["s2", "s1", "s3"], "fail_feedback": "Əvvəl sorğu vektora çevrilməlidir."},
         ],
         "misconceptions": [
             {"id": "length-not-direction", "name": "Uzunluğu istiqamətlə qarışdırmaq",
              "feedback": "Kosinus vektorun uzunluğunu nəzərə almır; yalnız bucağa baxır."},
             {"id": "always-positive", "name": "Kosinusun həmişə müsbət olduğunu düşünmək",
              "feedback": "Əks istiqamətli vektorlarda kosinus -1-ə yaxınlaşır."}]},
        {"item_id": "ideas-explain-b", "kind": "free_text", "title": "Öz sözlərinlə izah", "scope": "independent_reproduction",
         "difficulty": 2,
         "prompt_md": "Yeni tapşırıq: niyə kosinus oxşarlığında dot product uzunluqlara bölünür? 2-4 cümlə yaz.",
         "rubric": [{"id": "r-normalize", "criterion": "Uzunluqlara bölmənin (normallaşdırmanın) rolunu izah edir"},
                    {"id": "r-direction", "criterion": "Nəticənin istiqaməti ölçdüyünü göstərir"}],
         "exemplar": "Dot product uzun vektorlarda böyüyür; uzunluqlara böləndə yalnız istiqamət qalır.",
         "misconceptions": [{"id": "keyword-only", "name": "Yalnız termin sadalamaq",
                             "feedback": "Terminləri sadalamaq kifayət deyil; səbəb-nəticəni izah et."}]},
    ],
}

CONCEPT_LESSON = {
    "node_id": "vec-ideas",
    "objective": "Kosinus oxşarlığının istiqaməti ölçdüyünü izah etmək.",
    "success_criteria": ["Uzunluq ilə istiqaməti ayırır", "Əks istiqamətin mənfi nəticə verdiyini bilir"],
    "explanation": {"short": "Kosinus oxşarlığı iki vektorun nə qədər eyni tərəfə baxdığını göstərir, uzunluqları nəzərə almır.",
                    "body": ("Dot product uzun vektorlarda böyüyür. Onu iki uzunluğun hasilinə böləndə nəticə -1 ilə 1 "
                             "arasına düşür: 1 eyni istiqamət, 0 perpendikulyar, -1 əks istiqamət deməkdir."),
                    "refs": [{"source_id": "src-fixture-001", "quote": QUOTE}]},
    "why": {"text": "Axtarış sistemi sənədin uzunluğuna yox, mənasına (istiqamətinə) görə seçməlidir.",
            "refs": [{"supplementary": True, "note": "Fixture: pedagogical framing."}]},
    "worked_example": {"title": "Nümunə: üç vektor", "problem": "u=[2,0], v=[5,0], w=[0,3] üçün hansı cüt eyni istiqamətdədir?",
                       "steps": ["u və v eyni oxdadır: kosinus 1", "u və w perpendikulyardır: kosinus 0"],
                       "result": "u ilə v", "refs": [{"source_id": "src-fixture-001", "quote": QUOTE}]},
    "alternative_explanation": {"text": ("İki oxu düşün: eyni tərəfə baxırlarsa bucaq sıfırdır və kosinus 1 olur. "
                                         "Oxlardan biri uzun olsa da bucaq dəyişmir."),
                                "example": "Qısa və uzun şimal oxu: kosinus 1.",
                                "refs": [{"supplementary": True, "note": "Fixture analogy."}]},
    "self_explanation_prompt": "Öz sözlərinlə izah et: niyə uzun vektor daha oxşar sayılmamalıdır?",
    "self_explanation_key_terms": ["istiqamət", "uzunluq", "bölmək"],
    "practice": {
        "task_id": "ideas-practice", "kind": "concept",
        "prompt_md": "Bələdçili praktika: kosinusun mənasını yoxlayan sualı cavablandır.",
        "hints": ["Eyni istiqamət bucağın sıfır olması deməkdir.", "cos(0) neçədir?"],
        "questions": [{"id": "p1", "type": "single_choice", "category": "core",
                       "prompt": "Eyni istiqamətli iki vektorun kosinusu neçədir?",
                       "options": [{"id": "a", "text": "0"}, {"id": "b", "text": "1"}, {"id": "c", "text": "-1"}],
                       "answer": "b", "fail_feedback": "Bucaq sıfırdırsa kosinus 1-dir.",
                       "misconceptions": {"a": "ortho-confusion"}}],
        "misconceptions": [{"id": "ortho-confusion", "name": "Eyni istiqaməti perpendikulyarla qarışdırmaq",
                            "feedback": "0 perpendikulyar vektorlar üçündür; eyni istiqamətdə nəticə 1 olur."}],
    },
}


def concept_roadmap():
    plan = copy.deepcopy(ROADMAP)
    plan["nodes"].append(copy.deepcopy(CONCEPT_NODE))
    return plan


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
