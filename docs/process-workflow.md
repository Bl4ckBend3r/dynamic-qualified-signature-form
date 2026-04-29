# Docelowy przebieg procesu obsługi zgłoszenia

## Cel dokumentu

Dokument opisuje docelowy proces obsługi zgłoszenia uczestnika w aplikacji formularzy online.

Proces ma być możliwy do wykorzystania w wielu projektach, dlatego logika nie powinna być wpisana na sztywno dla jednego formularza. Poszczególne reguły, dokumenty, statusy i wiadomości e-mail powinny być konfigurowalne z poziomu definicji projektu oraz danych obsługiwanych w eBiurze.

---

## Główne założenia

1. Uczestnik wypełnia formularz online.
2. System zapisuje dane formularza w eBiurze jako tabelę danych.
3. System generuje PDF formularza zgłoszeniowego.
4. Urzędnik weryfikuje dane i wpisuje decyzję w odpowiednim polu tabeli.
5. Akceptacja urzędnika uruchamia etap deklaracji.
6. Deklaracja jest generowana automatycznie na podstawie danych z formularza.
7. Uczestnik uzupełnia pola `TAK/NIE` oraz wymagane zgody w deklaracji.
8. Część odpowiedzi w deklaracji może blokować wygenerowanie umowy.
9. Deklaracja musi zostać podpisana elektronicznie.
10. System weryfikuje podpis deklaracji.
11. Jeżeli warunki są spełnione, system generuje umowę.
12. Jeżeli warunki nie są spełnione, system blokuje umowę i zapisuje powód blokady.
13. Jeżeli umowa została wygenerowana, przechodzi analogiczny proces podpisu i weryfikacji.
14. System wysyła wiadomości e-mail zgodnie ze statusem procesu.

---

## Etapy procesu

### 1. Wypełnienie formularza online

Uczestnik wypełnia formularz online.

System wykonuje następujące operacje:

- waliduje dane formularza,
- zapisuje dane zgłoszenia,
- generuje PDF formularza zgłoszeniowego,
- zapisuje PDF w przestrzeni eBiura / Nextcloud,
- zapisuje dane formularza w tabeli CSV dostępnej dla eBiura.

Minimalne dane techniczne zgłoszenia:

```text
submission_id
form_slug
form_name
created_at
pdf_filename
process_status
```

Po zakończeniu tego etapu zgłoszenie otrzymuje status:

```text
FORM_SUBMITTED
```

---

### 2. Weryfikacja zgłoszenia przez urzędnika

Urzędnik weryfikuje dane uczestnika w tabeli eBiura.

Decyzja urzędnika jest wpisywana w odpowiednim polu tabeli:

```text
officer_decision
```

Dozwolone wartości:

```text
TAK
NIE
```

Dodatkowe pola:

```text
officer_decision_reason
officer_decision_email_requested
officer_decision_email_sent
```

Znaczenie pól:

- `officer_decision` — decyzja urzędnika,
- `officer_decision_reason` — powód odrzucenia lub komentarz,
- `officer_decision_email_requested` — informacja, czy system ma wysłać e-mail,
- `officer_decision_email_sent` — informacja, czy e-mail został wysłany.

Jeżeli decyzja urzędnika to `TAK`, proces przechodzi do generowania deklaracji.

Jeżeli decyzja urzędnika to `NIE`, proces kończy się odrzuceniem zgłoszenia i może zostać wysłany e-mail do uczestnika.

Statusy po decyzji urzędnika:

```text
OFFICER_ACCEPTED
OFFICER_REJECTED
```

---

### 3. Generowanie deklaracji uczestnictwa

Po akceptacji zgłoszenia przez urzędnika system generuje deklarację uczestnictwa.

Deklaracja jest generowana automatycznie na podstawie danych z formularza.

Dane uczestnika nie mogą być przepisywane ręcznie.

System powinien używać mapowania pól formularza na pola dokumentu deklaracji.

Przykładowe mapowanie:

```json
{
  "first_name": "imiona",
  "last_name": "nazwisko",
  "pesel": "pesel",
  "email": "email",
  "phone": "telefon"
}
```

Plik deklaracji powinien być nazwany według schematu:

```text
Imie_Nazwisko-deklaracja.pdf
```

Przykład:

```text
Jan_Kowalski-deklaracja.pdf
```

Po wygenerowaniu deklaracji proces otrzymuje status:

```text
DECLARATION_READY
```

Następnie:

```text
DECLARATION_WAITING_FOR_SIGNATURE
```

---

### 4. Uzupełnienie deklaracji przez uczestnika

Uczestnik uzupełnia w deklaracji wymagane pola `TAK/NIE` oraz zgody.

Część pól może być krytyczna dla dalszego procesu.

Przykładowe pola krytyczne:

```text
meets_project_requirements
accepts_terms
accepts_personal_data_processing
accepts_monitoring_obligations
```

Jeżeli uczestnik zaznaczy odpowiedź blokującą udział w projekcie, system nie powinien generować umowy.

Powód blokady powinien zostać zapisany w tabeli eBiura.

---

### 5. Podpisanie deklaracji

Deklaracja musi zostać podpisana elektronicznie.

Dopuszczalne typy podpisu:

```text
mSzafir
Profil Zaufany
```

Inne typy podpisu powinny być oznaczone jako niedopuszczalne.

Po wgraniu podpisanej deklaracji system zapisuje plik i uruchamia weryfikację podpisu.

---

### 6. Weryfikacja podpisu deklaracji

System sprawdza:

- czy dokument został podpisany,
- czy podpis ma poprawną strukturę techniczną,
- czy podpis należy do dopuszczalnego typu,
- czy podpisany dokument nie został zmieniony po podpisaniu,
- czy podpis dotyczy właściwego uczestnika,
- czy dane osoby podpisującej są zgodne z danymi z formularza.

Wynik weryfikacji powinien zostać zapisany w tabeli eBiura.

Przykładowe pola:

```text
declaration_signed
declaration_signature_type
declaration_signature_valid
declaration_signature_error
declaration_signed_filename
```

Statusy:

```text
DECLARATION_SIGNED
DECLARATION_SIGNATURE_INVALID
```

---

### 7. Decyzja o wygenerowaniu umowy

Po poprawnym podpisaniu deklaracji system sprawdza reguły kwalifikowalności.

Jeżeli warunki są spełnione, system generuje umowę.

Jeżeli warunki nie są spełnione, system:

- nie generuje umowy,
- oznacza umowę jako zablokowaną,
- zapisuje powód blokady,
- umożliwia wyświetlenie powodu blokady urzędnikowi w eBiurze,
- uruchamia możliwość wysłania wiadomości e-mail o niespełnieniu wymogów.

Przykładowe pola:

```text
agreement_required
agreement_blocked
agreement_block_reason
agreement_generated
agreement_filename
```

Statusy:

```text
AGREEMENT_BLOCKED
AGREEMENT_READY
AGREEMENT_WAITING_FOR_SIGNATURE
```

---

### 8. Podpisanie i weryfikacja umowy

Jeżeli umowa została wygenerowana, przechodzi analogiczny proces jak deklaracja.

System powinien rozróżniać:

- umowę wymaganą,
- umowę niewymaganą,
- umowę zablokowaną,
- umowę wygenerowaną,
- umowę podpisaną poprawnie,
- umowę podpisaną niepoprawnie.

Przykładowe pola:

```text
agreement_signed
agreement_signature_type
agreement_signature_valid
agreement_signature_error
agreement_signed_filename
```

Statusy:

```text
AGREEMENT_SIGNED
AGREEMENT_SIGNATURE_INVALID
```

Po poprawnym podpisaniu i zweryfikowaniu umowy uczestnik może zostać uznany za zapisanego do programu.

Status:

```text
PARTICIPANT_ACCEPTED
```

---

### 9. Wysyłka wiadomości e-mail

System powinien obsługiwać kilka typów wiadomości e-mail.

#### 9.1. Akceptacja przez urzędnika

Wysyłana po decyzji `TAK` wpisanej przez urzędnika.

Warunek:

```text
officer_decision = TAK
officer_decision_email_requested = TAK
officer_decision_email_sent != TAK
```

#### 9.2. Odrzucenie przez urzędnika

Wysyłana po decyzji `NIE` wpisanej przez urzędnika.

Warunek:

```text
officer_decision = NIE
officer_decision_email_requested = TAK
officer_decision_email_sent != TAK
```

#### 9.3. Poprawne wgranie umowy

Wysyłana po poprawnym wgraniu i zweryfikowaniu podpisanej umowy.

Warunek:

```text
agreement_signed = TAK
agreement_signature_valid = TAK
agreement_success_email_sent != TAK
```

#### 9.4. Odrzucenie z powodu niespełnienia wymogów

Wysyłana, gdy odpowiedzi uczestnika blokują wygenerowanie umowy.

Warunek:

```text
agreement_blocked = TAK
requirements_rejection_email_sent != TAK
```

---

## Statusy procesu

Docelowa lista statusów:

```text
FORM_SUBMITTED
WAITING_FOR_OFFICER_DECISION
OFFICER_ACCEPTED
OFFICER_REJECTED
DECLARATION_READY
DECLARATION_WAITING_FOR_SIGNATURE
DECLARATION_SIGNED
DECLARATION_SIGNATURE_INVALID
AGREEMENT_BLOCKED
AGREEMENT_READY
AGREEMENT_WAITING_FOR_SIGNATURE
AGREEMENT_SIGNED
AGREEMENT_SIGNATURE_INVALID
PARTICIPANT_ACCEPTED
PARTICIPANT_REJECTED
PROCESS_COMPLETED
```

---

## Minimalny zestaw pól w tabeli eBiura

```text
submission_id
form_slug
form_name
created_at
email
first_name
last_name
pesel
pdf_filename
process_status

officer_decision
officer_decision_reason
officer_decision_email_requested
officer_decision_email_sent

declaration_generated
declaration_filename
declaration_signed
declaration_signature_type
declaration_signature_valid
declaration_signature_error
declaration_signed_filename

agreement_required
agreement_blocked
agreement_block_reason
agreement_generated
agreement_filename
agreement_signed
agreement_signature_type
agreement_signature_valid
agreement_signature_error
agreement_signed_filename

agreement_success_email_sent
requirements_rejection_email_sent
```

---

## Kompatybilność z aktualnym projektem

Aktualny projekt używa pola:

```text
acceptance_required
```

Docelowo pole powinno zostać zastąpione przez:

```text
officer_decision
```

Na etapie migracji system powinien obsługiwać oba pola:

- `officer_decision` jako nowe pole docelowe,
- `acceptance_required` jako pole kompatybilności wstecznej.

Jeżeli `officer_decision` jest puste, system może odczytać decyzję z `acceptance_required`.

---

## Zakres kolejnego etapu

Kolejny etap powinien dodać techniczną warstwę procesu:

```text
services/process_service.py
```

Moduł powinien odpowiadać za:

- normalizację decyzji urzędnika,
- ustalanie statusu procesu,
- wykrywanie, czy można przejść do podpisywania deklaracji,
- wykrywanie, czy umowa powinna być wygenerowana,
- obsługę powodów blokady,
- zachowanie kompatybilności z aktualnym polem `acceptance_required`.
