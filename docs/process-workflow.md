# Aktualny przebieg procesu obsługi zgłoszenia

Ten dokument opisuje aktualny stan aplikacji po refaktoryzacji na blueprinty, serwisy, repozytoria i konfigurowalne workflow dokumentów.

Proces nie jest już pisany wyłącznie pod jeden formularz. Definicja formularza z Nextcloud jest normalizowana przez `FormConfigService` i od tego momentu kod pracuje na:

```text
form_config["documents"]
form_config["workflow"]
form_config["notifications"]
form_config["rules"]
```

Stary format `process.documents` nadal działa jako kompatybilność wsteczna.

---

## Główne elementy techniczne

### Aplikacja

`app.py` jest normalną fabryką Flask:

```python
create_app(config_object=None, storage_override=None)
```

Fabryka tworzy kontener serwisów, zapisuje go w:

```python
app.extensions["services"]
```

i rejestruje blueprinty:

```text
routes/public_forms.py
routes/documents.py
routes/api.py
```

`legacy_app.py` nie jest główną aplikacją. Pozostaje tymczasowo jako źródło niektórych helperów migracyjnych.

### Serwisy

Aktualny kontener tworzy:

```text
storage
storage_repository
submission_repository
submission_service
workflow_service
document_service
notification_service
audit_log_service
access_token_service
form_config_service
rules_service
```

Trasy pobierają zależności przez:

```python
current_app.extensions["services"]
```

---

## Przebieg biznesowy

### 1. Wysłanie formularza

Użytkownik wypełnia formularz publiczny.

Endpoint:

```text
POST /submit/<slug>
```

Route przekazuje obsługę do `SubmissionService.submit_form()`.

Serwis:

- pobiera dane z formularza,
- waliduje je,
- tworzy `submission_id`,
- generuje `access_token`,
- zapisuje zgłoszenie przez `SubmissionRepository`,
- generuje główny PDF formularza,
- zapisuje PDF,
- zapisuje audit log `FORM_SUBMITTED`,
- zapisuje audit log `PDF_GENERATED`,
- wysyła `FORM_SUBMITTED`, jeżeli jest skonfigurowane.

Status startowy:

```text
FORM_SUBMITTED
```

Minimalne pola techniczne:

```text
submission_id
form_slug
form_name
created_at
pdf_filename
access_token
process_status
```

---

### 2. Decyzja urzędnika

Urzędnik aktualizuje dane w CSV/eBiuro.

Obsługiwane pola:

```text
officer_decision
acceptance_required
officer_decision_reason
officer_decision_email_requested
officer_decision_email_sent
decision_email_sent
decision_email_sent_for
```

`officer_decision` jest polem docelowym.

`acceptance_required` jest polem kompatybilności wstecznej.

Dozwolone decyzje:

```text
TAK
NIE
```

Statusy:

```text
OFFICER_ACCEPTED
OFFICER_REJECTED
WAITING_FOR_OFFICER_DECISION
```

Endpoint statusu:

```text
GET /api/submissions/<submission_id>/acceptance-status
```

Ten endpoint jest idempotentny dla maili decyzji. Odświeżanie strony nie powinno wysyłać kolejnych wiadomości dla tej samej decyzji.

---

### 3. Strona dokumentów do podpisania

Endpoint:

```text
GET /do-podpisania
POST /do-podpisania
GET /do-podpisania?submission_id=<id>
```

Jeżeli `submission_id` jest w query stringu, strona od razu pokazuje aktualny stan tego wniosku. Po operacjach na dokumentach redirect wraca na:

```text
/do-podpisania?submission_id=<id>
```

Dzięki temu użytkownik nie trafia po każdej operacji do pustego formularza z samym ID wniosku.

Widok jest budowany przez warstwę pośrednią w `routes/documents.py`:

```text
build_documents_to_sign_result()
```

Docelowo template powinien pracować na liście dokumentów i akcji workflow, a nie na pojedynczych polach historycznych.

---

### 4. Uzupełnienie deklaracji

Endpoint:

```text
GET/POST /declaration/<slug>/<submission_id>
```

Pola deklaracji pochodzą z konfiguracji dokumentu `declaration`.

Obsługiwane jest:

```text
documents[].fields
process.documents.declaration.fields
```

Po poprawnym wysłaniu formularza deklaracji:

- dane deklaracji są zapisywane w zgłoszeniu,
- `RulesService` stosuje reguły,
- `DocumentService.generate_document(..., "declaration", force=True)` generuje deklarację PDF,
- użytkownik wraca na `/do-podpisania?submission_id=<id>`.

Status po wygenerowaniu deklaracji:

```text
DECLARATION_WAITING_FOR_SIGNATURE
```

---

### 5. Podpis deklaracji

Użytkownik pobiera deklarację PDF, podpisuje ją zewnętrznie i wgrywa podpisany PDF.

Endpoint:

```text
POST /upload-declaration-signed/<slug>/<submission_id>
```

Dopuszczalne podpisy:

```text
mSzafir
Profil Zaufany
```

Po uploadzie system:

- zapisuje podpisany PDF, jeśli podpis jest poprawny,
- zapisuje pola podpisu deklaracji,
- zapisuje audit log `SIGNED_DOCUMENT_UPLOADED`,
- zapisuje `SIGNATURE_VERIFIED` albo `SIGNATURE_INVALID`.

Pola:

```text
declaration_signed
declaration_signed_filename
declaration_signature_type
declaration_signature_valid
declaration_signature_error
```

Statusy:

```text
AGREEMENT_READY
DECLARATION_SIGNATURE_INVALID
PARTICIPANT_ACCEPTED
```

`PARTICIPANT_ACCEPTED` może pojawić się po deklaracji tylko wtedy, gdy formularz nie wymaga umowy.

---

### 6. Reguły blokowania umowy

Reguły są konfigurowane w JSON w sekcji:

```text
rules
```

Przykład:

```json
{
  "id": "block_agreement_if_not_eligible",
  "when": {
    "any": [
      { "field": "deklaracja_18_lat", "equals": "Nie" },
      { "field": "deklaracja_lubuskie", "equals": "Nie" }
    ]
  },
  "then": [
    { "action": "set_field", "field": "agreement_blocked", "value": "Tak" },
    { "action": "set_field", "field": "agreement_block_reason", "value": "Warunki nie zostały spełnione na podstawie deklaracji uczestnika." },
    { "action": "set_status", "value": "AGREEMENT_BLOCKED" }
  ]
}
```

Obsługiwane operatory:

```text
equals
not_equals
in
not_in
any
all
```

Obsługiwane akcje:

```text
set_field
set_status
block_document
unblock_document
```

Jeżeli reguły zarządzają blokadą umowy i po korekcie deklaracji warunki blokady nie są już spełnione, system czyści stare:

```text
agreement_blocked
agreement_block_reason
```

---

### 7. Generowanie umowy lub umów szkoleniowych

Endpoint:

```text
POST /agreements/<slug>/<submission_id>/generate
```

Data wygenerowania umowy jest ustawiana automatycznie z dnia generowania:

```python
date.today().isoformat()
```

Użytkownik nie wybiera tej daty na stronie.

`DocumentService` obsługuje:

```text
generate_document()
generate_documents_for_collection()
```

Umowa może być pojedyncza:

```json
{
  "id": "agreement",
  "kind": "generated_pdf",
  "template": "Template/umowa.html"
}
```

albo powtarzana po kolekcji, na przykład po wybranych szkoleniach:

```json
{
  "id": "training_agreement",
  "label": "Umowa szkoleniowa",
  "kind": "generated_pdf",
  "template": "Template/umowa-wiedza-kluczem.html",
  "filename_pattern": "{first_name}_{last_name}-{training_id}-umowa.pdf",
  "signature_required": true,
  "repeat_over": "selected_trainings",
  "repeat_item_alias": "training",
  "numbering": {
    "number_pattern": "{submission_id}/{agreement_sequence}/{generated_date}"
  }
}
```

Po wygenerowaniu:

```text
agreement_generated = Tak
agreement_generated_at = YYYY-MM-DD
process_status = AGREEMENT_WAITING_FOR_SIGNATURE
```

Dla wielu umów szczegóły są w polu JSON:

```text
training_agreements
```

---

### 8. Podpis umowy przez uczestnika

Aktualny user-facing upload umowy dotyczy podpisu uczestnika/osoby prywatnej.

Endpoint:

```text
POST /agreements/<slug>/<submission_id>/<agreement_id>/upload
```

Użytkownik:

1. Pobiera umowę PDF.
2. Podpisuje ją zewnętrznie, na przykład Profilem Zaufanym albo mSzafirem.
3. Wgrywa podpisany PDF na stronie.

System:

- weryfikuje podpis,
- zapisuje podpisany PDF,
- aktualizuje wpis w `training_agreements`, jeżeli umów jest wiele,
- ustawia `agreement_signature_valid`,
- po podpisaniu kompletu umów ustawia status `AGREEMENT_SIGNED`,
- wysyła zdarzenie `AGREEMENT_SIGNED`.

Pola:

```text
agreement_signed
agreement_signed_filename
agreement_signature_type
agreement_signature_valid
agreement_signature_error
agreement_success_email_sent
agreement_success_email_sent_for
```

Statusy:

```text
AGREEMENT_SIGNED
AGREEMENT_SIGNATURE_INVALID
AGREEMENT_WAITING_FOR_SIGNATURE
```

---

### 9. Podpis umowy przez urząd

Podpis urzędu jest kolejnym etapem biznesowym po podpisie uczestnika.

Aktualny kod nie ma osobnej publicznej trasy dla uploadu kontrasygnaty urzędu.

W kodzie pozostawiono pola przygotowujące pod ten etap:

```text
office_agreement_signed_email_sent
office_agreement_signed_email_sent_for
```

`NotificationService` potrafi zbudować treść tekstową również dla zdarzenia:

```text
OFFICE_AGREEMENT_SIGNED
```

To zdarzenie jest zarezerwowane dla przyszłej integracji lub ręcznego procesu urzędowego.

---

### 10. Pobieranie PDF i tokeny

Każde nowe zgłoszenie dostaje:

```text
access_token
```

Linki do PDF powinny być budowane przez:

```python
DocumentService.build_download_url(submission, filename, signed=False)
```

Endpointy pobrania:

```text
GET /downloads/pdfs/<slug>/<filename>?token=<access_token>
GET /downloads/signed/<slug>/<filename>?token=<access_token>
```

Prawidłowy token daje `200`.

Błędny token daje `403`.

Brak tokenu w linku daje `403`.

Dla starych zgłoszeń bez tokenu `DocumentService.ensure_access_token()` może wygenerować token przy budowaniu linku i zapisać go przez repozytorium.

---

### 11. Powiadomienia e-mail

Powiadomienia są event-based.

Konfiguracja w JSON:

```json
"notifications": [
  {
    "event": "AGREEMENT_SIGNED",
    "to": ["form_notifications"],
    "template": "Template/Mail/agreement_signed.html",
    "subject": "Umowa podpisana przez uczestnika"
  }
]
```

Obsługiwani odbiorcy:

```text
participant
form_notifications
field:nazwa_pola
literalny adres e-mail
```

`form_notifications` używa:

```text
FORM_NOTIFICATION_EMAILS
```

Jeżeli JSON formularza nie zawiera `AGREEMENT_SIGNED`, trasa uploadu umowy dodaje fallback:

```text
event: AGREEMENT_SIGNED
template: Template/Mail/agreement_signed.html
to: form_notifications
```

Szablony maili mogą być lokalne albo z Nextcloud. Jeśli lokalny szablon nie istnieje, `NotificationService` próbuje odczytać go ze storage/Nextcloud. Jeśli szablonu nie ma, używa niepustej treści domyślnej.

Wysyłka decyzji urzędnika jest idempotentna:

```text
decision_email_sent
decision_email_sent_for
officer_decision_email_sent
```

Wysyłka `AGREEMENT_SIGNED` jest idempotentna:

```text
agreement_success_email_sent
agreement_success_email_sent_for
```

---

### 12. Audit log

Audit log obsługuje repozytorium storage:

```text
output/<form_slug>/audit/audit_log.jsonl
```

Istnieje też lokalny fallback:

```text
TEMP_DIR/audit_log.jsonl
```

Zdarzenia używane w procesie:

```text
FORM_SUBMITTED
PDF_GENERATED
DOCUMENT_GENERATED
DOCUMENT_DOWNLOADED
SIGNED_DOCUMENT_UPLOADED
SIGNATURE_VERIFIED
SIGNATURE_INVALID
OFFICER_DECISION_CHANGED
DECISION_EMAIL_SENT
WORKFLOW_STATUS_CHANGED
PROCESS_COMPLETED
```

---

## Statusy procesu

Aktualnie używane i obsługiwane statusy:

```text
FORM_SUBMITTED
WAITING_FOR_OFFICER_DECISION
OFFICER_ACCEPTED
OFFICER_REJECTED
DECLARATION_NOT_REQUIRED
DECLARATION_READY
DECLARATION_WAITING_FOR_SIGNATURE
DECLARATION_SIGNED
DECLARATION_SIGNATURE_INVALID
AGREEMENT_NOT_REQUIRED
AGREEMENT_BLOCKED
AGREEMENT_READY
AGREEMENT_WAITING_FOR_SIGNATURE
AGREEMENT_SIGNED
AGREEMENT_SIGNATURE_INVALID
PARTICIPANT_ACCEPTED
PARTICIPANT_REJECTED
PROCESS_COMPLETED
```

Uwaga: `AGREEMENT_SIGNED` oznacza aktualnie poprawnie zweryfikowaną umowę podpisaną przez uczestnika. Nie oznacza jeszcze podpisu urzędu.

---

## Minimalny zestaw pól CSV/eBiuro

```text
submission_id
form_slug
form_name
created_at
email
first_name
last_name
pesel
pdf_filename
access_token
process_status

officer_decision
officer_decision_reason
officer_decision_email_requested
officer_decision_email_sent
decision_email_sent
decision_email_sent_for
acceptance_required

declaration_required
declaration_generated
declaration_filename
declaration_signed
declaration_signature_type
declaration_signature_valid
declaration_signature_error
declaration_signed_filename

agreement_required
agreement_blocked
agreement_block_reason
agreement_generated
agreement_generated_at
agreement_filename
agreement_signed
agreement_signature_type
agreement_signature_valid
agreement_signature_error
agreement_signed_filename
training_agreements

agreement_success_email_sent
agreement_success_email_sent_for
office_agreement_signed_email_sent
office_agreement_signed_email_sent_for
requirements_rejection_email_sent
```

---

## Testy

Pełny zestaw:

```powershell
python -m pytest -q
```

Aktualnie oczekiwany wynik:

```text
81 passed
```
