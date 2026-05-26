# Dynamic Qualified Signature Form

Flask application for dynamic form submissions, generated PDF documents, external electronic signatures and configurable document workflows.

The current implementation is no longer a single hard-coded flow in `legacy_app.py`. The application starts from a normal Flask application factory, registers blueprints, creates a service container, reads form definitions from Nextcloud and keeps the current CSV/Nextcloud storage adapter as the persistence layer.

## What The Application Does

- Reads JSON form definitions from Nextcloud.
- Renders public forms dynamically.
- Validates submitted data on the backend.
- Saves submissions through a repository abstraction.
- Generates the main submission PDF.
- Handles officer decision status.
- Generates declaration and agreement PDFs from HTML templates stored in Nextcloud.
- Supports one agreement or repeated training agreements.
- Verifies uploaded signed PDFs.
- Protects PDF download links with per-submission access tokens.
- Sends event-based e-mail notifications.
- Writes audit logs through an audit log service and repository.

## Current Architecture

Main entry point:

```text
app.py
```

`app.py` exposes:

```python
create_app(config_object=None, storage_override=None)
```

The factory:

- loads `Config`,
- creates the service container,
- stores it in `app.extensions["services"]`,
- registers blueprints,
- registers context processors,
- installs temporary legacy helper aliases.

Blueprints:

```text
routes/public_forms.py
routes/documents.py
routes/api.py
```

Services:

```text
services/container.py
services/submission_service.py
services/document_service.py
services/workflow_service.py
services/notification_service.py
services/rules_service.py
services/audit_log_service.py
services/form_config_service.py
services/access_token_service.py
```

Repositories:

```text
repositories/submission_repository.py
repositories/storage_repository.py
repositories/audit_log_repository.py
```

`legacy_app.py` still exists only as a temporary source of helper functions used during migration. It is not the main Flask application.

## Process Flow

Current supported process:

1. User submits the public form.
2. System saves the submission and generates the main form PDF.
3. Officer accepts or rejects the submission in the CSV/eBiuro data.
4. Accepted user opens the documents page with their submission ID.
5. User completes declaration fields.
6. System generates the declaration PDF.
7. User signs the declaration externally, for example with Profil Zaufany or mSzafir.
8. User uploads the signed declaration PDF.
9. System verifies the declaration signature.
10. Rules decide whether the agreement can be generated.
11. System generates one agreement or repeated training agreements.
12. User downloads the agreement PDF.
13. User signs the agreement externally.
14. User uploads the signed agreement PDF.
15. System verifies the agreement signature and emits `AGREEMENT_SIGNED`.
16. The agreement can then be handled manually or by a future office-signature step.

Important: the agreement is signed by the participant first. Office signature is a later downstream step and is not currently exposed as a separate user route.

## Nextcloud Files

Form JSON files and HTML templates are stored in Nextcloud.

Example:

```text
Strona WWW/
└── Formularze/
    ├── sample_form.json
    └── Template/
        ├── deklaracja-wiedza-kluczem.html
        ├── umowa-wiedza-kluczem.html
        └── Mail/
            ├── potwierdzenie.html
            └── agreement_signed.html
```

If `NEXTCLOUD_FORMS_DIR=Strona WWW/Formularze`, then this JSON value:

```json
"template": "Template/deklaracja-wiedza-kluczem.html"
```

points to:

```text
Strona WWW/Formularze/Template/deklaracja-wiedza-kluczem.html
```

## Configuration Format

Preferred document configuration is the normalized top-level `documents` list:

```json
"documents": [
  {
    "id": "declaration",
    "label": "Deklaracja uczestnictwa",
    "kind": "generated_pdf",
    "template": "Template/deklaracja-wiedza-kluczem.html",
    "filename_pattern": "{first_name}_{last_name}-deklaracja.pdf",
    "signature_required": true,
    "fields": []
  },
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
]
```

The legacy format is still supported:

```json
"process": {
  "documents": {
    "declaration": { "enabled": true },
    "agreement": { "enabled": true }
  }
}
```

`FormConfigService` normalizes both formats into `form_config["documents"]`.

## Rules

Workflow rules are configurable in JSON:

```json
"rules": [
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
]
```

Supported condition operators:

```text
equals
not_equals
in
not_in
any
all
```

Supported actions:

```text
set_field
set_status
block_document
unblock_document
```

## Notifications

Notifications are configured in form JSON and can use templates stored in Nextcloud.

Example event after the participant uploads a correctly signed agreement:

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

If the form JSON does not define `AGREEMENT_SIGNED`, the documents route adds a fallback notification that uses:

```text
Template/Mail/agreement_signed.html
```

Recipients:

- `participant` sends to the submission `email`,
- `form_notifications` sends to `FORM_NOTIFICATION_EMAILS`,
- `field:nazwa_pola` reads recipients from a submission field,
- a literal e-mail address can be used directly.

Decision e-mails use local templates if available and fall back to Nextcloud/default HTML if needed.

## Environment

Important environment variables:

```env
NEXTCLOUD_BASE_URL=
NEXTCLOUD_USERNAME=
NEXTCLOUD_APP_PASSWORD=
NEXTCLOUD_FORMS_DIR=Strona WWW/Formularze
NEXTCLOUD_OUTPUT_DIR=Strona WWW/output
CSV_FILENAME=dane.csv

SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
MAIL_FROM=
SMTP_USE_TLS=true
SMTP_USE_SSL=false
FORM_NOTIFICATION_EMAILS=koordynator@example.com
```

## Running Locally

Install dependencies, then install Chromium for PDF generation:

```powershell
python -m playwright install chromium
```

Run the app:

```powershell
python app.py
```

Default local URL:

```text
http://127.0.0.1:5000
```

## Tests

Run all tests:

```powershell
python -m pytest -q
```

Run route and notification tests:

```powershell
python -m pytest tests/test_notification_service.py tests/test_routes.py -q
```

Current expected result after the refactor:

```text
81 passed
```

## Validating Form JSON

Validate a local JSON file:

```powershell
python manage.py validate-form forms/sample_form.json --skip-template-check
```

Use a local template root when templates are available on disk:

```powershell
python manage.py validate-form forms/sample_form.json --template-root C:\path\to\templates
```

Use `--skip-template-check` for forms whose templates exist only in Nextcloud.

## More Documentation

```text
docs/process-workflow.md
docs/document-configuration.md
docs/json-and-html-templates.md
docs/instrukcja-deklaracje-i-umowy.md
```
