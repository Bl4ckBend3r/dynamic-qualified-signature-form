# Raport zależności legacy

Aktualny runtime jest budowany przez `app.py`; ten raport dokumentuje granice zgodności, a nie zachęca do nowych importów.

| Plik | Relacja z legacy | Oczekiwany stan |
|---|---|---|
| `routes/documents.py` | obsługuje bieżące i ograniczone ścieżki zgodności dokumentów | bez importu `legacy_app.py`, zapis przez usługi dokumentowe |
| `services/container.py` | udostępnia canonical zależności aplikacji | bez tworzenia aliasów dla nowych funkcji |
| `legacy_app.py` | historyczne wrappery i direct execution | wyłącznie testy zgodności; brak importu z runtime factory |

Kontrolę należy wykonywać testem importów runtime oraz zawężonym wyszukiwaniem odwołań. Sam brak importu nie wystarcza do usunięcia fallbacków danych — obowiązuje plan migracji i strict readiness.
