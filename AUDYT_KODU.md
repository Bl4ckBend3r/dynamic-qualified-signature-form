# Audyt kodu aplikacji formularzy online

## 1. Podsumowanie

Projekt jest w stanie przejściowym: główna aplikacja działa już przez fabrykę `create_app()` w `app.py`, blueprinty w `routes/` i kontener serwisów w `services/container.py`, ale obok tego nadal istnieje duży `legacy_app.py`, a część nowych modułów nadal korzysta z helperów legacy. Kierunek architektury jest dobry: są osobne moduły dla formularzy, dokumentów, workflow, powiadomień, Nextcloud, repozytoriów i walidacji. Największy problem to niedokończona migracja i rozproszenie odpowiedzialności między endpointami, serwisami, szablonami oraz polami legacy w modelach.

Najmocniejsze elementy projektu to `services/container.py`, `services/process_service.py`, `services/workflow_service.py`, `services/document_service.py`, `services/notification_service.py`, `services/nextcloud_storage.py`, `repositories/submission_repository.py`, `validators/form_config_validator.py` oraz rozbudowany zestaw testów w `tests/`. Najsłabsze elementy to rozmiar i zakres `routes/admin.py`, obecność `legacy_app.py`, logika statusów powielona w Pythonie i JavaScripcie, bardzo szeroki model `FormSubmission` w `models.py` oraz mieszanie logiki widoku z logiką workflow w `templates/documents_to_sign.html`.

Ocena gotowości do obsługi wielu workflow: **6/10**. Aplikacja ma już konfigurowalne dokumenty, kroki workflow, reguły, powiadomienia i pola po akceptacji, ale wiele decyzji nadal jest zapisanych pod konkretny scenariusz deklaracja/umowa/szkolenia. Przed skalowaniem do wielu obiegów trzeba scentralizować statusy, przejścia, politykę dokumentów, maile i warstwę administracyjną.

## 2. Największe problemy

| Problem | Lokalizacja | Skutek | Rekomendacja |
|---|---|---|---|
| Niedokończona migracja z monolitu | `legacy_app.py`, `services/container.py:96`, `routes/documents.py:14`, `routes/documents.py:267` | Nowe endpointy zależą od starej aplikacji i helperów, więc odpowiedzialności są niejasne. | Przenieść helpery szkoleń i deklaracji z `legacy_app.py` do `services/document_service.py` albo osobnego `services/training_agreement_service.py`, potem usunąć import `legacy_app` z routingu. |
| Za duży routing admina | `routes/admin.py` | Jeden plik obsługuje routing, RBAC, formularze, pola, HTML/DOCX parsery, logo, maile, stopki, filtrowanie i wysyłkę. | Podzielić na `routes/admin/forms.py`, `routes/admin/submissions.py`, `routes/admin/mail.py`, `routes/admin/logos.py`, `routes/admin/users.py` oraz serwisy pomocnicze. |
| Dwa równoległe systemy statusów | `statuses.py`, `services/process_service.py`, `services/workflow_service.py:10`, `routes/admin.py:83`, `templates/documents_to_sign.html:547` | Część statusów jest enumem, część stałymi string, część lowercase, część uppercase; łatwo o niespójność UI i backendu. | Wprowadzić jeden katalog statusów i przejść, eksportowany do backendu i frontendu. |
| Zbyt szeroki model zgłoszenia | `models.py:17` | `FormSubmission` miesza dane konkretnego formularza, workflow, dokumenty, maile i pola legacy. | Stopniowo przenieść dane specyficzne dla formularza do `data_json`, a dokumenty/statusy do osobnych tabel. |
| Szablon z logiką workflow i JS | `templates/documents_to_sign.html:65`, `templates/documents_to_sign.html:362`, `templates/documents_to_sign.html:535` | Widok użytkownika powiela logikę statusów i ukrywania etapów z backendu. | Zrobić backendowy view model w `DocumentService.build_documents_view()` i osobny plik JS/CSS. |
| Rozproszona obsługa maili | `services/notification_service.py`, `routes/admin.py:1579`, `services/mail_template_service.py` | Maile systemowe i adminowe mają różne ścieżki wyboru szablonu, stopki i logowania. | Utworzyć jeden `MailDispatchService`, który wybiera szablon, renderuje, wysyła i zapisuje `EmailLog`. |
| Ścieżki plików i logo w kilku miejscach | `pdf_generator.py:13`, `services/document_service.py:413`, `services/notification_service.py:226`, `routes/documents.py:57`, `services/nextcloud_storage.py:204` | Trudno zagwarantować spójne ścieżki dla PDF, logo, szablonów i załączników. | Scentralizować w `StoragePathService` albo rozszerzyć `services/file_metadata.py`. |
| Importowany HTML jako źródło logiki | `routes/admin.py:1232`, `services/mail_template_service.py:192`, `templates/form_page.html:45` | HTML admina/maili/dokumentów może stać się niejednoznacznym źródłem danych i ryzykiem XSS. | Ustalić politykę zaufania, sanitizować konsekwentnie i ograniczyć `|safe` do pól zaufanych. |

## 3. Struktura projektu

Struktura katalogów jest częściowo uporządkowana: `routes/` zawiera endpointy, `services/` logikę biznesową, `repositories/` dostęp do danych, `validators/` walidację JSON, `templates/` widoki i `static/` zasoby. To dobry fundament. Problemem jest jednak to, że część plików nadal przejmuje kilka warstw jednocześnie.

Największe pliki wymagające podziału:

| Plik | Rozmiar | Problem | Rekomendacja |
|---|---:|---|---|
| `routes/admin.py` | ok. 1545 linii | Routing, autoryzacja, parsery uploadu, logika formularzy, maili, logo, użytkowników i filtrowania w jednym pliku. | Podzielić na blueprinty domenowe i wynieść helpery do `services/admin_*`. |
| `legacy_app.py` | ok. 1319 linii | Stary monolit nadal jest zależnością nowych routów i testów. | Traktować jako katalog migracji; przenieść używane helpery, potem usunąć plik. |
| `templates/documents_to_sign.html` | ok. 768 linii | HTML, Jinja, CSS i JS z logiką statusów w jednym pliku. | Wydzielić partiale, `static/documents_to_sign.js` i `static/documents_to_sign.css`. |
| `services/document_service.py` | ok. 768 linii | Generowanie, upload, nazwy plików, podpisy, view model i helpery PDF. | Podzielić na `DocumentGenerationService`, `SignedDocumentService`, `DocumentNamingService`. |
| `routes/documents.py` | ok. 715 linii | Endpointy dokumentów zawierają adaptery konfiguracji, powiadomienia, generowanie i upload. | Zostawić routing cienki, przenieść decyzje do serwisów. |
| `models.py` | ok. 393 linie | Jeden model `FormSubmission` ma bardzo dużo pól domenowych. | Podzielić dane formularza, workflow, pliki i maile na osobne tabele. |

Elementy struktury do przeniesienia:

| Element | Aktualnie | Docelowo |
|---|---|---|
| Parser uploadu formularzy HTML/DOCX | `routes/admin.py:1232`, `routes/admin.py:1280`, `routes/admin.py:1303` | `services/form_import_service.py` |
| Dobór szablonu i wysyłka maili admina | `routes/admin.py:1579`, `routes/admin.py:1644`, `routes/admin.py:1696` | `services/mail_dispatch_service.py` |
| Obsługa logo | `routes/admin.py:951`, `routes/admin.py:1027`, `routes/public_forms.py:127` | `services/logo_service.py` |
| Statusy frontendowe | `templates/documents_to_sign.html:547` | API/view model z backendu plus wspólny słownik statusów |
| Helpery szkoleń | `legacy_app.py`, `routes/documents.py:267` | `services/training_agreement_service.py` |

Katalogi możliwe do uproszczenia lub weryfikacji: `output/` wygląda na katalog artefaktów lokalnych z PDF, CSV i podpisami, a nie kod aplikacji. `.coverage`, `.pytest_cache/` i `tmp/logos/` są śledzone przez Git według `git ls-files`; powinny zostać usunięte z repozytorium lub przeniesione do ignorowanych artefaktów.

## 4. Czytelność kodu

Najbardziej nieczytelne fragmenty wynikają nie z pojedynczych nazw, tylko z długości funkcji i liczby przypadków specjalnych.

| Lokalizacja | Problem | Rekomendacja |
|---|---|---|
| `routes/admin.py:363` `form_edit()` | Obsługuje walidację workflow, pola formularza, slug, logo, uprawnienia i render błędów. | Rozbić na `AdminFormService.update_form_settings()` i osobne helpery renderowania. |
| `routes/admin.py:472` `form_fields()` | Dodawanie, przywracanie, ukrywanie i zapis wielu pól w jednym endpointcie. | Oddzielić akcje `add_field`, `update_fields`, `archive_field`. |
| `routes/admin.py:713` `mail_template_edit()` | Tworzenie/edycja, upload HTML, fallbacki pól, aktywność i default status w jednej funkcji. | Przenieść do `MailTemplateAdminService`. |
| `routes/admin.py:854` `mail_template_import_zip()` | Import ZIP, zapis assetów i logika defaultów w routingu. | Router powinien tylko przyjąć plik i wywołać serwis importu. |
| `routes/documents.py:671` `documents_to_sign()` | GET, POST, wyszukiwanie zgłoszenia, render błędów i budowa resultu. | Wydzielić `DocumentsToSignControllerService.handle_request()`. |
| `services/document_service.py:71` `generate_document()` | Generowanie dokumentu, cache istniejącego PDF, storage, metadane, update i audit. | Rozdzielić generowanie bytes, zapis storage i update statusu. |
| `services/document_service.py:164` `generate_documents_for_collection()` | Generowanie wielu umów powtarza większość logiki `generate_document()`. | Wspólny generator dokumentu dla pojedynczego i kolekcji. |
| `templates/form_page.html:42` | Duży blok Jinja renderujący wszystkie typy pól. | Wydzielić partial `templates/partials/form_field.html`. |
| `templates/documents_to_sign.html:65` | Warunki Jinja sterują etapami workflow. | Przenieść decyzje do backendowego `result.stages`. |

Nazwy są w większości zrozumiałe, ale niespójne językowo i domenowo. Przykłady: `akceptacja`, `acceptance_required`, `officer_decision`, `accepted_waiting_for_additional_fields` oraz `REVIEW_ACCEPTED` oznaczają podobne etapy różnymi konwencjami. Funkcje `build_declaration_form_definition()` w `routes/documents.py:136` i logika dokumentów w `DocumentService` powinny używać wspólnego nazewnictwa dokumentów.

Komentarze są raczej oszczędne. Brakuje krótkich komentarzy przy mostach legacy: `services/container.py:96`, `routes/documents.py:267` i `services/submission_service.py:240`. Zakomentowanego starego kodu nie widać jako dominującego problemu.

## 5. Prostota implementacji

Implementacja jest miejscami bardziej złożona niż cel, bo obsługuje jednocześnie nowy model i kompatybilność z wcześniejszym formularzem.

| Fragment | Co komplikuje | Propozycja uproszczenia |
|---|---|---|
| `services/process_service.py:164` | Status procesu jest wyliczany z wielu pól `Tak/Nie`, plików i decyzji. | Zrobić jeden obiekt `ProcessState` zapisywany jako status + `workflow_step`, a pola dokumentów traktować jako dane pomocnicze. |
| `services/form_config_service.py:150` | Domyślny workflow jest generowany sztywno pod deklarację, umowę i szkolenia. | Przenieść domyślne workflow do wersjonowanych presetów JSON. |
| `services/document_service.py:399` | Filename zależy od typu dokumentu i osobnych fallbacków. | Jeden `DocumentNamingService.build(document, context)`. |
| `services/nextcloud_storage.py:219` | Lookup PDF zgaduje typ dokumentu i podpis po nazwie pliku. | Zapisywać i czytać po metadanych `SubmissionFile.storage_path`, a zgadywanie zostawić tylko jako migracyjny fallback. |
| `routes/admin.py:1280` | Parsowanie formularza z HTML regexem. | Użyć parsera HTML albo ograniczyć import HTML do bezpiecznego subsetu. |
| `routes/admin.py:1579` | Wysyłka maili admina działa poza `NotificationService.notify_event()`. | Jeden centralny dispatch maili dla ręcznych i automatycznych wiadomości. |

Zbędne warstwy abstrakcji nie są głównym problemem. Większy koszt dają adaptery legacy i dublowanie tych samych decyzji w kilku miejscach.

## 6. Workflow i statusy

Projekt ma trzy poziomy statusów:

| Poziom | Lokalizacja | Ocena |
|---|---|---|
| Statusy procesu domenowego | `services/process_service.py:8` | Czytelne jako enum, ale zawierają stare i nowe nazwy oraz mieszają uppercase/lowercase. |
| Statusy docelowe/legacy map | `statuses.py:4`, `statuses.py:17` | Przydatne migracyjnie, ale to drugi katalog statusów. |
| Statusy UI/admin/frontend | `routes/admin.py:83`, `routes/admin.py:1670`, `templates/documents_to_sign.html:547` | Zapisane jako stringi i powielone w wielu miejscach. |

Miejsca z rozproszoną logiką statusów:

| Lokalizacja | Problem |
|---|---|
| `services/process_service.py:164` | Wylicza status z danych zgłoszenia, decyzji, podpisów i wymaganych dokumentów. |
| `services/workflow_service.py:193` | Mapuje `step_id` na status przez heurystyki typu `if "signature" in step_id`. |
| `routes/admin.py:612` | Decyzja urzędnika ustawia status bez przejścia przez `WorkflowService.transition_to()`. |
| `routes/admin.py:580` | Ręczna edycja szczegółów zgłoszenia zapisuje dowolny `process_status`. |
| `routes/api.py:54` | Endpoint statusu wywołuje wysyłkę maila decyzji przy samym odczycie statusu. |
| `templates/documents_to_sign.html:547` | Frontend ma własne zbiory statusów zakończenia/odrzucenia. |

Potencjalne konflikty: `OFFICER_DECISIONS` w `routes/admin.py:79` używa `accepted/rejected/correction`, `OfficerDecision` w `services/process_service.py:31` używa `TAK/NIE`, a `NotificationService._decision_key()` w `services/notification_service.py:344` mapuje oba style. To działa dzięki fallbackom, ale zwiększa ryzyko błędnej obsługi nowego workflow.

Rekomendacja centralizacji: utworzyć `services/status_catalog.py` z enumami, labelami, dozwolonymi przejściami i eksportem JSON dla frontendu. `WorkflowService` powinien być jedynym miejscem zmiany `workflow_step` i `process_status`; endpointy admina powinny wywoływać komendy serwisu, nie ustawiać pola bezpośrednio.

## 7. Formularze i konfiguracja JSON

Formularze są ładowane i normalizowane w `FormConfigService.normalize_form_config()` oraz walidowane przez `FormConfigValidator`. To dobry kierunek. Obsługiwane są typy pól w `form_loader.py:9`, etapy pól w `form_loader.py:21` i workflow w `services/form_config_service.py:253`.

Ograniczenia aktualnej konfiguracji:

| Lokalizacja | Ograniczenie | Rekomendacja |
|---|---|---|
| `validators/form_config_validator.py:10` | Dozwolone placeholdery nazw plików są krótką whitelistą. | Pozwolić na placeholdery z pól formularza przez jawnie zdefiniowany `filename_context`. |
| `services/form_config_service.py:150` | Domyślny workflow zna tylko `declaration`, `agreement`, `training_agreement`. | Przenieść workflow do presetów i pozwolić adminowi wybrać preset. |
| `routes/admin.py:1257` | Edycja workflow zapisuje JSON i dwa pola HTML deklaracji/umowy. | Zrobić edytor kroków i dokumentów oparty o strukturę `workflow.steps`. |
| `routes/admin.py:472` | Edycja pól działa na tabeli `FormField`, ale dokumenty mogą mieć własne pola w JSON. | Ujednolicić źródło pól: albo wszystko w `definition_json`, albo wszystko w tabelach z synchronizacją. |
| `form_loader.py:292` | Walidacja `visible_if` obsługuje kilka operatorów, ale frontend w `templates/form_page.html:191` tylko `equals/not_equals`. | Ujednolicić walidację frontend/backend. |

Walidacja JSON istnieje, ale upload admina używa `FormConfigValidator(skip_template_check=True)` w `routes/admin.py:1248`, więc nie sprawdza istnienia szablonów dokumentów i maili. Dla środowiska produkcyjnego warto mieć tryb walidacji pełnej przed aktywacją formularza.

## 8. Dokumenty PDF

Generowanie PDF jest częściowo wydzielone: `pdf_generator.py` odpowiada za WeasyPrint, style i stopkę, a `DocumentService` za dokumenty deklaracji/umów. Nadal są jednak duplikaty i stare ścieżki.

| Problem | Lokalizacja | Rekomendacja |
|---|---|---|
| Generowanie PDF formularza i dokumentów idzie dwiema ścieżkami | `services/submission_service.py:90`, `services/document_service.py:855` | Jeden interfejs `PdfRenderer.render(template, context)`. |
| `DocumentService` ma zbyt szeroki zakres | `services/document_service.py:71`, `services/document_service.py:278`, `services/document_service.py:800` | Oddzielić generowanie, upload podpisu, nazewnictwo i kontekst PDF. |
| Stare helpery PDF nadal są w `legacy_app.py` | `legacy_app.py:420`, `legacy_app.py:669` | Przenieść używane funkcje do serwisów i usunąć duplikaty. |
| Stała ścieżka logo Nextcloud | `pdf_generator.py:13` | Użyć konfiguracji albo tabeli `Logo`, nie stałej `Strona WWW/Formularze/Logo`. |
| Zgadywanie ścieżek po nazwie pliku | `services/nextcloud_storage.py:189`, `services/nextcloud_storage.py:200`, `services/nextcloud_storage.py:219` | Preferować `SubmissionFile.storage_path`; lookup po nazwie tylko jako fallback. |
| Inline usuwanie logo z HTML regexem | `services/document_service.py:833` | Zastąpić parserem HTML albo jasno określonym slotem logo w szablonie. |

Podpisane i niepodpisane pliki są rozróżniane przez `SubmissionFile.signed`, `services/file_metadata.py` i katalogi Nextcloud `podpisane/niepodpisane`. To należy zostawić, ale oprzeć pobieranie plików o metadane zamiast heurystyk nazw.

## 9. Maile

Aplikacja ma trzy mechanizmy maili:

| Mechanizm | Lokalizacja | Ocena |
|---|---|---|
| Eventowe powiadomienia formularza | `services/notification_service.py:18` | Dobre jako baza, konfigurowalne przez JSON. |
| Mail decyzji | `services/notification_service.py:107` oraz stare `legacy_app.py:577` | Dubluje część logiki i szablonów. |
| Maile ręczne/bulk z admina | `routes/admin.py:1579`, `routes/admin.py:1610`, `routes/admin.py:1696` | Za dużo logiki w routingu, osobny wybór szablonu i logowanie. |

Problemy ze stopką i logo: stopki admina są w `MailFooter` i renderowane przez `build_footer_html()` w `routes/admin.py:1210`, ale eventowe `NotificationService` nie korzysta z tego samego mechanizmu. Logo maila jest powiązane z `MailFooter.logo_id` w `models.py:429`, natomiast PDF i formularz publiczny mają oddzielną obsługę logo. Potrzebny jest wspólny model „assetów formularza”.

Nie widać oczywistych nieużywanych szablonów maili `templates/emails/decision_accepted.html` i `templates/emails/decision_rejected.html`, bo są używane przez `NotificationService._render_decision_template()`. Do sprawdzenia są natomiast fallbacki w `legacy_app.py`, gdy po migracji nie będą już potrzebne.

## 10. Nextcloud i pliki

Integracja z Nextcloud jest wydzielona do `services/nextcloud_storage.py`, co jest dobrą decyzją. Moduł obsługuje DAV, katalogi bazowe, CSV, PDF i odczyt plików. Problemem jest to, że przechowuje jednocześnie niskopoziomowy transport, strukturę katalogów oraz logikę dokumentów.

| Lokalizacja | Ryzyko | Rekomendacja |
|---|---|---|
| `services/nextcloud_storage.py:162` | Struktura katalogów PDF jest na sztywno dla deklaracji i umów. | Konfigurować typy dokumentów z JSON workflow. |
| `services/nextcloud_storage.py:219` | Fallbacki lookupów mogą zwrócić plik z nieoczekiwanej ścieżki. | Użyć `SubmissionFile.storage_path` jako źródła prawdy. |
| `routes/documents.py:57` i `services/notification_service.py:226` | Normalizacja ścieżek Nextcloud jest powielona. | Wydzielić `normalize_storage_path()` do jednego modułu. |
| `routes/admin.py:970` | Logo są zapisywane lokalnie w `TEMP_DIR`, nie w Nextcloud/storage. | Ujednolicić storage logo z resztą plików albo jasno oznaczyć jako lokalne assety admina. |
| `services/nextcloud_storage.py:127` | Błędy zawierają `response.text`; może ujawnić szczegóły techniczne w logach. | Logować identyfikator błędu, status i skrócony komunikat. |

## 11. Panel admina i urzędnika

Nie ma osobnego panelu urzędnika; panel admina pełni też rolę obsługi urzędniczej przez użytkowników i uprawnienia formularzy. Role są w `routes/admin.py:57`, a dostęp do formularzy przez `FormPermission` w `models.py:302`.

Przycisk usuwania formularza usuwa dane z bazy tylko gdy nie ma powiązanych zgłoszeń: `routes/admin.py:281` blokuje usunięcie przy `FormSubmission` dla sluga. To chroni przed utratą danych. Dla wielu workflow lepszym domyślnym zachowaniem jest dezaktywacja formularza, a twarde usuwanie tylko jako operacja techniczna.

Miejsca zależne od statusów zapisanych na sztywno:

| Lokalizacja | Problem |
|---|---|
| `routes/admin.py:612` | Decyzja `accepted/rejected` ustawia konkretne statusy. |
| `routes/admin.py:1450` | Filtrowanie po statusie używa surowych wartości z requestu. |
| `routes/admin.py:1644` | Wybór szablonu maila porównuje stringi statusów/decyzji. |
| `templates/admin/submissions/list.html:85` | Formularz decyzji urzędnika jest powiązany z aktualną logiką decyzji. |

Elementy UI do uproszczenia: `templates/admin/forms/edit.html` miesza ustawienia formularza, workflow, szablony HTML deklaracji/umowy i uprawnienia. `templates/admin/forms/fields.html` powinien być osobnym ekranem projektowania pól, a workflow osobnym ekranem.

## 12. Frontend

Frontend jest klasyczny: Jinja + CSS + małe skrypty inline. Jest używalny, ale ma duplikację i logikę biznesową w widokach.

| Lokalizacja | Problem | Rekomendacja |
|---|---|---|
| `static/style.css` | Duży wspólny plik dla publicznych widoków i części admina. | Podzielić na bazę, komponenty formularza i widok dokumentów. |
| `static/admin.css` | Duży plik panelu, prawdopodobnie rośnie razem z funkcjami admina. | Podzielić na layout admina i komponenty tabel/formularzy. |
| `templates/documents_to_sign.html:362` | CSS inline w szablonie. | Przenieść do pliku statycznego. |
| `templates/documents_to_sign.html:535` | JS inline z logiką statusów. | Przenieść do pliku JS i zasilać danymi z API. |
| `templates/form_page.html:167` | JS visible-if obsługuje mniej operatorów niż backend. | Wspólny kontrakt operatorów albo generowanie stanu z backendu. |
| `templates/form_page.html:45`, `templates/form_page.html:49`, `templates/form_page.html:65`, `templates/form_page.html:140` | `|safe` dla etykiet i opisów z konfiguracji. | Sanitizować konfigurację lub oznaczyć tylko zaufane pola jako HTML. |

Nie ma osobnych plików JS, więc trudno wskazać nieużywany JS jako plik. Martwy lub trudny do utrzymania jest raczej inline JS w `documents_to_sign.html` i `form_page.html`.

## 13. Konfiguracja

Konfiguracja jest skupiona w `config.py`, co jest dobre. Wymagane zmienne Nextcloud są walidowane dopiero przy tworzeniu storage w `services/nextcloud_storage.py:26`. `DATABASE_URL` jest opcjonalny, a aplikacja przełącza się na CSV/Nextcloud w `services/container.py:47`.

Wartości wpisane na sztywno:

| Lokalizacja | Wartość | Rekomendacja |
|---|---|---|
| `config.py:19` | `APP_NAME = "Formularze Lubuskie"` | Przenieść do `.env` lub ustawień deploymentu. |
| `config.py:22` | `SECRET_KEY = "change-me-in-production"` | W produkcji wymagać wartości i kończyć start czytelnym błędem. |
| `pdf_generator.py:13` | `NEXTCLOUD_LOGO_DIR = "Strona WWW/Formularze/Logo"` | Przenieść do `Config`. |
| `routes/documents.py:30` | Domyślny template maila `Template/Mail/agreement_signed.html` | Przenieść do konfiguracji workflow/presetu. |
| `services/nextcloud_storage.py:162` | Katalogi `deklaracja/umowy/podpisane/niepodpisane` | Ustawić przez konfigurację dokumentów albo katalog statusów dokumentu. |

Brakuje centralnej walidacji wymaganych zmiennych środowiskowych. Rekomendacja: dodać `Config.validate()` wywoływane w `create_app()`, które sprawdza `SECRET_KEY`, Nextcloud, SMTP gdy wysyłka aktywna, oraz `DATABASE_URL` gdy `/admin` ma być dostępny.

## 14. Baza danych

Modele są czytelnie zapisane SQLAlchemy 2.0, relacje formularzy, pól, uprawnień, maili i plików są logiczne. Problemem jest model `FormSubmission`, który zawiera pola specyficzne dla jednego historycznego formularza i aktualnego workflow.

Pola wymagające sprawdzenia pod kątem migracji lub usunięcia:

| Pole | Lokalizacja | Powód |
|---|---|---|
| `acceptance_required`, `acceptance_email_sent`, `decision_email_sent`, `akceptacja` | `models.py:104` | Pola legacy równoległe do `officer_decision*`. |
| Pola deklaracji `deklaracja_*` | `models.py:81` | Wyglądają na specyficzne dla jednego formularza; dla wielu formularzy powinny być w `data_json`. |
| Pola oświadczeń `osw_*` | `models.py:71` | Specyficzne dla jednego wzoru formularza. |
| `selected_trainings`, `training_agreements` jako `Text` | `models.py:94` | Przechowują struktury list jako tekst JSON; lepiej osobna tabela albo JSONB. |
| `agreement_*` i `declaration_*` | `models.py:115` | Część powinna przejść do tabeli `submission_documents`. |
| `signature_status`, `signature_request_id` | `models.py:98` | Powiązane z dawnym podpisem formularza, podczas gdy dokumenty mają własne podpisy. |

Relacje do zostawienia: `Form -> FormField`, `Form -> FormPermission`, `Form -> MailTemplate`, `Form -> MailFooter`, `FormSubmission -> SubmissionFile`. Przy usuwaniu formularza `routes/admin.py:281` poprawnie chroni przed usunięciem, jeśli istnieją zgłoszenia; warto jednak dodać jawny test regresji dla kaskad `FormField`, `MailTemplate`, `MailFooter`, `FormPermission`.

## 15. Testy

Projekt ma sensowny zestaw testów: workflow, status mapping, reguły, dokumenty, PDF, maile, Nextcloud storage, routingi, panel admina, tokeny dostępu i walidację formularzy. To jest mocna strona repozytorium.

Istniejące testy:

| Obszar | Pliki |
|---|---|
| Workflow/statusy | `tests/test_workflow_service.py`, `tests/test_status_mapping.py`, `tests/test_rules_service.py` |
| Formularze/walidacja | `tests/test_form_loader.py`, `tests/test_form_config_validator.py`, `tests/test_form_submission.py` |
| PDF/dokumenty | `tests/test_pdf_generation.py`, `tests/test_document_service.py`, `tests/test_training_agreements.py` |
| Maile | `tests/test_notification_service.py`, częściowo `tests/test_admin_panel.py` |
| Nextcloud/storage | `tests/test_storage.py`, `tests/test_submission_repository.py` |
| Uprawnienia/admin | `tests/test_admin_panel.py`, `tests/test_routes.py` |
| Bezpieczeństwo pobierania | `tests/test_access_tokens.py`, `tests/test_routes.py` |

Brakujące testy regresji:

| Priorytet | Test |
|---|---|
| P0 | Brak dostępu do `/downloads/pdfs/...` i `/downloads/signed/...` dla pliku innego zgłoszenia przy znanym tokenie obcego zgłoszenia. |
| P0 | Upload podpisanego PDF z poprawnym rozszerzeniem, ale błędnym MIME i złośliwą nazwą pliku. |
| P1 | Przejście decyzji admina przez `WorkflowService`, nie bezpośrednią zmianę `process_status`. |
| P1 | Ten sam workflow renderowany spójnie w adminie, API i `documents_to_sign.html`. |
| P1 | Import HTML/DOCX formularza z polami zagnieżdżonymi i niedozwolonym HTML. |
| P2 | Usunięcie/dezaktywacja formularza z mail template, footer, permission i field cascade. |
| P2 | Wybór szablonu maila dla `trigger_status`, `trigger_decision` i `template_type`. |

Nie uruchamiałem testów w ramach audytu, bo zadanie dotyczyło analizy i przygotowania raportu, a środowisko ma ograniczenia zapisu.

## 16. Bezpieczeństwo

Najważniejsze ryzyka:

| Ryzyko | Lokalizacja | Rekomendacja |
|---|---|---|
| HTML z konfiguracji renderowany jako bezpieczny | `templates/form_page.html:45`, `templates/form_page.html:49`, `templates/form_page.html:65`, `templates/form_page.html:140`, `templates/form_page.html:151` | Sanitizować pola konfiguracji albo ograniczyć `|safe` do whitelisty admina super_admin. |
| Renderowanie template string z zewnętrznego storage | `services/notification_service.py:187`, `services/notification_service.py:222`, `pdf_generator.py:161` | Użyć sandbox Jinja dla treści admina/Nextcloud i ograniczyć kontekst. |
| Upload logo ufa MIME z klienta | `routes/admin.py:962` | Zweryfikować magic bytes i limit rozmiaru. SVG traktować ostrożnie albo sanitize. |
| Upload PDF sprawdza głównie rozszerzenie | `routes/documents.py:443`, `services/document_service.py:285` | Sprawdzać nagłówek `%PDF`, rozmiar i odrzucać nietypowe nazwy. |
| Publiczne API statusu wysyła mail przy odczycie | `routes/api.py:54` | Odczyt statusu nie powinien mieć efektów ubocznych; wysyłkę przenieść do zmiany decyzji. |
| Błędy Nextcloud mogą ujawnić response body | `services/nextcloud_storage.py:127` | Skracać komunikaty dla użytkownika i logować szczegóły tylko technicznie. |
| Sekret domyślny | `config.py:22` | W produkcji wymagać `SECRET_KEY`. |

Kontrola dostępu do dokumentów istnieje przez `AccessTokenService` i `DocumentService.verify_download_token()`, ale wymaga dalszych testów między zgłoszeniami i dla plików znalezionych po fallbackach nazw.

## 17. Do usunięcia

| Typ | Nazwa | Lokalizacja | Powód | Pewność | Ryzyko usunięcia |
|---|---|---|---|---|---|
| Plik | `.coverage` | `.coverage` | Artefakt pokrycia testów jest śledzony w Git. | Wysoki | Niskie, jeśli nie jest używany przez CI jako fixture. |
| Katalog/pliki | cache pytest | `.pytest_cache/` | Artefakt lokalny, nie powinien być w repo. | Wysoki | Niskie. |
| Katalog/pliki | lokalne logo tymczasowe | `tmp/logos/*` | Pliki runtime są śledzone w Git. | Wysoki | Średnie, jeśli aktualne dane admina wskazują na te ścieżki. |
| Katalog/pliki | lokalne PDF/CSV/podpisy | `output/` | Artefakty wygenerowane lokalnie, nie kod. | Średni | Średnie, bo mogą służyć jako przykłady/manualne fixture. |
| Plik | `csv_exporter.py` | `csv_exporter.py` | Brak referencji poza definicją; zapis CSV jest w `NextcloudStorage` i repozytorium. | Średni | Niskie po potwierdzeniu braku użycia w skryptach zewnętrznych. |
| Plik/skrypt | `check_decision_emails.py` | `check_decision_emails.py` | Importuje stare symbole z `app`, których obecna fabryka nie eksportuje w tej formie. | Średni | Średnie, jeśli jest używany ręcznie jako narzędzie operacyjne. |
| Plik | `signature_service.py` | `signature_service.py` | Brak referencji w aplikacji; aktualny upload używa `signature_verifier.py`. | Średni | Średnie, jeśli planowana jest integracja REST podpisu kwalifikowanego. |
| Plik | `legacy_app.py` | `legacy_app.py` | Stary monolit, ale nadal używany przez `routes/documents.py` i testy. | Niski teraz, wysoki po migracji | Wysokie bez wcześniejszego przeniesienia helperów. |
| Funkcje | Helpery PDF/deklaracji legacy | `legacy_app.py:420`, `legacy_app.py:669` | Dublują `DocumentService`. | Niski teraz | Wysokie bez refaktoryzacji testów. |
| Funkcje | `build_definition_from_html()` regex | `routes/admin.py:1280` | Parser HTML regexem jest kruchy. | Niski | Średnie; lepiej zastąpić niż usuwać. |
| Endpointy | Legacy endpointy w `legacy_app.py` | `legacy_app.py:792` i dalej | Nie są rejestrowane przez `create_app()`, ale plik nadal jest importowany. | Średni | Średnie, jeśli ktoś uruchamia `legacy_app.py` bezpośrednio. |
| Szablon | `templates/result.html` | `templates/result.html` | Używany przez `routes/documents.py`/legacy dla wyniku, więc nie usuwać teraz; sprawdzić po migracji UX. | Niski | Średnie. |
| Style/JS | Inline CSS/JS dokumentów | `templates/documents_to_sign.html:362`, `templates/documents_to_sign.html:535` | Nie usuwać, tylko przenieść do statycznych plików. | Średni | Niskie po przeniesieniu testowanym. |
| Pola DB | Pola legacy akceptacji | `models.py:104` | Duplikują `officer_decision*`. | Średni | Wysokie bez migracji danych. |
| Pola DB | Pola specyficzne jednego formularza | `models.py:35` do `models.py:93` | Utrudniają wiele różnych formularzy. | Średni | Wysokie bez migracji do `data_json`. |
| Zależność | `playwright` | `requirements.txt` | Nie jest używany przez aplikację backendową; może być tylko narzędziem testowym. | Niski | Średnie, jeśli używany w testach E2E poza repo. |

## 18. Do zostawienia

| Typ | Nazwa | Lokalizacja | Powód pozostawienia | Rekomendacja uproszczenia |
|---|---|---|---|---|
| Moduł | Fabryka aplikacji | `app.py:27` | Dobry punkt startu, rejestruje blueprinty i kontener. | Dodać walidację konfiguracji. |
| Moduł | Kontener serwisów | `services/container.py:35` | Centralizuje zależności i wybór repozytorium. | Usunąć aliasy legacy po migracji. |
| Moduł | Nextcloud storage | `services/nextcloud_storage.py` | Kluczowa integracja z Nextcloud. | Oddzielić transport DAV od struktury dokumentów. |
| Moduł | Form config service | `services/form_config_service.py` | Normalizuje JSON i buduje workflow. | Wyprowadzić presety workflow do danych. |
| Moduł | Workflow service | `services/workflow_service.py` | Naturalne miejsce kontroli przejść. | Uczynić jedyną ścieżką zmiany statusu. |
| Moduł | Process service | `services/process_service.py` | Czytelny opis stanu biznesowego. | Ujednolicić z `statuses.py`. |
| Moduł | Document service | `services/document_service.py` | Kluczowy mechanizm dokumentów. | Podzielić na mniejsze serwisy. |
| Moduł | Notification service | `services/notification_service.py` | Baza dla powiadomień workflow. | Włączyć maile admina i stopki. |
| Model | `Form` | `models.py:235` | Kluczowa encja formularza publicznego/admina. | Zostawić `definition_json`, dodać wersjonowanie. |
| Model | `FormField` | `models.py:283` | Potrzebny do edycji pól w adminie. | Ustalić relację z `definition_json` jako źródłem prawdy. |
| Model | `SubmissionFile` | `models.py:152` | Bardzo potrzebny do kontroli dostępu i storage. | Rozszerzyć i używać jako źródła prawdy pobrań. |
| Model | `MailTemplate`, `MailFooter`, `EmailLog` | `models.py:321`, `models.py:414`, `models.py:443` | Kluczowe dla konfigurowalnych maili i audytu. | Znormalizować pola legacy `html_body/content_html`. |
| Endpoint | Publiczny formularz | `routes/public_forms.py:30`, `routes/public_forms.py:81` | Główna funkcja aplikacji. | Cienki routing, więcej w `SubmissionService`. |
| Endpoint | Dokumenty do podpisu | `routes/documents.py:671` | Kluczowy workflow użytkownika. | Uprościć przez view model. |
| Endpoint | Panel admina | `routes/admin.py` | Niezbędny operacyjnie. | Podzielić plik. |
| Mechanizm | Walidator JSON | `validators/form_config_validator.py` | Chroni konfigurację. | Rozszerzyć walidację template i statusów. |
| Mechanizm | Testy | `tests/` | Dobra baza regresji. | Dodać testy bezpieczeństwa i workflow multi-scenario. |

## 19. Priorytety działań

| Priorytet | Działanie | Lokalizacja | Efekt |
|---|---|---|---|
| P0 | Usunąć efekt uboczny wysyłki maila z publicznego odczytu statusu. | `routes/api.py:54` | Odczyt API będzie bezpieczny i przewidywalny. |
| P0 | Wzmocnić kontrolę pobierania dokumentów przez metadane plików. | `routes/documents.py:757`, `routes/documents.py:790`, `services/nextcloud_storage.py:219` | Mniejsze ryzyko dostępu do cudzego pliku przez fallback nazwy. |
| P0 | Ustalić politykę sanitizacji HTML i ograniczyć `|safe`. | `templates/form_page.html`, `templates/documents_to_sign.html`, `services/notification_service.py` | Mniejsze ryzyko XSS/template injection. |
| P0 | Wymagać produkcyjnego `SECRET_KEY`. | `config.py:22` | Bezpieczniejsze sesje admina. |
| P1 | Dokończyć migrację z `legacy_app.py`. | `legacy_app.py`, `routes/documents.py:14`, `services/container.py:96` | Jasne odpowiedzialności i mniejsza duplikacja. |
| P1 | Scentralizować statusy i przejścia. | `services/process_service.py`, `statuses.py`, `services/workflow_service.py`, `templates/documents_to_sign.html` | Workflow gotowy na wiele scenariuszy. |
| P1 | Podzielić `routes/admin.py`. | `routes/admin.py` | Łatwiejszy rozwój panelu admina/urzędnika. |
| P1 | Zintegrować maile admina z `NotificationService` lub nowym dispatch service. | `routes/admin.py:1696`, `services/notification_service.py` | Jedna ścieżka wysyłki i logowania. |
| P1 | Rozdzielić dokumenty od formularza głównego. | `models.py`, `services/document_service.py` | Lepsza obsługa wielu typów dokumentów. |
| P2 | Przenieść inline CSS/JS do `static/`. | `templates/documents_to_sign.html`, `templates/form_page.html` | Czystsze widoki, łatwiejsze testy UI. |
| P2 | Przenieść parsery HTML/DOCX formularzy do serwisu. | `routes/admin.py:1232` | Cieńszy routing admina. |
| P2 | Ujednolicić ścieżki plików i logo. | `pdf_generator.py`, `services/file_metadata.py`, `services/nextcloud_storage.py` | Mniej fallbacków i błędnych ścieżek. |
| P3 | Usunąć artefakty z repo. | `.coverage`, `.pytest_cache/`, `tmp/logos/`, `output/` | Czystsze repozytorium. |
| P3 | Ujednolicić język komunikatów i kodowania tekstu. | wiele plików i terminal output | Lepsza czytelność. |

## 20. Rekomendowany plan refaktoryzacji

1. Stabilizacja bezpieczeństwa i efektów ubocznych: przenieść wysyłkę maila decyzji z `routes/api.py:54` do momentu zapisu decyzji w `routes/admin.py:598`, wymusić `SECRET_KEY`, dodać walidację uploadów i testy dostępu do dokumentów.

2. Centralizacja statusów: połączyć `ProcessStatus` i `statuses.py` w jeden katalog statusów, dodać labelki i dozwolone przejścia, a `templates/documents_to_sign.html` zasilić statusem z API/view modelu zamiast lokalnych zbiorów JS.

3. Dokończenie migracji legacy: przenieść helpery używane przez `routes/documents.py` z `legacy_app.py` do nowych serwisów, przepiąć testy `tests/test_training_agreements.py`, usunąć `install_legacy_helpers()` i dopiero wtedy skasować `legacy_app.py`.

4. Podział admina: rozbić `routes/admin.py` na mniejsze moduły, zaczynając od maili i formularzy, bo tam jest najwięcej logiki biznesowej. Przy okazji przenieść parsery uploadów do `FormImportService`.

5. Uporządkowanie dokumentów: wydzielić generowanie PDF, nazewnictwo, upload podpisów i metadane plików. `SubmissionFile.storage_path` powinien stać się źródłem prawdy dla pobierania.

6. Uporządkowanie modeli: zaplanować migrację pól specyficznych jednego formularza z `FormSubmission` do `data_json` albo tabel pomocniczych. Osobno wymodelować `SubmissionDocument` dla deklaracji, umów i przyszłych dokumentów.

7. Frontend: wydzielić partiale pól formularza, pliki JS/CSS dla `documents_to_sign`, usunąć duplikację logiki visible-if i workflow między frontendem a backendem.

8. Porządki repozytorium: usunąć artefakty `.coverage`, `.pytest_cache/`, `tmp/logos/`, `output/` po potwierdzeniu, że nie są fixture w testach ani dokumentacji; zaktualizować `.gitignore`.

Końcowa ocena gotowości do wielu workflow: **6/10**. Fundament jest dobry i testy już chronią część kluczowych ścieżek, ale dopóki workflow, statusy, dokumenty i maile mają równoległe ścieżki legacy oraz zakodowane przypadki deklaracji/umowy/szkoleń, każdy nowy scenariusz będzie podnosił koszt utrzymania i ryzyko regresji.
