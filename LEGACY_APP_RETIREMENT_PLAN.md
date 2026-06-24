# Legacy app retirement plan

P4.6 nie usuwa `legacy_app.py`, nie rejestruje jego endpointow w `create_app()` i nie zmienia publicznych URL-i. Ten dokument przygotowuje decyzje: usuniecie pliku albo pozostawienie go jako diagnostycznego entrypointu.

## Aktualny status

- `legacy_app.py` istnieje jako historyczny modul zgodnosci.
- Runtime `create_app()` nie importuje `legacy_app.py`.
- Publiczne endpointy aplikacji sa rejestrowane przez aktualne blueprinty.
- Importy `legacy_app.py` sa test-only.

## Aktualne importy test-only

- `tests/conftest.py`
- `tests/test_training_agreements.py`
- `tests/test_training_agreement_service.py`
- `tests/test_document_split_services.py`
- `tests/test_runtime_legacy_imports.py`
- `tests/test_legacy_dependencies.py`

Liste nalezy potwierdzac przez:

```powershell
rg -n "legacy_app" .
```

## Warunki usuniecia

- Potwierdzono, ze nikt nie uruchamia `legacy_app.py` jako entrypointu produkcyjnego.
- Testy zgodnosci zostaly przepisane na nowe serwisy albo przeniesione do pakietu legacy.
- Historyczne endpointy maja odpowiedniki w aktualnym runtime albo sa swiadomie porzucone.
- Dokumentacja wdrozeniowa nie wskazuje `legacy_app.py` jako sposobu startu.
- Pelny pakiet testow przechodzi bez importu `legacy_app.py`.

## Warunki pozostawienia

- Plik jest potrzebny jako diagnostyczny entrypoint.
- Testy kompatybilnosci maja swiadomie utrzymywac historyczne wrappery.
- Zespol chce zachowac plik do czasu migracji danych legacy.
- Plik pozostaje oznaczony jako legacy i nie jest importowany przez runtime `create_app()`.

## Testy do przepisania przed usunieciem

- Testy wrapperow treningowych.
- Testy wrapperow dokumentowych.
- Testy runtime legacy importow.
- Testy zaleznosci legacy.
- Fixture w `tests/conftest.py`, jesli nadal ustawia globalny stan `legacy_app`.

## Ryzyka

- Zewnetrzny proces moze nadal uruchamiac `legacy_app.py` bez wiedzy repozytorium.
- Usuniecie wrapperow moze utrudnic porownanie zachowania historycznego.
- Diagnostyka starych danych moze wymagac dostepu do legacy helperow.

## Rekomendowany wariant

Pozostawic `legacy_app.py` do czasu zakonczenia stabilizacji strict mode i decyzji o destrukcyjnej migracji legacy. Usuniecie powinno byc osobnym etapem po spelnieniu checklisty i po potwierdzeniu, ze importy sa wylacznie test-only albo juz nie istnieja.
