# P2.1 remaining template patch

GitHub Contents API blokowalo pelne nadpisanie duzego szablonu `templates/documents_to_sign.html`, dlatego dodano skrypt lokalny wykonujacy ostatni krok P2.1 automatycznie.

## Szybkie wykonanie

Po pobraniu zmian uruchom lokalnie z katalogu repozytorium:

```powershell
.venv\Scripts\python.exe scripts\apply_p2_1_template_patch.py
.venv\Scripts\python.exe -m pytest -q
```

Jesli nie uzywasz `.venv`, uruchom zwyklym Pythonem:

```powershell
python scripts\apply_p2_1_template_patch.py
python -m pytest -q
```

## Co robi skrypt

Skrypt `scripts/apply_p2_1_template_patch.py`:

1. dodaje do `templates/documents_to_sign.html` bloki `extra_css` i `extra_js`,
2. podpina `static/documents_to_sign.css`,
3. podpina `static/documents_to_sign.js`,
4. usuwa stary inline blok CSS,
5. usuwa stary inline blok JS,
6. jest idempotentny, czyli ponowne uruchomienie nie powinno zmieniac pliku drugi raz.

## Pliki juz przygotowane

- `templates/base.html` ma bloki `extra_css` i `extra_js`.
- `static/documents_to_sign.css` istnieje.
- `static/documents_to_sign.js` istnieje.
- `scripts/apply_p2_1_template_patch.py` istnieje.
- `tests/test_p2_1_template_patch_script.py` sprawdza zachowanie skryptu.

## Reczna alternatywa

Jesli skrypt nie zostanie uruchomiony, wykonaj recznie w `templates/documents_to_sign.html`:

1. Dodaj po bloku `title`:

```jinja2
{% block extra_css %}
    <link rel="stylesheet" href="{{ url_for('static', filename='documents_to_sign.css') }}">
{% endblock %}

{% block extra_js %}
    <script src="{{ url_for('static', filename='documents_to_sign.js') }}"></script>
{% endblock %}
```

2. Usun caly blok od `<style>` do `</style>`.
3. Usun caly blok od `<script>` do `</script>`.
4. Zostaw koncowy `{% endblock %}` jako zamkniecie bloku `content`.

## Po patchu

Docelowo mozna zaostrzyc `tests/test_p2_1_frontend_assets.py`, aby wymagalo:

```python
assert "documents_to_sign.css" in template
assert "documents_to_sign.js" in template
assert "<style>" not in template
assert "<script>" not in template
```
