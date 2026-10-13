# Course demo — screen-recording storyboard (v7.1, ≈4 min)

Two kinds of footage, always labelled on screen:

- **REAL CONTENT (DEMO LEARNER)** — the NCA-GENL course from 2026-10-12 (sources
  validated in NotebookLM notebook `81a2ab0e…`), restored from a verified backup into a
  scratch workspace and migrated to schema v2; the original session is untouched
  (hash-checked). Learner answers are **HAZIRLANMIŞ CAVAB (DEMO)**.
- **SYNTHETIC FIXTURE** — `tests/e2e/course_ui_e2e.py` on the labelled test course
  (vectors). Every claim on screen is a typed assertion in `e2e-log.json`.

No efficacy, retention or learner-study result is shown, because none exists.

Artifacts (local, gitignored): real content — scratchpad `demo-v710/out-r3/`
(`transcript.json`, `screenshots/01–05`), content `.learning/demo-nca-genl/v710/content.json`,
backup `.learning/demo-nca-genl/v710/sess_f033-v1-backup.zip`; synthetic — `e2e-v710-r*/`
(`e2e-log.json`, `screenshots/01–16`, `trace.zip`; final run `e2e-v710-r6`).

| # | Time | Footage | Screen | Narration (Azerbaijani) |
|---|------|---------|--------|-------------------------|
| 1 | 0:00–0:20 | real | NVIDIA page, five domains | “Hədəfi bu gün yenidən yoxladıq: NCA-GENL, beş bölmə — 30, 14, 22, 24, 10 faiz. RAG Core ML bölməsində açıq yazılıb. İmtahan versiyası dərc olunmayıb, biz də iddia etmirik.” |
| 2 | 0:20–0:45 | real | terminal: migration step of `transcript.json` | “Köhnə kurs yeni versiyaya keçdi: əvvəlki vəziyyət arxivdə saxlanıb, keçilmiş mərhələ və dərs qüvvədədir, görülmüş tapşırıq ‘görülmüş’ kimi tanınır, təkrar yoxlama planlaşdırılıb. Heç nə silinməyib.” |
| 3 | 0:45–1:05 | real | `01-roadmap-with-concept-node.png` | “Yeni mərhələ əlavə olunur: RAG anlayışları. Təsdiq əvvəlcə nəyin dəyişəcəyini göstərir — yalnız ‘əlavə olundu’; mövcud proqres toxunulmur.” |
| 4 | 1:05–1:35 | real | `02-lesson.png` | “İzah üç mənbə sitatına əsaslanır və hər sitat runtime-da yoxlanılıb. Mənbə dəyişsə və ya oxunmasa, bu dərs ‘hazır’ görünməz.” |
| 5 | 1:35–2:00 | real | `03-concept-practice.png` | “Kodsuz praktika. **HAZIRLANMIŞ CAVAB (DEMO):** ‘model yenidən öyrədilir’. Sistem səhvin adını deyir: RAG-i yenidən öyrətmə ilə qarışdırmaq. İpucu, sonra düzgün cavab — amma bu köməkli praktikadır.” |
| 6 | 2:00–2:30 | real | `04-scenario-exam.png` → `05-scenario-result.png` | “Görmədiyi yeni ssenari: bank çatbotu köhnə faizləri deyir. İzah və ipucu bağlıdır. Nəticə 4/4 — amma ekranda açıq yazılır: seçim sualları tanıma səviyyəli sübutdur, ustalıq deyil. Növbəti gün yeni tapşırıqla təkrar yoxlama var.” |
| 7 | 2:30–2:50 | synthetic | `06-exam-locked.png` | “İmtahanda geri, irəli, birbaşa link, ikinci pəncərə, serverin yenidən başlaması — hər dəfə eyni cəhd. ‘Niyə’ düyməsi fokuslanır və niyə bağlı olduğunu oxuyur.” |
| 7b | 2:50–3:05 | synthetic | `11-free-text-awaiting-rubric.png` → `12-free-text-result.png` | “Açıq cavab açar sözlərlə qiymətləndirilmir: rubrika qərarı gözlənilir, kilid qalır. Qərar gələndə ekranda kimin qiymətləndirdiyi və bunun runtime yoxlaması yox, rəy olduğu yazılır.” |
| 8 | 3:05–3:15 | synthetic | terminal: duplicate check → `13-grounding-invalid.png` | “Eyni tapşırığı başqa adla əlavə etsək, ‘təkrar’ sayılır və yeni sübut vermir. Mənbə oxunmaz olanda dərs bağlanır və yalnız etiketli tarixi nüsxə görünür.” |
| 9 | 3:10–3:35 | synthetic | `14-capability-impact.png` | “Mərhələnin məqsədi dəyişəndə təsdiq ekranı əvvəlcədən deyir: köhnə nəticə tarixçəyə keçəcək, yeni tapşırıqla yenidən yoxlanacaq. Asılı mərhələnin öz nəticəsi qalır.” |
| 10 | 3:35–3:55 | synthetic | `15-due-review-mission.png` → `16-progress-reviews.png` | “Bir gün sonra təkrar yoxlama missiyanın önünə keçir, yeni tapşırıqla aparılır və növbəti interval uzanır. Real vaxt keçmədiyi üçün sübut ‘gecikmiş yadda saxlama’ kimi qeyd olunmur.” |
| 11 | 3:55–4:10 | either | Progress “Bu sistemin sərhədləri” | “Bu, öz cihazında iş axını nəzarətidir, nəzarətli imtahan deyil. Effektivlik burada sübut olunmur — bunun üçün eyni vaxtlı müqayisə qrupu və gecikmiş test olan tədqiqat lazımdır.” |

## Reproduce

```bash
# synthetic fixture (also the CI job):
python3 tests/e2e/course_ui_e2e.py --out <dir> [--node-modules <existing node_modules>]
# real content: restore a verified backup into a scratch workspace, then run the driver
alearn --workspace . backup-session --session <sid> --output .learning/<name>.zip
alearn --workspace <scratch> restore-session --backup-file <scratch>/backup.zip
```
