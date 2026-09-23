const TYPE_LABELS = {
  text: "Tekst",
  textarea: "Dłuższy tekst",
  email: "E-mail",
  tel: "Telefon",
  number: "Liczba",
  date: "Data",
  time: "Czas",
  select: "Lista",
  radio: "Radio",
  checkbox: "Checkbox",
  pesel: "PESEL",
  file: "Plik",
  repeatable_group: "Grupa powtarzalna",
};

export function createFieldItem(field, index, selected, actions) {
  const item = document.createElement("article");

  item.className = `form-builder__field${selected ? " is-selected" : ""}`;

  item.draggable = true;

  item.dataset.fieldKey = field._key;
  item.dataset.fieldIndex = String(index);
  item.dataset.fieldType = field.type;
  if (field.type === "repeatable_group") {
    item.dataset.repeatableGroup = field._key;
  }

  item.style.setProperty("--field-span", String(field.width_span || 12));

  item.tabIndex = 0;

  item.setAttribute(
    "aria-label",
    `${field.label || field.name}, ${TYPE_LABELS[field.type] || field.type}`,
  );

  const head = document.createElement("div");
  head.className = "form-builder__field-head";

  const label = document.createElement("span");
  label.className = "form-builder__field-label";
  label.textContent = field.label || field.name;

  if (field.required) {
    const marker = document.createElement("span");

    marker.className = "form-builder__field-required";

    marker.textContent = " *";

    label.append(marker);
  }

  label.title = "Kliknij dwukrotnie, aby szybko zmienić etykietę";

  label.addEventListener("dblclick", (event) => {
    event.stopPropagation();

    beginInlineLabelEdit(label, field.label, actions.rename);
  });

  const tools = fieldTools(actions);

  head.append(label, tools);

  item.append(head, previewControl(field, actions));

  /*
   * stopPropagation jest ważne dla pól
   * znajdujących się wewnątrz repeatable_group.
   *
   * Bez tego kliknięcie dziecka zaznaczyłoby
   * najpierw dziecko, a potem kontener grupy.
   */
  item.addEventListener("click", (event) => {
    event.stopPropagation();
    actions.select();
  });

  item.addEventListener("keydown", (event) => {
    if (event.target !== item) {
      return;
    }

    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      event.stopPropagation();

      actions.select();
    }
  });

  return item;
}

function fieldTools(actions) {
  const tools = document.createElement("div");

  tools.className = "form-builder__field-tools";

  const handle = toolButton(
    "☰",
    "Przeciągnij pole",
    "form-builder__drag-handle",
  );

  const menuButton = toolButton("⋮", "Akcje pola");

  menuButton.setAttribute("aria-expanded", "false");

  const menu = document.createElement("div");

  menu.className = "form-builder__field-menu";

  menu.hidden = true;

  [
    ["Duplikuj", actions.duplicate],
    ["Przenieś wyżej", actions.moveUp],
    ["Przenieś niżej", actions.moveDown],
    ["Usuń", actions.remove],
  ].forEach(([text, callback]) => {
    const button = document.createElement("button");

    button.type = "button";
    button.textContent = text;

    button.addEventListener("click", (event) => {
      event.stopPropagation();
      menu.hidden = true;

      callback();
    });

    menu.append(button);
  });

  menuButton.addEventListener("click", (event) => {
    event.stopPropagation();

    menu.hidden = !menu.hidden;

    menuButton.setAttribute("aria-expanded", String(!menu.hidden));
  });

  tools.append(handle, menuButton, menu);

  return tools;
}

function toolButton(text, label, className = "") {
  const button = document.createElement("button");

  button.type = "button";
  button.textContent = text;
  button.className = className;

  button.setAttribute("aria-label", label);

  button.addEventListener("click", (event) => event.stopPropagation());

  return button;
}

function previewControl(field, actions) {
  /*
   * repeatable_group jest kontenerem
   * dla normalnych createFieldItem().
   */
  if (field.type === "repeatable_group") {
    return repeatableGroupEditor(field, actions);
  }

  if (["radio", "checkbox"].includes(field.type)) {
    const list = document.createElement("div");

    list.className = "form-builder__option-preview";

    const options = field.options?.length ? field.options : ["Opcja 1"];

    options.slice(0, 4).forEach((option) => {
      const span = document.createElement("span");

      span.textContent =
        typeof option === "object" ? option.label || option.value : option;

      list.append(span);
    });

    return list;
  }

  const control =
    field.type === "textarea"
      ? document.createElement("textarea")
      : field.type === "select"
        ? document.createElement("select")
        : document.createElement("input");

  control.className = "form-builder__control";

  control.disabled = true;

  if (field.type === "time") {
    control.type = "time";
  }

  if (field.type === "date") {
    control.type = "date";
  }

  if (field.type === "number") {
    control.type = "number";
  }

  if (field.type === "email") {
    control.type = "email";
  }

  if (field.type === "tel") {
    control.type = "tel";
  }

  if (field.type === "file") {
    /*
     * Nie ustawiamy input[type=file],
     * ponieważ przeglądarka nie pozwala
     * ustawiać jego value.
     */
    control.type = "text";
    control.value = "Wybierz plik";
  } else if (field.type === "select") {
    const option = document.createElement("option");

    option.textContent = "— wybierz —";

    control.append(option);
  } else {
    control.placeholder = field.placeholder || "";
  }

  return control;
}

/*
 * Kontener repeatable_group.
 *
 * Każde dziecko jest renderowane przez
 * dokładnie ten sam createFieldItem().
 */
function repeatableGroupEditor(group, actions) {
  const container = document.createElement("div");

  container.className = "form-builder__nested-canvas";

  container.dataset.repeatableGroup = group._key;

  /*
   * Kliknięcie pustego miejsca w grupie
   * nie powinno propagować się dalej.
   */
  container.addEventListener("click", (event) => {
    event.stopPropagation();
  });

  const summary = document.createElement("div");

  summary.className = "form-builder__repeatable-summary";

  summary.textContent =
    `${group.item_label || "Element"} · ` +
    `${group.min_items ?? 1}–` +
    `${group.max_items ?? 20}`;

  container.append(summary);

  const nestedFields = Array.isArray(group.fields) ? group.fields : [];

  const fieldsContainer = document.createElement("div");

  fieldsContainer.className = "form-builder__nested-fields";

  fieldsContainer.dataset.nestedFields = group._key;

  if (!nestedFields.length) {
    const empty = document.createElement("div");

    empty.className = "form-builder__nested-empty";

    empty.textContent = "Przeciągnij tutaj pole.";

    fieldsContainer.append(empty);
  }

  nestedFields.forEach((child, index) => {
    const selected =
      actions.isNestedSelected?.(group._key, child._key) || false;

    const childItem = createFieldItem(child, index, selected, {
      /*
       * Kliknięcie dziecka.
       */
      select: () => actions.selectNested(group._key, child._key),

      /*
       * Dwukrotne kliknięcie
       * etykiety dziecka.
       */
      rename: (label) =>
        actions.updateNested(group._key, child._key, { label }),

      /*
       * Te same akcje menu,
       * co dla zwykłego pola.
       */
      duplicate: () => actions.duplicateNested(group._key, child._key),

      remove: () => actions.removeNested(group._key, child._key),

      moveUp: () => actions.moveNested(group._key, child._key, -1),

      moveDown: () => actions.moveNested(group._key, child._key, 1),
    });

    /*
     * Informacja dla przyszłego
     * drag-and-drop.js.
     */
    childItem.dataset.parentGroupKey = group._key;

    childItem.dataset.nestedField = "true";

    fieldsContainer.append(childItem);
  });

  container.append(fieldsContainer);

  return container;
}

function beginInlineLabelEdit(label, current, commit) {
  const input = document.createElement("input");

  input.className = "form-builder__inline-label";

  input.value = current || "";

  let finished = false;

  const finish = () => {
    if (finished) {
      return;
    }

    finished = true;

    commit(input.value.trim() || current);
  };

  input.addEventListener("keydown", (event) => {
    event.stopPropagation();

    if (event.key === "Enter") {
      event.preventDefault();
      finish();
    }

    if (event.key === "Escape") {
      finished = true;
      commit(current);
    }
  });

  input.addEventListener("blur", finish, { once: true });

  label.replaceWith(input);

  input.focus();
  input.select();
}
