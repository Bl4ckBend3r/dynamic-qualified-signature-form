# Plan wydzielenia zasobów frontendu

Dokument archiwizuje zakończony etap ograniczania kodu inline w dużych szablonach. Kod wykonywalny i testy pozostają źródłem prawdy.

## Dokumenty do podpisu

- Style widoku przeniesiono do `static/css/documents_to_sign.css`.
- Obsługę interakcji i przesyłania dokumentów przeniesiono do `static/js/documents_to_sign.js`.
- `templates/documents_to_sign.html` ładuje oba zasoby i nie zawiera własnych bloków `<style>` ani `<script>`.
- Publiczny CSRF, token uczestnika i walidacja uploadu pozostają po stronie istniejących routes oraz usług.

## Kontrola po zmianie

Test szablonu pilnuje odwołań do obu zasobów i braku kodu inline. Zmiany zachowania należy dodatkowo sprawdzać w testach publicznego uploadu dokumentów.
