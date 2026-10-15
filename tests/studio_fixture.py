"""TEST FIXTURE - synthetic Course Studio helpers: a fake author and a course-session base case.

Content is synthetic and public. The fake author answers studio orders from data built on
course_fixture.py, so the whole studio loop runs with no network and no real agent.
"""
import copy
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/adaptive-learn"))
sys.path.insert(0, str(ROOT / "tests"))

from runtime.contracts import LearningContract  # noqa: E402
from runtime.course import CourseKernel  # noqa: E402
from runtime.session import SessionKernel  # noqa: E402
from runtime.studio import StudioKernel  # noqa: E402
from course_fixture import (CONCEPT_ITEMS, IsolatedState, QUOTE, SOURCE_TEXT, items as sim_items_base,  # noqa: E402
                            lesson as sim_lesson_v1, manifest_for, roadmap)
from runtime.course_content import WALKTHROUGH_LABEL_AZ  # noqa: E402

SRC = "src-fixture-001"


class FakeClock:
    """Mutable, timezone-aware clock for deterministic lease/expiry tests."""

    def __init__(self, start=None):
        self.now = start or datetime(2026, 10, 13, 9, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def third_sim_item():
    item = copy.deepcopy(CONCEPT_ITEMS["items"][0])
    item.update(item_id="sim-concept-c", scope="far_transfer")
    return item


def sim_items():
    payload = sim_items_base()
    payload["items"].append(third_sim_item())
    return payload


# Practice-first parts (studio-targets-v2). Azerbaijani wording is synthetic fixture text; a native speaker
# should review the phrasing ("nümayiş", "pilləli təlim") before any real course reuses it.
def sim_walkthrough():
    """A python walkthrough on a DIFFERENT instance (the mean of a list) than the dot/norm practice and the cosine exams."""
    return {
        "title": "Siyahının ortasını hesablamaq",
        "scenario_md": "Üç imtahan balından ibarət siyahı var. Məqsəd: ballar siyahısının ortasını addım-addım hesablamaq.",
        "language": "python",
        "steps": [
            {"explain_md": "Əvvəl balları siyahıya yazırıq.", "code": "scores = [4, 8, 6]"},
            {"explain_md": "Sonra balların cəmini tapıb çap edirik.", "code": "total = sum(scores)\nprint(total)",
             "checkpoint": {"expected_output": "18"},
             "predict": {"prompt": "Cəm neçə çıxacaq?", "reveal": "4 + 8 + 6 = 18"}},
            {"explain_md": "Cəmi bal sayına bölürük.", "code": "average = total / len(scores)\nprint(average)",
             "checkpoint": {"expected_output": "18\n6.0"}},
            {"explain_md": "Eyni hesabı təkrar istifadə üçün funksiyaya salırıq.",
             "code": "def mean(values):\n    return sum(values) / len(values)\n\nprint(mean(scores))",
             "checkpoint": {"expected_output": "18\n6.0\n6.0"}},
        ],
        "mistake": {"step": 3, "wrong_code": "average = total / scores",
                    "symptom": "Kod xəta verir: ədədi siyahıya bölmək olmur.",
                    "fix_explain": "Bölən siyahının özü yox, uzunluğu (len) olmalıdır."},
        "transcript_md": ("Bu nümayişdə ballar siyahısı yaradılır, cəm hesablanır, cəm bal sayına bölünür və nəticə "
                          "funksiyaya çevrilir. Hər addımın çap etdiyi nəticə yoxlanılır."),
        "refs": [{"source_id": SRC, "quote": QUOTE}],
        "label": WALKTHROUGH_LABEL_AZ,
    }


def sim_ladder():
    return {
        "worked": {"code_or_steps": "def mean(values):\n    return sum(values) / len(values)\n",
                   "explain_md": "Bu həll olunmuş nümunədir: siyahının ortası cəmin say-a bölünməsidir."},
        "completion": {"template": ("import math\n\ndef dot(a, b):\n    return sum(___ for x, y in zip(a, b))\n\n"
                                    "def norm(a):\n    return math.sqrt(___)\n"),
                       "blanks": ["x * y", "dot(a, a)"],
                       "explain_md": "Birinci boşluğa cüt elementlərin hasilini, ikinciyə vektorun özü ilə skalyar hasilini yaz."},
        "independent": "practice",
    }


def sim_lesson():
    lesson = sim_lesson_v1()
    lesson.update(walkthrough=sim_walkthrough(), ladder=sim_ladder(), hint_ceiling=1)
    return lesson


def search_walkthrough():
    """A reasoning walkthrough (language 'none') for a concept node; a different instance than the practice/exams."""
    return {
        "title": "Ən isti günləri seçmək",
        "scenario_md": "Dörd günlük temperatur ölçməsi var. Məqsəd: ən isti iki günü seçmək.",
        "language": "none",
        "steps": [
            {"explain_md": "Əvvəl ölçmələri yazırıq: 21, 27, 24 və 30 dərəcə."},
            {"explain_md": "Sonra ölçmələri böyükdən kiçiyə düzürük: 30, 27, 24, 21.",
             "predict": {"prompt": "Düzəndən sonra siyahının başında hansı ölçmə olacaq?",
                         "reveal": "30; ən böyük dəyər birinci gəlir."}},
            {"explain_md": "İki gün istədiyimiz üçün düzülmüş siyahının ilk iki elementini götürürük: 30 və 27."},
        ],
        "mistake": {"step": 2, "symptom": "Siyahını artan sırada düzsək, ilk iki element ən soyuq günlər olur.",
                    "fix_explain": "Sıralamanı azalan istiqamətdə aparmaq lazımdır ki, ən böyük dəyərlər başda olsun."},
        "transcript_md": ("Bu nümayişdə ölçmələr azalan sırada düzülür və ilk iki element seçilir. "
                          "Eyni üsul istənilən ölçmə siyahısına tətbiq olunur."),
        "refs": [{"source_id": SRC, "quote": QUOTE}],
        "label": WALKTHROUGH_LABEL_AZ,
    }


def search_ladder():
    return {
        "worked": {"code_or_steps": ["Ölçmələr 21, 27 və 24 olsun.", "Azalan düzülüş: 27, 24, 21; ilk bir element 27-dir."],
                   "explain_md": "Bu həll olunmuş nümunədir: əvvəl düzmək, sonra ilk elementi götürmək."},
        "completion": {"template": "Ölçmələr ___ sırada düzülür və yalnız ilk ___ element saxlanır.",
                       "blanks": ["azalan", "iki"],
                       "explain_md": "Birinci boşluq düzülüşün istiqamətidir, ikincisi saxlanan elementlərin sayı."},
        "independent": "practice",
    }


def search_lesson():
    lesson = search_lesson_v1()
    lesson.update(walkthrough=search_walkthrough(), ladder=search_ladder(), hint_ceiling=1)
    return lesson


def search_lesson_v1():
    return {
        "node_id": "vec-search",
        "objective": "Sorğuya ən yaxın sənədləri xala görə sıralayıb ilk k nəticəni seçmək.",
        "success_criteria": ["Sənədləri xala görə azalan sıralayır", "Yalnız ilk k nəticəni saxlayır"],
        "explanation": {"short": "Top-k axtarış hər sənədə xal verir, xalları azalan düzür və ən yaxşı k sənədi saxlayır.",
                        "body": ("Əvvəl hər sənəd sorğu ilə müqayisə olunub bir xal alır. Sonra bütün xallar böyükdən kiçiyə "
                                 "düzülür və yalnız ilk k nəticə qaytarılır. Sənəd sayı k-dan azdırsa, hamısı qaytarılır."),
                        "refs": [{"source_id": SRC, "quote": QUOTE}]},
        "why": {"text": "Böyük sənəd bazasında istifadəçi yalnız ən uyğun bir neçə nəticəyə baxır; k bu sayı məhdudlaşdırır.",
                "refs": [{"supplementary": True, "note": "Fixture: pedagogical framing."}]},
        "worked_example": {"title": "Üç sənədli nümunə", "problem": "Xallar d1=0.2, d2=0.9, d3=0.5 olduqda k=2 üçün cavabı tap.",
                           "steps": ["Xalları azalan düz: d2, d3, d1", "İlk iki elementi götür: d2 və d3"],
                           "result": "d2, d3", "refs": [{"source_id": SRC, "quote": QUOTE}]},
        "alternative_explanation": {"text": ("Bunu yarış cədvəli kimi düşün: hamı xal toplayır, cədvəl xala görə düzülür və "
                                             "podiuma yalnız ilk k iştirakçı çıxır. Qalanlar siyahıdan çıxmır, sadəcə göstərilmir."),
                                    "example": "Beş iştirakçıdan k=3 üçün ilk üçü podiuma çıxır.",
                                    "refs": [{"supplementary": True, "note": "Fixture analogy."}]},
        "self_explanation_prompt": "Öz sözlərinlə izah et: niyə xalları əvvəl düzmək lazımdır?",
        "self_explanation_key_terms": ["xal", "sıralama", "k"],
        "practice": {
            "task_id": "topk-practice", "kind": "concept",
            "prompt_md": "Bələdçili praktika: ilk k nəticənin necə seçildiyini yoxlayan sualı cavablandır.",
            "hints": ["Əvvəl hansı addım gəlir: seçmək, yoxsa düzmək?", "Düzüləndən sonra neçə element qalır?"],
            "questions": [{"id": "p1", "type": "single_choice", "category": "core",
                           "prompt": "Xallar düzüldükdən sonra k=2 üçün neçə nəticə qaytarılır?",
                           "options": [{"id": "a", "text": "Yalnız bir"}, {"id": "b", "text": "İki"},
                                       {"id": "c", "text": "Hamısı"}],
                           "answer": "b", "fail_feedback": "k nəticə sayını müəyyən edir.",
                           "misconceptions": {"c": "return-everything"}}],
            "misconceptions": [{"id": "return-everything", "name": "Bütün sənədləri qaytarmaq",
                                "feedback": "k məhz qaytarılan sənəd sayını məhdudlaşdırır; hamısını qaytarmaq lazım deyil."}],
        },
    }


def search_items():
    return {"node_id": "vec-search", "items": [
        {"item_id": "search-rank-a", "kind": "concept", "title": "Sıralama anlayışı", "scope": "independent_reproduction",
         "difficulty": 1,
         "prompt_md": "Yeni tapşırıq: axtarış nəticələrinin seçilməsi barədə suallara cavab ver.",
         "questions": [
             {"id": "s1", "type": "single_choice", "category": "core", "prompt": "Nəticələr hansı sırada düzülür?",
              "options": [{"id": "a", "text": "Xalı artan sırada"}, {"id": "b", "text": "Xalı azalan sırada"},
                          {"id": "c", "text": "Təsadüfi sırada"}],
              "answer": "b", "fail_feedback": "Ən yüksək xal birinci gəlməlidir.", "misconceptions": {"a": "ascending-order"}},
             {"id": "s2", "type": "multi_choice", "category": "transfer", "prompt": "Hansı ifadələr doğrudur?",
              "options": [{"id": "a", "text": "k böyüdükcə qaytarılan nəticə sayı artır"},
                          {"id": "b", "text": "k sənəd sayından böyük ola bilməz"},
                          {"id": "c", "text": "k=1 yalnız ən yaxşı nəticəni verir"}],
              "answer": ["a", "c"], "fail_feedback": "k sənəd sayından böyük olarsa hamısı qaytarılır.",
              "misconceptions": {"b": "k-bounded"}}],
         "misconceptions": [
             {"id": "ascending-order", "name": "Artan sıralama", "feedback": "Artan sıralama ən zəif nəticəni birinci qoyur."},
             {"id": "k-bounded", "name": "k-nın məhdud olduğunu düşünmək", "feedback": "k sənəd sayından böyük ola bilər, onda hamısı qaytarılır."}]},
        {"item_id": "search-scenario-b", "kind": "scenario", "title": "Kitabxana axtarışı", "scope": "near_transfer",
         "difficulty": 2,
         "prompt_md": "Yeni tapşırıq: aşağıdakı vəziyyət üzrə sualları cavablandır.",
         "scenario_md": ("Kitabxana sistemi sorğuya görə yüz kitabı xallandırır və istifadəçiyə yalnız beş kitab göstərir. "
                         "İstifadəçi altıncı kitabı da görmək istəyir."),
         "questions": [
             {"id": "t1", "type": "numeric", "category": "core", "prompt": "Sistem neçə kitab göstərir?",
              "answer": 5, "tolerance": 0, "fail_feedback": "Ssenaridə göstərilən kitab sayına bax."},
             {"id": "t2", "type": "single_choice", "category": "transfer", "prompt": "Altıncı kitabı görmək üçün nə dəyişməlidir?",
              "options": [{"id": "a", "text": "Xalların hesablanması"}, {"id": "b", "text": "k dəyəri"},
                          {"id": "c", "text": "Kitabların sayı"}],
              "answer": "b", "fail_feedback": "Göstərilən nəticə sayını k müəyyən edir.", "misconceptions": {"a": "rescore-needed"}}],
         "misconceptions": [{"id": "rescore-needed", "name": "Yenidən xallandırma",
                             "feedback": "Xallar eyni qalır; yalnız kəsilmə həddi (k) dəyişir."}]},
        {"item_id": "search-explain-c", "kind": "free_text", "title": "Öz sözlərinlə", "scope": "far_transfer", "difficulty": 2,
         "prompt_md": "Yeni tapşırıq: niyə yalnız ilk k nəticəni göstəririk? 2-4 cümlə ilə izah et.",
         "rubric": [{"id": "r-limit", "criterion": "Nəticə sayının məhdudlaşdırılma səbəbini izah edir"},
                    {"id": "r-order", "criterion": "Seçimin əvvəlcədən sıralanmış xallara əsaslandığını göstərir"}],
         "exemplar": "İstifadəçi çox nəticəyə baxmır; xalla düzülmüş siyahıdan ən yaxşı k göstərilir.",
         "misconceptions": [{"id": "list-only", "name": "Yalnız sadalamaq",
                             "feedback": "Səbəb və sıralama əlaqəsini izah etmək lazımdır, sadəcə sadalamaq kifayət deyil."}]},
    ]}


LIBRARY = {
    ("lesson", "vec-sim"): sim_lesson, ("items", "vec-sim"): sim_items,
    ("lesson", "vec-search"): search_lesson, ("items", "vec-search"): search_items,
}


class FakeAuthor:
    """A well-behaved author: reads an order dict, returns the payload for (kind, node)."""

    def __init__(self, library=None, defects=None):
        self.library = dict(library or LIBRARY)
        self.defects = defects or {}      # {(kind, node): [payload_for_attempt_1, ...]} served first
        self.seen = []                    # every order this author was handed (for need-to-know checks)

    def payload_for(self, order):
        self.seen.append(order)
        key = (order["kind"], order["node_id"])
        queue = self.defects.get(key)
        if queue:
            return copy.deepcopy(queue.pop(0))
        return self.library[key]()

    def answer(self, studio, sid, task, *, via_inbox=False):
        """Submit the payload for an ``order`` task, inline or through the inbox result file."""
        order = task["order"]
        payload = self.payload_for(order)
        if via_inbox:
            out = Path(task["inbox"]) / "out" / "result.json"
            out.write_text(json.dumps(payload), encoding="utf-8")
            return studio.submit(sid, order["job_id"], order["order_id"])
        return studio.submit(sid, order["job_id"], order["order_id"], payload)

    def run(self, studio, sid, job_id=None, *, limit=40, via_inbox=False):
        """Drive next-task / submit until the studio stops handing out orders. Returns the last task."""
        for _ in range(limit):
            task = studio.next_task(sid, job_id)
            if task["state"] != "order":
                return task
            self.answer(studio, sid, task, via_inbox=via_inbox)
        raise AssertionError("studio loop did not settle")


class StudioCase(unittest.TestCase):
    """A course session with the 2-node fixture roadmap PROPOSED (not approved) and a fresh studio."""

    propose = True

    def setUp(self):
        # The sandbox self-tests have a 3 s budget; widen it so a loaded CI machine cannot flake the suite.
        import runtime.course_content as cc
        self._verifier_timeout = cc._VERIFIER.timeout_sec
        cc._VERIFIER.timeout_sec = 30.0
        self.iso = IsolatedState().__enter__()
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name)
        self.sk = SessionKernel(self.ws)
        self.sid = self.sk.start("Vektorlar", LearningContract(goal="Vektor oxşarlığını hesablamaq",
                                                               stated_background="Python əsasları"))["session_id"]
        self.d = self.sk.dir(self.sid)
        self.clock = FakeClock()
        self.ck = CourseKernel(self.ws, clock=self.clock)
        # Stage A/B tests exercise authoring and validation: the rules gate (stage C) is off here and covered by the
        # test_v802_* files, which build with the default (gated) kernel.
        self.st = StudioKernel(self.ws, clock=self.clock, gate=False)
        self.ck.init(self.sid)
        self.ck.record_sources(self.sid, manifest_for(self.d / "sources"),
                               [{"key": "r1", "kind": "url", "locator": "https://example.org/vectors"}])
        if self.propose:
            self.ck.propose_roadmap(self.sid, roadmap())

    def tearDown(self):
        import runtime.course_content as cc
        cc._VERIFIER.timeout_sec = self._verifier_timeout
        self.tmp.cleanup()
        self.iso.__exit__()

    # helpers
    def records(self, record_type=None):
        recs = self.sk.ledger(self.sid).verified_records()
        return [r for r in recs if record_type is None or r["record_type"] == record_type]

    def studio_root(self):
        return self.d / "studio"

    def new_manifest_text(self, extra=" Extra sentence that changes the hash."):
        """Re-record the source manifest with different text (same quote still present)."""
        import hashlib
        text = SOURCE_TEXT + extra
        manifest = manifest_for(self.d / "sources")
        manifest["resources"][0]["content_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        manifest["manifest_version"] = "sm-fixture-2"
        staging = self.d / "sources-staging" / "stg_changed"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "src-fixture-001.txt").write_text(text, encoding="utf-8")
        return manifest, staging
