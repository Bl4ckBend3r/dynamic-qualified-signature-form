# Dynamiczny formularz Flask z PDF, CSV i procesem kwalifikowanego podpisu

Aplikacja webowa w Pythonie umożliwiająca:

- wczytanie definicji formularza z pliku JSON,
- dynamiczne renderowanie formularza w HTML,
- walidację danych po stronie backendu,
- generowanie dokumentu PDF,
- zapis danych formularza do pliku CSV,
- uruchomienie procesu kwalifikowanego podpisu elektronicznego przez warstwę abstrakcji,
- zapis metadanych procesu podpisu,
- przechowywanie pliku PDF oraz podpisanej wersji PDF.

## Założenie dotyczące podpisu

Aplikacja **nie implementuje odręcznego podpisu**, pola typu handwritten signature ani podpisu rysowanego myszką.

Podpis kwalifikowany jest traktowany jako **zewnętrzny proces podpisywania całego dokumentu PDF**.  
Warstwa `signature_service.py` udostępnia:

- interfejs `QualifiedSignatureProvider`,
- mock `MockQualifiedSignatureProvider` do testów lokalnych,
- szkielet `RestQualifiedSignatureProvider` do przyszłej integracji z rzeczywistym dostawcą przez REST API.

## Wymagania systemowe

### Python
- Python 3.10 lub nowszy

### Biblioteki systemowe dla WeasyPrint
Na Linux może być wymagane doinstalowanie zależności systemowych używanych przez WeasyPrint, np.:
- Pango
- Cairo
- GDK-PixBuf

Przykład dla Debian/Ubuntu:
```bash
sudo apt-get update
sudo apt-get install -y python3-dev build-essential libpango-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info
