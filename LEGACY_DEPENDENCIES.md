# Legacy dependencies

Ten raport opisuje aktualne zaleznosci od `legacy_app.py`. Plik pozostaje w repozytorium, bo nadal jest uzywany przez warstwe kompatybilnosci i testy migracyjne.

## Przeniesione w P1.2

| Element | Nowe miejsce | Status |
| --- | --- | --- |
| `get_training_selection_field` | `services/training_agreement_service.py` | `routes/documents.py` uzywa juz nowego serwisu. `legacy_app.py` zostal jako wrapper zgodnosci. |
| `extract_training_selection` | `services/training_agreement_service.py` | `routes/documents.py` uzywa juz nowego serwisu. `legacy_app.py` deleguje do serwisu. |
| `build_training_agreement_number` | `services/training_agreement_service.py` | `legacy_app.py` deleguje do serwisu, stare testy zachowuja kompatybilnosc. |

## Aktywne zaleznosci

| Miejsce | Uzywane elementy | Docelowe miejsce | Ryzyko migracji |
| --- | --- | --- | --- |
| `services/container.py` | `install_legacy_helpers()` ustawia `legacy_app.app`, `legacy_app.storage`, `legacy_app.access_token_service` | Usunac po przepieciu ostatnich helperow generowania dokumentow do serwisow | Srednie: pozostale helpery legacy nadal oczekuja tych globali. |
| `tests/conftest.py` | monkeypatch `legacy_app.generate_pdf` | Testy docelowo powinny patchowac serwis PDF | Niskie po przeniesieniu generowania PDF do serwisu. |
| `tests/test_training_agreements.py` | `generate_training_agreements_for_submission`, `ensure_declaration_generated` oraz kompatybilne importy przeniesionych helperow | `services/training_agreement_service.py` i serwisy dokumentowe | Wysokie: obejmuje szablony, storage i generowanie PDF. |
| `README.md`, `docs/process-workflow.md`, `docs/document-configuration.md` | Opis tymczasowej roli legacy i zdarzen dokumentow | Zaktualizowac po usunieciu importow runtime | Niskie: dokumentacja, bez wplywu runtime. |

## Aktualny stan runtime

`routes/documents.py` nie importuje juz `legacy_app.py`. Runtime import legacy pozostaje w `services/container.py`, gdzie utrzymywana jest zgodnosc dla pozostalych helperow migracyjnych.

## Kolejny rekomendowany krok

Nastepny kandydat to wydzielenie czystych fragmentow `generate_training_agreements_for_submission()`: przygotowanie wierszy kontekstu dla pojedynczej umowy i wybor konfiguracji numeracji. Samo generowanie PDF oraz `ensure_declaration_generated()` powinny zostac w legacy do czasu wydzielenia rendererow PDF i storage.
