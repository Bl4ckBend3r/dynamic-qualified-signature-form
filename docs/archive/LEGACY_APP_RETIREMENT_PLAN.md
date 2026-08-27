# Plan wycofania `legacy_app.py`

Moduł ma status test-only i historycznego trybu bezpośredniego uruchomienia. Produkcyjny entrypoint to `app.py::create_app()`, który rejestruje blueprints i kontener usług bez importowania tego modułu.

## Warunki pozostawienia

- istnieją testy regresyjne lub wrappery kompatybilności bez bezpiecznego odpowiednika;
- historyczny direct execution jest jeszcze potrzebny do kontrolowanej migracji;
- raport zależności wskazuje aktywne importy poza dozwolonym zakresem testowym.

## Warunki usuniecia

- runtime i narzędzia administracyjne korzystają wyłącznie z `create_app()`;
- wszystkie wrappery mają odpowiedniki w services/routes i testy regresyjne;
- nie istnieją importy runtime, a pełny zestaw testów przechodzi po usunięciu;
- instrukcje uruchomienia i dokumentacja wskazują wyłącznie bieżący entrypoint.

Usunięcie musi być osobną, łatwą do przeglądu zmianą i nie może równocześnie zmieniać logiki domenowej.
