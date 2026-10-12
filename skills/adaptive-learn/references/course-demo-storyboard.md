# Course demo — screen-recording storyboard (≈5 min 30 s)

Everything shown is real system behaviour recorded on 2026-10-12 against the
installed NotebookLM CLI 0.7.3 and the local course app, except where a frame
is labelled **HAZIRLANMIŞ CAVAB (DEMO)**: those learner answers were written by
the E2E driver (`tests/e2e/course_ui_e2e.py`) to make the run repeatable. No
delayed-retention result, learner study or efficacy number is shown, because
none exists yet.

Artifacts (local, gitignored): `.learning/demo-nca-genl/` — `target.json`,
`resources-v3.json`, `roadmap.json`, `lesson-embeddings.json`, `e2e-run4/*.png`,
`e2e-run4/*-log.json`, `e2e-run4/export.json`. Session: run 4
(`sid4.txt`); runs 1–3 are kept as earlier, aborted takes (driver fixes), not
deleted.

| # | Time | Screen | Exact narration (Azerbaijani) |
|---|------|--------|-------------------------------|
| 1 | 0:00–0:30 | Terminal: `alearn course target …` output; NVIDIA page with the five weighted domains | “Məqsəd konkretdir: NVIDIA-Certified Associate: Generative AI LLMs, kodu NCA-GENL. Rəsmi səhifəni bu gün yoxladıq: beş bölmə və onların çəkiləri — 30, 14, 22, 24 və 10 faiz. Səhifə imtahan versiyasını göstərmir, ona görə sistem də ‘versiya dərc olunmayıb’ yazır və bundan artıq iddia etmir.” |
| 2 | 0:30–1:05 | Terminal: `course sources-capabilities` (auth: ok, credentials not shown) → `sources-validate`: 5/5 validated, one unexpected `6.pdf` | “Mənbələr quraşdırılmış NotebookLM CLI ilə notebook-a əlavə olunub. Sistem say ilə kifayətlənmir: hər mənbənin idxalını və həqiqətən oxunan mətnini ayrıca yoxlayır. Bir dərslik fəslinin linki eyni qalsa da məzmunu dəyişmişdi — bunu ‘məzmun gözlənilən mövzu deyil’ kimi qeyd etdi, düzgün fəsli əlavə etdik, köhnəsi isə gizlədilmədən ‘gözlənilməyən mənbə’ kimi görünür.” |
| 3 | 1:05–1:40 | App `#/roadmap` (01-roadmap-desktop.png, 02-roadmap-detail-keyboard.png, 03-roadmap-mobile.png) | “Kurs başlamazdan əvvəl yol xəritəsi gəlir: on iki mərhələ, hər biri bacarıq, nəticə, ilkin şərt, praktika və mənbə ilə. Klaviatura ilə gəzmək olur, telefonda siyahıya çevrilir. Dərs linkini birbaşa açmağa çalışsam belə, təsdiq olmadan sistem məni yol xəritəsinə qaytarır.” |
| 4 | 1:40–2:00 | Approval form → 04-mission-after-approval.png | “Vaxtı da birlikdə razılaşırıq: həftədə üç gün, yüz iyirmi dəqiqə. ‘Təsdiq edirəm’ — bu versiya və təsdiq anı jurnala yazılır. Yol xəritəsi sonradan mahiyyətcə dəyişsə, təsdiq yenidən istəniləcək.” |
| 5 | 2:00–2:15 | Mission card | “İndi bir konkret missiya var: embedding-lər və kosinus oxşarlığı. Səbəb də yazılıb: ilkin şərti yoxdur, imtahanın Core ML bölməsinə aiddir. Başlanğıc testdə **HAZIRLANMIŞ CAVAB (DEMO)** ‘10’ idi — yəni dot product-da elementlər toplanıb.” |
| 6 | 2:15–2:55 | 05-lesson-entry.png → 06-explanation.png → 07-worked-example.png → 08-self-explanation.png | “Öyrənən ‘Bilmirəm’ deyir. Qısa izah gəlir və hər mühüm iddianın altında mənbədən dəqiq sitat var; mənbədə olmayan bənzətmə ‘əlavə izah’ kimi işarələnib. Sonra ayrıca, əl ilə nümunə — imtahan tapşırığı deyil. Sonra öyrənən öz sözləri ilə izah yazır; sistem bunun yalnız açar anlayış əhatəsi olduğunu, düzgünlük qiyməti olmadığını açıq deyir.” |
| 7 | 2:55–3:45 | 09-practice.png → 10-practice-misconception.png → 11-method-change.png → 12-practice-pass.png | “Praktika öz redaktorunda, öz faylında gedir. **HAZIRLANMIŞ CAVAB (DEMO):** normallaşdırmadan dot product. ‘Yoxla’ CI kimi işləyir: hər yoxlama ayrıca; ‘Sənin nəticən: 10, gözlənilən: 1.0’. Səhv adı ilə göstərilir. Eyni səhv təkrarlananda sistem metodu dəyişir — başqa yolla izah. Düzəliş edilir: 4/4. Amma bu köməkli praktikadır və müstəqil bacarıq sayılmır.” |
| 8 | 3:45–4:25 | Confirm modal → 13-exam.png → direct `#/learn` attempt + toast → 14-exam-second-tab.png → terminal `course help` → `exam_active` | “İndi görmədiyi yeni tapşırıq: RAG üçün ən yaxın k sənəd. Bu müddətdə izah və ipucu bağlıdır — həm tətbiqdə, həm agentdə. Dərsə keçməyə, geri qayıtmağa, səhifəni yeniləməyə və ikinci pəncərə açmağa çalışıram — hər dəfə eyni cəhdə qayıdır. Agent də kömək etmək istəsə, runtime ‘imtahan davam edir’ deyib imtina edir.” |
| 9 | 4:25–5:00 | Terminal: server stopped and started → 15-exam-after-restart.png → submit (double click) → 16-result.png | “Serveri tamamilə dayandırıb yenidən başladıram — cəhd itmir, eyni tapşırıqdır. **HAZIRLANMIŞ CAVAB (DEMO)** göndərilir, düyməyə iki dəfə basılır. Gizli yoxlamalar: əsas, kənar hal və görmədiyi transfer halları — 7/7. Sübut bir dəfə yazılıb, ikinci klik heç nə əlavə etməyib.” |
| 10 | 5:00–5:20 | 17-next-mission.png → 18-progress.png → terminal `ledger-verify` (ok, keyed) | “Növbəti missiya sübutdan çıxır və səbəbi yazılır. Vacib qeyd: bu bir müstəqil nəticədir, ustalıq hələ təsdiqlənməyib — iyirmi dörd saatdan sonra qısa təkrar yoxlama olacaq. Növbəti dərs hələ hazırlanmayıbsa, sistem bunu ‘hazır’ kimi göstərmir. Yenilədikdə və yenidən açdıqda hər şey yerindədir; jurnalın bütövlüyü yoxlanılıb.” |
| 11 | 5:20–5:40 | Progress card “Bu sistemin sərhədləri” | “Dürüst sərhəd: bu, öyrənənin öz cihazında iş axını nəzarətidir, nəzarət olunan imtahan deyil; tətbiqdə kömək olmaması kənar köməyin olmadığını sübut etmir. Burada uzunmüddətli yadda saxlama və öyrənmə effektivliyi göstərilmir — bunun üçün müqayisə qrupu və gecikmiş test olan pilot lazımdır.” |

## Shot list commands (reproduce)

```bash
D=.learning/demo-nca-genl; SID=<fresh session id>
./alearn --workspace . course serve --session $SID --allow-anon        # terminal 1
python3 tests/e2e/course_ui_e2e.py approve --out $D/e2e-rec            # terminal 2
# author items + lesson after approval (see course-runtime.md §12), then:
python3 tests/e2e/course_ui_e2e.py learn   --out $D/e2e-rec
# stop + restart the server in terminal 1, then:
python3 tests/e2e/course_ui_e2e.py resume  --out $D/e2e-rec
./alearn --workspace . ledger-verify --session $SID
```

A new recording needs a fresh session (the phases assume a clean start).
