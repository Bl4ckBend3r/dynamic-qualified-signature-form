# P2.1 remaining template patch

Ten plik dokumentuje ostatni krok P2.1, ktory powinien zostac wykonany lokalnie, jezeli GitHub Contents API blokuje pelne nadpisanie duzego szablonu `templates/documents_to_sign.html`.

## Cel

W `templates/documents_to_sign.html`:

1. Dodac po bloku `title`:

```jinja2
{% block extra_css %}
    <link rel="stylesheet" href="{{ url_for('static', filename='documents_to_sign.css') }}">
{% endblock %}

{% block extra_js %}
    <script src="{{ url_for('static', filename='documents_to_sign.js') }}"></script>
{% endblock %}
```

2. Usunac caly blok od:

```html
<style>
```

do:

```html
</style>
```

3. Usunac caly blok od:

```html
<script>
```

do:

```html
</script>
```

4. Zostawic koncowy:

```jinja2
{% endblock %}
```

jako zamkniecie bloku `content`.

## Pliki juz przygotowane

- `templates/base.html` ma bloki `extra_css` i `extra_js`.
- `static/documents_to_sign.css` istnieje.
- `static/documents_to_sign.js` istnieje.

## Po patchu

Zaktualizowac `tests/test_p2_1_frontend_assets.py` tak, aby wymagalo:

```python
assert "documents_to_sign.css" in template
assert "documents_to_sign.js" in template
assert "<style>" not in template
assert "<script>" not in template
```

Nastepnie uruchomic:

```powershell
.venv\Scripts\python.exe -m pytest -q
```
