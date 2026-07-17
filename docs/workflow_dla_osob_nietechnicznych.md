# Jak tworzyć workflow formularza

## Czym jest workflow?

Workflow to plan drogi, którą przechodzi zgłoszenie od wysłania formularza do zakończenia sprawy. Dzięki niemu użytkownik wie, co powinien zrobić, a urzędnik widzi tylko te czynności, które są potrzebne w danym momencie.

## Najważniejsze pojęcia

- **Etap** to część procesu, na przykład sprawdzenie wniosku, podpisanie deklaracji albo podpisanie umowy.
- **Status** opisuje aktualną sytuację, na przykład „Wniosek oczekuje na decyzję” lub „Umowa oczekuje na podpis”.
- **Akcja użytkownika** to czynność wykonywana przez osobę składającą wniosek, na przykład wgranie podpisanej umowy.
- **Decyzja urzędnika** to potwierdzenie, odrzucenie albo skierowanie dokumentu do poprawy.

Nazwy techniczne statusów są potrzebne aplikacji, ale użytkownik powinien zawsze widzieć prostą, polską nazwę.

## Jak zaplanować proces formularza

1. Zapisz na kartce, kto rozpoczyna proces i jaki ma być jego końcowy rezultat.
2. Wypisz dokumenty wymagane od użytkownika.
3. Zaznacz miejsca, w których potrzebna jest decyzja urzędnika.
4. Przy każdym etapie odpowiedz na pytanie: „Co użytkownik albo urzędnik ma zrobić teraz?”.
5. Usuń etapy, które nie mają własnej czynności lub decyzji.

## Jak zdefiniować etapy

W panelu administracyjnym otwórz formularz i przejdź do sekcji „Ustawienia procesu”. Zaznacz, czy proces wymaga deklaracji i umowy. Następnie w sekcji „Etapy workflow” ustaw etapy w kolejności, w której mają się pojawiać. Typowy proces składa się z czterech części: Wniosek, Deklaracja, Umowa i Zakończenie.

Jeśli formularz nie wymaga deklaracji albo umowy, nie dodawaj pustego etapu. Aplikacja powinna przejść bezpośrednio do kolejnej potrzebnej czynności.

## Jak przypisać statusy do etapów

Każdy status przypisz do jednego, najlepiej pasującego etapu. Przykładowo:

- „Wniosek złożony” i „Oczekuje na decyzję urzędnika” należą do etapu Wniosek.
- „Deklaracja oczekuje na podpis” i „Deklaracja podpisana” należą do etapu Deklaracja.
- „Umowa oczekuje na podpis”, „Umowa podpisana przez beneficjenta — do potwierdzenia” i „Podpisana umowa wymaga poprawy” należą do etapu Umowa.
- „Proces zakończony” należy do etapu Zakończenie.

Nie przypisuj tego samego statusu do kilku etapów, ponieważ użytkownik nie będzie wiedział, który krok jest aktualny.

## Prosty workflow krok po kroku

1. Wpisz nazwę procesu w sekcji „Ustawienia procesu”.
2. Kliknij „Dodaj etap” i utwórz etap „Wniosek złożony”.
3. Dodaj „Weryfikację przez urzędnika” i oznacz ją jako akcję urzędnika.
4. Dodaj potrzebne etapy dokumentów.
5. Dodaj etap „Proces zakończony” i oznacz go jako końcowy.
6. W każdej karcie wybierz kolejny etap.
7. Ustaw „Wniosek złożony” jako status początkowy.
8. Zapisz workflow. Jeśli brakuje ważnego elementu, panel pokaże po polsku, co trzeba poprawić.

Kolejność kart możesz zmieniać strzałkami. Usunięcie karty nie usuwa automatycznie odwołań do niej — przed zapisem wybierz poprawny kolejny etap w pozostałych kartach.

## Jak dodać deklarację

1. Włącz „Wymaga deklaracji”.
2. W sekcji dokumentów wklej lub wybierz szablon deklaracji.
3. Dodaj etap oczekiwania na podpis deklaracji.
4. Dodaj etap wgrania podpisanej deklaracji.
5. Jeśli urzędnik ma ją sprawdzać, skonfiguruj decyzję „Potwierdzenie podpisanej deklaracji”.

Bez etapu deklaracji i bez szablonu panel nie zapisze procesu wymagającego deklaracji.

## Jak dodać umowę

1. Włącz „Wymaga umowy”.
2. Dodaj szablon umowy w sekcji dokumentów.
3. Dodaj etap „Umowa oczekuje na podpis beneficjenta”.
4. Dodaj etap „Podpisana umowa wgrana przez beneficjenta”.
5. Jeżeli urzędnik ma sprawdzać podpis, włącz potwierdzenie i dodaj decyzję na etapie wgranej umowy.

Opcja „Jedna umowa na jedno szkolenie” oznacza, że osoba wybierająca trzy szkolenia otrzyma trzy osobne umowy. Każda z nich ma własny plik i numer. W aktualnym procesie szkoleniowym ta opcja jest zawsze włączona.

## Jak napisać instrukcję dla użytkownika

Instrukcja powinna odpowiadać na trzy pytania:

1. Co właśnie się wydarzyło?
2. Co użytkownik ma teraz zrobić?
3. Czy musi czekać na urzędnika?

Przykładowe komunikaty:

- „Wniosek został wysłany. Poczekaj na decyzję urzędnika.”
- „Deklaracja jest gotowa. Pobierz ją, podpisz i wgraj ponownie.”
- „Podpisana umowa została wgrana. Poczekaj na potwierdzenie przez urzędnika.”
- „Umowa wymaga poprawy. Przeczytaj uzasadnienie, popraw dokument i wgraj go ponownie.”
- „Proces został zakończony. Nie musisz wykonywać dalszych czynności.”

Opis etapu i pole „Co użytkownik ma zrobić dalej?” znajdziesz na każdej karcie etapu. Te treści automatycznie zasilają okno instrukcji po sprawdzeniu statusu zgłoszenia. W sekcji „Instrukcje dla użytkownika” możesz dodatkowo ustawić wspólny tytuł i krótki tekst wprowadzający.

## Kiedy wysyłać e-maile

E-mail warto wysłać wtedy, gdy użytkownik musi wykonać nową czynność albo otrzymał ważną decyzję. Szczególnie przydatne są wiadomości po:

- zaakceptowaniu albo odrzuceniu wniosku,
- przygotowaniu dokumentu do podpisu,
- odrzuceniu dokumentu i wskazaniu powodu,
- potwierdzeniu podpisanej umowy,
- zakończeniu procesu.

Treść e-maila powinna być zgodna z instrukcją widoczną po sprawdzeniu statusu. Brak adresu e-mail nie może zatrzymać decyzji urzędnika.

Aby włączyć wiadomość, przejdź do sekcji „Powiadomienia e-mail”, wybierz zdarzenie, zaznacz „Wyślij e-mail” i wskaż szablon. Zaznacz wysyłkę automatyczną tylko wtedy, gdy wiadomość nie wymaga sprawdzenia przez urzędnika. W pozostałych przypadkach wybierz ręczne potwierdzenie.

## Przykładowy workflow

1. Użytkownik wysyła formularz.
2. Urzędnik sprawdza wniosek.
3. Użytkownik podpisuje deklarację.
4. System generuje umowę.
5. Użytkownik podpisuje i wgrywa umowę.
6. Urzędnik potwierdza, że umowa została podpisana przez beneficjenta.
7. Proces zostaje zakończony.

Jeżeli urzędnik odrzuci umowę, proces wraca do punktu 5. Użytkownik otrzymuje powód i może wgrać poprawny dokument.

## Jak sprawdzić, czy proces działa

Przed udostępnieniem formularza wykonaj próbne zgłoszenie i przejdź cały proces jako użytkownik oraz jako urzędnik. Sprawdź:

- czy na każdym etapie widoczna jest tylko właściwa czynność,
- czy nie można zatwierdzić dokumentu przed jego wgraniem,
- czy negatywna decyzja wymaga uzasadnienia,
- czy po poprawie można ponownie wgrać dokument,
- czy historia zawiera decyzje, daty i osoby wykonujące czynności,
- czy linki w e-mailach działają,
- czy proces bez deklaracji lub umowy pomija niewymagany etap.

Najbezpieczniej utworzyć testowy formularz, wysłać próbne zgłoszenie i wykonać po kolei każdą decyzję. Przetestuj również ścieżkę odrzucenia i korekty. Po zmianie konfiguracji powtórz próbę od początku — rozpoczęte wcześniej zgłoszenie może znajdować się już na innym etapie.

## Czego unikać

- Nie zmieniaj pełnej konfiguracji JSON, jeśli wystarcza kreator.
- Nie usuwaj etapu, do którego prowadzi inna karta lub decyzja.
- Nie twórz dwóch etapów o tej samej roli i tej samej nazwie.
- Nie włączaj dokumentu bez dodania jego szablonu i etapów obsługi.
- Nie ustawiaj automatycznej wiadomości, jeśli jej treść wymaga ręcznego uzupełnienia.
- Nie używaj nazwy widocznej dla użytkownika jako reguły działania procesu. Działanie wynika z wybranego statusu.

## Dobre praktyki

- Nie twórz zbyt wielu etapów.
- Każdy etap powinien mieć jasną akcję.
- Komunikaty powinny mówić użytkownikowi, co ma zrobić dalej.
- Nie używaj technicznych nazw statusów w treściach widocznych dla użytkownika.
- Wymagaj uzasadnienia każdej negatywnej decyzji.
- Zakończ proces dopiero po wykonaniu wszystkich wymaganych czynności.
