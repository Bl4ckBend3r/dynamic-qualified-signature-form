# Instrukcja przygotowywania deklaracji i umów

## Cel

Dokument opisuje jednolity sposób przygotowywania deklaracji, oświadczeń, formularzy i umów w aplikacji. Instrukcja jest przeznaczona dla użytkowników nietechnicznych.

## Zasady ogólne

1. Jeden dokument powinien zawierać kompletną treść: tytuł, opis, sekcje, pola, oświadczenia, podpisy i przypisy.
2. Należy stosować krótkie, jednoznaczne zdania.
3. Nie należy formatować dokumentu spacjami, tabulatorami ani wieloma pustymi liniami.
4. Wygląd dokumentu powinien wynikać wyłącznie z klas CSS.
5. Wszystkie dokumenty powinny korzystać z tych samych klas i znaczników.

## Dozwolone znaczniki HTML

- `article` — cały dokument,
- `header` — nagłówek dokumentu,
- `section` — sekcja dokumentu,
- `h1` — tytuł dokumentu,
- `h2` — tytuł sekcji,
- `h3` — tytuł podsekcji,
- `p` — akapit,
- `div` — blok układu,
- `label` — etykieta pola,
- `span` — krótki element tekstowy,
- `ul`, `ol`, `li` — listy,
- `table`, `thead`, `tbody`, `tr`, `th`, `td` — tabele.

## Niedozwolone znaczniki HTML

Nie należy używać: `font`, `center`, `b`, `i`, `u`, `br`.

## Klasy główne

- `document` — główna klasa każdego dokumentu,
- `document--declaration` — deklaracja,
- `document--agreement` — umowa,
- `document--form` — formularz,
- `document-header` — nagłówek dokumentu,
- `document-eyebrow` — krótka etykieta nad tytułem,
- `document-title` — główny tytuł,
- `document-subtitle` — opis pod tytułem,
- `document-meta` — dane dodatkowe dokumentu.

## Klasy sekcji

- `document-section` — standardowa sekcja,
- `document-section--compact` — sekcja z mniejszym odstępem,
- `document-section--page-break` — sekcja od nowej strony w PDF,
- `section-title` — tytuł sekcji,
- `section-subtitle` — opis sekcji,
- `section-note` — uwaga w sekcji.

## Klasy pól

- `field-grid` — siatka pól,
- `field-grid--two` — dwie kolumny,
- `field-grid--three` — trzy kolumny,
- `field` — pojedyncze pole,
- `field--full` — pole na pełną szerokość,
- `field-label` — nazwa pola,
- `field-value` — wartość pola,
- `field-help` — opis pomocniczy.

## Klasy checkboxów

- `choice-group` — grupa odpowiedzi,
- `choice-group--inline` — odpowiedzi w jednej linii,
- `choice-item` — pojedyncza odpowiedź,
- `checkbox` — pole wyboru,
- `checkbox--checked` — zaznaczone pole wyboru,
- `choice-label` — treść odpowiedzi.

## Klasy oświadczeń

- `statement-list` — lista oświadczeń,
- `statement-item` — pojedyncze oświadczenie,
- `statement-number` — numer oświadczenia,
- `statement-text` — treść oświadczenia,
- `statement-note` — uwaga do oświadczenia.

## Klasy podpisów

- `signature-area` — obszar podpisów,
- `signature-grid` — układ podpisów,
- `signature-block` — pojedynczy podpis,
- `signature-line` — linia podpisu,
- `signature-space` — kreska na podpis,
- `signature-label` — opis podpisu,
- `signature-date` — data i miejscowość.

## Klasy tabel i przypisów

- `document-table` — tabela,
- `document-table--compact` — tabela kompaktowa,
- `document-table--bordered` — tabela z obramowaniem,
- `table-note` — opis tabeli,
- `footnotes` — sekcja przypisów,
- `footnote` — pojedynczy przypis,
- `legal-note` — informacja prawna,
- `small-note` — krótka uwaga.

## Minimalna struktura dokumentu

```html
<article class="document document--declaration">
    <header class="document-header">
        <p class="document-eyebrow">Deklaracja</p>
        <h1 class="document-title">Tytuł dokumentu</h1>
        <p class="document-subtitle">Krótki opis dokumentu.</p>
    </header>

    <section class="document-section">
        <h2 class="section-title">Nazwa sekcji</h2>
        <div class="field-grid field-grid--two">
            <div class="field">
                <label class="field-label">Nazwa pola</label>
                <div class="field-value">[WARTOŚĆ]</div>
            </div>
        </div>
    </section>
</article>
```

## Checklist przed publikacją

1. Dokument ma jeden `document-title`.
2. Każda część dokumentu jest w `document-section`.
3. Wszystkie pola mają `field`, `field-label`, `field-value`.
4. Checkboxy mają `choice-group`, `choice-item`, `checkbox`, `choice-label`.
5. Oświadczenia mają `statement-list` i `statement-item`.
6. Podpisy mają `signature-area`, `signature-grid`, `signature-block`, `signature-space`, `signature-label`.
7. Nie ma ręcznego wyrównywania spacjami.
8. Nie ma znaczników `font`, `center`, `b`, `i`, `u`, `br`.
9. Dokument został sprawdzony w PDF.
10. Wszystkie wartości `[ ... ]` zostały zastąpione danymi.