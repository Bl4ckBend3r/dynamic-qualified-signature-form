# Frontend split plan

`templates/documents_to_sign.html` nadal zawiera inline CSS i JS. W P1.3 nie wydzielono ich fizycznie, bo `templates/base.html` nie ma jeszcze blokow per-template dla dodatkowych assetow, a zmiana layoutu globalnego powinna byc osobnym, testowanym krokiem P2.

## Zakres do wydzielenia

| Element | Obecne miejsce | Docelowe miejsce |
| --- | --- | --- |
| Style widoku dokumentow | `templates/documents_to_sign.html` blok `<style>` | `static/documents_to_sign.css` |
| Logika statusu, kafli i upload dropzone | `templates/documents_to_sign.html` blok `<script>` | `static/documents_to_sign.js` |

## Bezpieczna kolejnosc

1. Dodac w `templates/base.html` bloki `extra_css` i `extra_js`.
2. Przeniesc CSS bez zmiany selektorow i klas.
3. Przeniesc JS bez zmiany nazw funkcji i bez zmiany API.
4. Jesli JS bedzie potrzebowal danych z Jinja, przekazac je przez `data-*` lub JSON script tag.
5. Dodac test, ze `documents_to_sign.html` laduje `documents_to_sign.css` i `documents_to_sign.js`.
6. Dopiero po tym usunac inline bloki.

## Ryzyka

- JS obsluguje stan po odpowiedzi API, wymiane kart po pobraniu oraz drag-and-drop uploadu.
- Widok nadal korzysta z backendowych flag statusu, wiec nie nalezy przy okazji przywracac lokalnych list statusow.
- Zmiana `base.html` dotknie wszystkie strony, dlatego powinna byc osobnym krokiem z testem renderowania kilku widokow.
