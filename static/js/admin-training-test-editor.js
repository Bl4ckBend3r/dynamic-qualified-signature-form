/* Administracyjny edytor testów PRE / POST */
(() => {
  "use strict";

  function init() {
    const form = document.querySelector("form[data-training-test-form]");

    if (!form || form.dataset.uxInitialized === "1") {
      return;
    }

    const list = form.querySelector("[data-training-question-list]");

    if (!list) return;

    form.dataset.uxInitialized = "1";
    form.classList.add("training-test-editor");

    const field = (card, name) => card.querySelector(`[name="${name}"]`);

    const rows = () => [
      ...list.querySelectorAll(":scope > [data-training-question]"),
    ];

    const initialPrototype = list
      .querySelector("[data-training-question]")
      ?.cloneNode(true);

    const summary = document.createElement("p");
    summary.className = "training-test-editor__summary";
    summary.setAttribute("aria-live", "polite");

    list.before(summary);

    /* Teksty pomocy pod polami */

    function help(label, message) {
      if (!label || label.querySelector(".training-test-editor__help")) {
        return;
      }

      const note = document.createElement("small");
      note.className = "training-test-editor__help";
      note.textContent = message;

      label.append(note);
    }

    /* Licznik pytań i punktów */

    function updateSummary() {
      const cards = rows();

      const points = cards.reduce((sum, card) => {
        if (field(card, "question_type")?.value === "text") {
          return sum;
        }

        return sum + (Number(field(card, "question_points")?.value) || 0);
      }, 0);

      const count = cards.length;

      if (count === 0) {
        summary.textContent =
          "Brak pytań — dodaj przynajmniej " +
          "jedno pytanie przed zapisaniem testu.";
      } else {
        const word = count === 1 ? "pytanie" : count < 5 ? "pytania" : "pytań";

        summary.textContent =
          `${count} ${word} · maksymalnie ` +
          `${points.toLocaleString("pl-PL")} pkt`;
      }

      cards.forEach((card, index) => {
        const number = card.querySelector("[data-question-number]");

        if (number && number.textContent !== String(index + 1)) {
          number.textContent = String(index + 1);
        }

        const remove = card.querySelector("[data-remove-question]");

        if (remove) {
          if (remove.disabled) {
            remove.disabled = false;
          }

          remove.type = "button";

          if (remove.textContent !== "Usuń pytanie") {
            remove.textContent = "Usuń pytanie";
          }

          const label = `Usuń pytanie ${index + 1}`;

          if (remove.getAttribute("aria-label") !== label) {
            remove.setAttribute("aria-label", label);
          }
        }
      });
    }

    /* Synchronizacja odpowiedzi z polami backendu */

    function syncChoiceFields(card) {
      const editor = card.querySelector("[data-choice-editor]");

      if (!editor) return;

      const type = field(card, "question_type")?.value;

      if (type !== "single_choice" && type !== "multiple_choice") {
        return;
      }

      const items = [...editor.querySelectorAll("[data-choice-row]")];

      const options = items
        .map((item) => item.querySelector("[data-choice-text]").value.trim())
        .filter(Boolean);

      const correct = items
        .filter((item) => item.querySelector("[data-choice-correct]").checked)
        .map((item) => item.querySelector("[data-choice-text]").value.trim())
        .filter(Boolean);

      field(card, "question_options").value = options.join("\n");

      field(card, "question_correct_answers").value = correct.join("\n");
    }

    /* Dodawanie pojedynczej odpowiedzi */

    function addChoice(card, value = "", correct = false) {
      const editor = card.querySelector("[data-choice-editor]");

      if (!editor) return;

      const item = document.createElement("div");
      item.className = "training-test-editor__choice";
      item.dataset.choiceRow = "";

      const isCorrect = document.createElement("input");
      isCorrect.type = "checkbox";
      isCorrect.dataset.choiceCorrect = "";
      isCorrect.checked = correct;
      isCorrect.setAttribute("aria-label", "Poprawna odpowiedź");

      const content = document.createElement("input");
      content.type = "text";
      content.dataset.choiceText = "";
      content.value = value;
      content.placeholder = "Wpisz odpowiedź";
      content.setAttribute("aria-label", "Treść odpowiedzi");

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "admin-button-secondary compact";
      remove.textContent = "Usuń opcję";
      remove.setAttribute("aria-label", "Usuń tę opcję odpowiedzi");

      item.append(isCorrect, content, remove);

      editor.querySelector("[data-choice-items]").append(item);

      isCorrect.addEventListener("change", () => {
        const type = field(card, "question_type").value;

        if (isCorrect.checked && type === "single_choice") {
          editor
            .querySelectorAll("[data-choice-correct]")
            .forEach((control) => {
              if (control !== isCorrect) {
                control.checked = false;
              }
            });
        }

        syncChoiceFields(card);
      });

      content.addEventListener("input", () => syncChoiceFields(card));

      remove.addEventListener("click", () => {
        const items = editor.querySelectorAll("[data-choice-row]");

        if (items.length <= 2) {
          content.focus();
          return;
        }

        item.remove();
        syncChoiceFields(card);
      });

      syncChoiceFields(card);
    }

    /* Wyświetlanie pól zależnie od typu pytania */

    function updateType(card) {
      const type = field(card, "question_type")?.value;

      const options = field(card, "question_options");

      const answers = field(card, "question_correct_answers");

      const editor = card.querySelector("[data-choice-editor]");

      const booleanEditor = card.querySelector("[data-boolean-answer]");

      if (!options || !answers || !editor || !booleanEditor) {
        return;
      }

      const isChoice = type === "single_choice" || type === "multiple_choice";

      /* Ukryj techniczne pola tekstowe */

      options.closest("label").hidden = true;
      answers.closest("label").hidden = true;

      editor.hidden = !isChoice;
      booleanEditor.hidden = type !== "true_false";

      if (type === "true_false") {
        answers.value = booleanEditor.querySelector("select").value;
      }

      if (isChoice && type === "single_choice") {
        const marked = [
          ...editor.querySelectorAll("[data-choice-correct]:checked"),
        ];

        marked.slice(1).forEach((control) => {
          control.checked = false;
        });

        syncChoiceFields(card);
      }

      const points = field(card, "question_points");

      if (points) {
        if (type === "text") {
          if (!points.readOnly) {
            points.dataset.previousPoints = points.value;
          }

          points.value = "0";
          points.readOnly = true;
        } else if (points.readOnly) {
          points.value = points.dataset.previousPoints || "1";

          points.readOnly = false;
        }
      }

      updateSummary();
    }

    /* Ulepszenie istniejącej karty pytania */

    function enhanceCard(card) {
      if (card.dataset.uxEnhanced === "1") {
        return;
      }

      card.dataset.uxEnhanced = "1";

      const type = field(card, "question_type");
      const options = field(card, "question_options");
      const answers = field(card, "question_correct_answers");
      const points = field(card, "question_points");
      const key = field(card, "question_comparison_key");

      if (!type || !options || !answers) {
        return;
      }

      help(
        type.closest("label"),
        "Wybierz sposób udzielania " + "odpowiedzi przez uczestnika.",
      );

      help(
        points?.closest("label"),
        "Liczba punktów za poprawną odpowiedź. " +
          "Pytanie tekstowe jest niepunktowane.",
      );

      /* Objaśnienie klucza porównawczego */

      if (key) {
        const label = key.closest("label");
        const caption = label?.querySelector("span");

        if (caption) {
          caption.textContent = "Kod porównawczy PRE / POST (opcjonalnie)";
        }

        help(
          label,
          "Ten sam kod w podobnych pytaniach " +
            "PRE i POST pozwala je powiązać " +
            "w analizie, np. bezpieczenstwo_01. " +
            "W pozostałych przypadkach pozostaw puste.",
        );

        if (!label.closest("details")) {
          const advanced = document.createElement("details");

          advanced.className = "training-test-editor__advanced";

          const heading = document.createElement("summary");

          heading.textContent =
            "Ustawienia zaawansowane — " + "porównanie PRE / POST";

          label.before(advanced);
          advanced.append(heading, label);
        }
      }

      /* Odczyt wcześniej zapisanych odpowiedzi */

      const oldOptions = options.value
        .split(/\r?\n/)
        .map((text) => text.trim())
        .filter(Boolean);

      const oldAnswers = new Set(
        answers.value
          .split(/\r?\n/)
          .map((text) => text.trim())
          .filter(Boolean),
      );

      /* Graficzny edytor odpowiedzi */

      const editor = document.createElement("div");
      editor.className =
        "training-test-editor__choices " + "training-field-wide";

      editor.dataset.choiceEditor = "";

      editor.innerHTML = `
                <p class="training-test-editor__choice-heading">
                    Odpowiedzi
                    <span>— zaznacz poprawną</span>
                </p>
                <div data-choice-items></div>
                <button
                    class="admin-button-secondary compact"
                    type="button"
                    data-add-choice
                >
                    + Dodaj odpowiedź
                </button>
            `;

      options.closest("label").before(editor);

      editor
        .querySelector("[data-add-choice]")
        .addEventListener("click", () => addChoice(card));

      /* Odpowiedź dla Prawda / Fałsz */

      const booleanEditor = document.createElement("label");

      booleanEditor.className =
        "training-test-editor__boolean " + "training-field-wide";

      booleanEditor.dataset.booleanAnswer = "";

      booleanEditor.innerHTML = `
                <span>Poprawna odpowiedź</span>
                <select>
                    <option value="true">Prawda</option>
                    <option value="false">Fałsz</option>
                </select>
            `;

      editor.after(booleanEditor);

      const booleanSelect = booleanEditor.querySelector("select");

      booleanSelect.value =
        answers.value.trim().toLowerCase() === "false" ? "false" : "true";

      booleanSelect.addEventListener("change", () => {
        answers.value = booleanSelect.value;
      });

      const choices = oldOptions.length ? oldOptions : ["", ""];

      choices.forEach((value) => {
        addChoice(card, value, oldAnswers.has(value));
      });

      type.addEventListener("change", () => updateType(card));

      points?.addEventListener("input", updateSummary);

      updateType(card);
    }

    /* Przygotowanie nowego, pustego pytania */

    function resetCard(card) {
      card.dataset.uxEnhanced = "";

      card
        .querySelectorAll(
          "[data-choice-editor], " +
            "[data-boolean-answer], " +
            ".training-test-editor__advanced",
        )
        .forEach((element) => {
          if (element.matches(".training-test-editor__advanced")) {
            const label = element.querySelector("label");

            if (label) {
              element.before(label);
            }
          }

          element.remove();
        });

      card
        .querySelectorAll(".training-test-editor__help")
        .forEach((element) => element.remove());

      card.querySelectorAll("input, textarea, select").forEach((control) => {
        if (control.type === "hidden") {
          control.value = "";
        } else if (control.type === "checkbox" || control.type === "radio") {
          control.checked = false;
        } else if (control.tagName === "SELECT") {
          control.selectedIndex = 0;
        } else if (control.name === "question_points") {
          control.readOnly = false;
          control.value = "1";
        } else {
          control.value = "";
        }
      });

      enhanceCard(card);
    }

    /* Dodawanie nowego pytania */

    function addQuestion() {
      const template = document.querySelector(
        "template[data-training-question-template]",
      );

      let card = template?.content
        .querySelector("[data-training-question]")
        ?.cloneNode(true);

      if (!card && rows().length) {
        card = rows()[0].cloneNode(true);
      }

      if (!card && initialPrototype) {
        card = initialPrototype.cloneNode(true);
      }

      if (!card) return;

      resetCard(card);
      list.append(card);

      updateSummary();

      field(card, "question_text")?.focus();
    }

    /* Dodawanie i usuwanie pytań */

    form.addEventListener(
      "click",
      (event) => {
        const add = event.target.closest("[data-add-question]");

        const remove = event.target.closest("[data-remove-question]");

        if (!add && !remove) {
          return;
        }

        event.preventDefault();
        event.stopImmediatePropagation();

        if (add) {
          addQuestion();
          return;
        }

        const card = remove.closest("[data-training-question]");

        if (!card) return;

        const confirmed = window.confirm(
          "Usunąć to pytanie? " +
            "Zmiana zostanie zapisana po zapisaniu " +
            "całego testu.",
        );

        if (!confirmed) return;

        card.remove();
        updateSummary();
      },
      true,
    );

    /* Sprawdzenie danych przed zapisaniem */

    form.addEventListener(
      "submit",
      (event) => {
        if (!rows().length) {
          event.preventDefault();

          window.alert(
            "Przed zapisaniem testu dodaj " + "przynajmniej jedno pytanie.",
          );

          form.querySelector("[data-add-question]")?.focus();

          return;
        }

        for (const card of rows()) {
          const type = field(card, "question_type")?.value;

          if (type !== "single_choice" && type !== "multiple_choice") {
            continue;
          }

          syncChoiceFields(card);

          const editor = card.querySelector("[data-choice-editor]");

          const values = [...editor.querySelectorAll("[data-choice-text]")]
            .map((input) => input.value.trim())
            .filter(Boolean);

          const chosen = [
            ...editor.querySelectorAll("[data-choice-row]"),
          ].filter((row) => {
            return (
              row.querySelector("[data-choice-correct]").checked &&
              row.querySelector("[data-choice-text]").value.trim()
            );
          });

          let issue = null;

          if (values.length < 2) {
            issue = "Dodaj przynajmniej dwie odpowiedzi.";
          } else if (new Set(values).size !== values.length) {
            issue = "Odpowiedzi w jednym pytaniu " + "nie mogą się powtarzać.";
          } else if (!chosen.length) {
            issue = "Zaznacz przynajmniej jedną " + "poprawną odpowiedź.";
          }

          if (issue) {
            event.preventDefault();
            window.alert(issue);

            card.querySelector("[data-choice-text]")?.focus();

            return;
          }
        }
      },
      true,
    );

    /* Objaśnienie polityki podejść */

    help(
      form.querySelector('[name="attempt_policy"]')?.closest("label"),
      "Jedno podejście: uczestnik może rozwiązać " +
        "test raz. Wiele podejść: może ponawiać test.",
    );

    rows().forEach(enhanceCard);
    updateSummary();

    /* Zgodność ze starszą obsługą formularza */

    const observer = new MutationObserver((records) => {
      if (records.some((record) => record.type === "childList")) {
        rows().forEach(enhanceCard);
      }

      updateSummary();
    });

    observer.observe(list, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["disabled"],
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
