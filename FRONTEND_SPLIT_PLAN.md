# Frontend split plan

`templates/documents_to_sign.html` nadal zawiera inline CSS i JS, ale P2.1 przygotowal warstwe assetow i layout pod bezpieczne usuniecie inline blokow.

## Wykonane w P2.1

| Element | Status |
| --- | --- |
| Bloki `extra_css` i `extra_js` w `templates/base.html` | Wykonane. |
| Plik `static/documents_to_sign.css` | Wykonane. Zawiera style przeniesione z widoku dokumentow. |
| Plik `static/documents_to_sign.js` | Wykonane. Zawiera logike statusu, kafli, wymiany karty po pobraniu i drag-and-drop uploadu. |
| Testy assetow P2.1 | Dodano `tests/test_p2_1_frontend_assets.py`. |

## Pozostalo do wykonania

| Element | Obecne miejsce | Docelowe miejsce |
| --- | --- | --- |
| Usuniecie inline stylow | `templates/documents_to_sign.html` blok `<style>` | `static/documents_to_sign.css` jako jedyne zrodlo stylow. |
| Usuniecie inline skryptu | `templates/documents_to_sign.html` blok `<script>` | `static/documents_to_sign.js` jako jedyne zrodlo logiki. |
| Zaladowanie assetow w szablonie | Brak pelnego przepiecia | Bloki `extra_css` i `extra_js` w `templates/documents_to_sign.html`. |

## Bezpieczna kolejnosc kolejnego commita

1. Dodac w `templates/documents_to_sign.html` blok `extra_css` z linkiem do `documents_to_sign.css`.
2. Dodac blok `extra_js` z linkiem do `documents_to_sign.js`.
3. Usunac blok `<style>` bez zmiany klas i selektorow.
4. Usunac blok `<script>` bez zmiany nazw funkcji i bez przywracania lokalnych list statusow.
5. Zaktualizowac test tak, aby wymagac braku duzych inline blokow.
6. Uruchomic pelne testy i sprawdzic widok recznie w przegladarce.

## Ryzyka

- JS obsluguje stan po odpowiedzi API, wymiane kart po pobraniu oraz drag-and-drop uploadu.
- Widok nadal korzysta z backendowych flag statusu, wiec nie nalezy przy okazji przywracac lokalnych list statusow.
- Zmiana `base.html` dotyka wszystkie strony, dlatego P2.1 ograniczyl sie do dodania pustych blokow i testow.
- Pelne usuniecie inline blokow powinno byc osobnym commitem tylko na `templates/documents_to_sign.html`, z latwiejszym review diffu.
