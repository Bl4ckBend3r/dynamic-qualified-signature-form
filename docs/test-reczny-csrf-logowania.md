# Ręczny test CSRF formularza logowania

1. Otwórz `GET /admin/` w przeglądarce albo kliencie HTTP zachowującym ciasteczka. W kodzie formularza odczytaj wartość ukrytego pola `csrf_token`.
2. Wyślij `POST /admin/` z tym samym ciasteczkiem sesji oraz polami `email`, `password` i `csrf_token`. Dla poprawnych danych oczekiwane jest przekierowanie `302` do dashboardu; dla błędnego hasła odpowiedź `401`, ale nie błąd CSRF.
3. W nowej sesji pobierz formularz ponownie, a następnie wyślij `POST /admin/` bez `csrf_token` albo z wartością `niepoprawny-token`.
4. Oczekiwany wynik błędnej próby to HTTP `400`. Logowanie nie może zostać wykonane, nawet jeśli e-mail i hasło są poprawne.

Przykład z `curl` (plik `cookies.txt` zachowuje tę samą sesję):

```powershell
curl.exe -c cookies.txt http://localhost:5000/admin/
# Skopiuj token z: <input type="hidden" name="csrf_token" value="...">
curl.exe -b cookies.txt -X POST http://localhost:5000/admin/ -d "email=admin@example.com" -d "password=HASLO" -d "csrf_token=SKOPIOWANY_TOKEN"
curl.exe -b cookies.txt -X POST http://localhost:5000/admin/ -d "email=admin@example.com" -d "password=HASLO" -d "csrf_token=niepoprawny-token"
```
