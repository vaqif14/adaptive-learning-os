# Bug qeydləri

## BUG-001 — Azərbaycan dilində ilkin bilik yoxlamasının mətni aydın deyil

- Tarix: 2026-10-06
- Status: Açıq
- Komponent: adaptive-learn — ilkin bilik yoxlaması (Anchor Probe), Azərbaycan dili
- İstifadəçi rəyi: “cümlə quruluşunu düzgün qurmayıb, cümlələr düzgün anlaşılmır”.

### Təkrarlama addımları

1. Agentə bu sorğunu verin: “Adaptive-learn ilə LLM öyrənməyə başlayaq. Əvvəl biliklərimi qısa tapşırıqla yoxla.”
2. Agentin şirkət sənədləri üzrə LLM köməkçisi haqqında verdiyi ilkin tapşırığın mətnini oxuyun.

### Müşahidə edilən nəticə

İstifadəçi tapşırıqdakı cümlələrin quruluşunun anlaşılmadığını bildirib. Problemli cavabdan nümunələr:

> Şirkət daxili sənədlər üzrə suallara cavab verən LLM köməkçisi qurursunuz.
>
> Yeni sənədlərdən istifadə etməsi üçün hansı həlli seçərdiniz və niyə?
>
> Cavabın doğruluğunu yoxlamaq üçün hansı iki yoxlamanı tətbiq edərdiniz?

İkinci sualda hərəkəti edən tərəf açıq göstərilmir. Üçüncü sualda “yoxlamaq / yoxlama” təkrarı ifadəni ağırlaşdırır. Bunlar ilkin redaktə müşahidələridir; istifadəçi konkret bir cümləni ayrıca seçməyib. Səbəbin prompt, lokalizasiya və ya model çıxışı ilə bağlı olduğu hələ müəyyən edilməyib.

### Gözlənilən nəticə

Tapşırıq təbii Azərbaycan dilində, qısa və aydın cümlələrlə verilməlidir. Hər sualda kimdən və nədən danışıldığı, istifadəçidən hansı cavabın gözlənildiyi aydın olmalıdır. Dilin anlaşılmaması istifadəçinin mövzu üzrə bilik çatışmazlığı kimi qiymətləndirilməməlidir.

### Təklif olunan mətn

Şirkətin sənədlərinə əsasən sualları cavablandıran bir süni intellekt köməkçisi hazırladığınızı düşünün. Sənədlər hər həftə yenilənir. Köməkçi isə bəzən səhv cavab verir, amma cavabı inandırıcı səslənir.

1. Sizcə, LLM bir suala cavabı necə hazırlayır?
2. Köməkçinin yenilənmiş sənədlərə əsasən cavab verməsi üçün nə edərdiniz?
3. Köməkçinin verdiyi cavabın doğru olduğunu necə yoxlayardınız? İki üsul yazın.

Qısa cavab verə bilərsiniz. Bilmədiyiniz suala “bilmirəm” yazmaq olar.

### Qəbul meyarları

- İlkin tapşırığın Azərbaycan dilindəki mətni dil baxımından nəzərdən keçirilib.
- Əvəzliklər və cümlələrin subyekti aydındır; lazımsız təkrarlar aradan qaldırılıb.
- İstifadəçi suallardan nə tələb olunduğunu əlavə izah olmadan anlaya bilir.
- Tapşırığın anlaşılmaması ilə mövzu üzrə bilik çatışmazlığı ayrı qiymətləndirilir.
