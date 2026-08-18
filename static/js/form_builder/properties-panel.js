const TYPE_LABELS = {
  text: "Tekst",
  textarea: "Dłuższy tekst",
  email: "E-mail",
  tel: "Telefon",
  number: "Liczba",
  date: "Data",
  time: "Czas",
  select: "Select",
  radio: "Radio",
  checkbox: "Checkbox",
  pesel: "PESEL",
  file: "Plik",
  repeatable_group: "Grupa powtarzalna",
};
const WIDTH_LABELS = {
  quarter: "25%",
  third: "33%",
  half: "50%",
  "two-thirds": "66%",
  "three-quarters": "75%",
  full: "100%",
};

export function initializePropertiesPanel(panel, config, callbacks) {
  const content = panel.querySelector("[data-properties-content]");
  const empty = panel.querySelector("[data-properties-empty]");
  const typeSelect = panel.querySelector('[data-property="type"]');
  config.types.forEach((type) => {
    const option = document.createElement("option");
    option.value = type;
    option.textContent = TYPE_LABELS[type] || type;
    typeSelect.append(option);
  });
  const widthHolder = panel.querySelector("[data-width-options]");
  Object.keys(config.widths).forEach((width) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.width = width;
    button.textContent = WIDTH_LABELS[width] || `${config.widths[width]}/12`;
    button.addEventListener("click", () =>
      callbacks.update({ width, width_span: config.widths[width] }),
    );
    widthHolder.append(button);
  });
  panel.querySelectorAll("[data-property]").forEach((control) => {
    const eventName =
      control.type === "checkbox" || control.tagName === "SELECT"
        ? "change"
        : "input";
    control.addEventListener(eventName, () => {
      let value = control.type === "checkbox" ? control.checked : control.value;
      if (["min_items", "max_items"].includes(control.dataset.property)) {
        value = Number.parseInt(control.value, 10);

        if (Number.isNaN(value)) {
          return;
        }
      }
      if (control.dataset.property === "options")
        value = value
          .split(/\r?\n/)
          .map((item) => item.trim())
          .filter(Boolean);
      callbacks.update({ [control.dataset.property]: value });
    });
  });
  panel
    .querySelector("[data-delete-field]")
    .addEventListener("click", callbacks.remove);
  panel
    .querySelector("[data-duplicate-field]")
    .addEventListener("click", callbacks.duplicate);
  panel
    .querySelector("[data-close-properties]")
    .addEventListener("click", callbacks.close);

  return (field) => {
    empty.hidden = Boolean(field);
    content.hidden = !field;
    if (!field) return;
    panel.querySelectorAll("[data-property]").forEach((control) => {
      const property = control.dataset.property;
      let value = field[property] ?? "";
      if (property === "options")
        value = (value || [])
          .map((option) =>
            typeof option === "object"
              ? `${option.value}|${option.label}`
              : option,
          )
          .join("\n");
      if (control.type === "checkbox") control.checked = Boolean(value);
      else control.value = value;
    });
    const nameControl = panel.querySelector('[data-property="name"]');
    nameControl.readOnly = Boolean(field.id);
    panel.querySelector("[data-name-help]").hidden = !field.id;
    widthHolder
      .querySelectorAll("button")
      .forEach((button) =>
        button.classList.toggle(
          "is-active",
          button.dataset.width === field.width,
        ),
      );
    panel.querySelector("[data-options-setting]").hidden = ![
      "select",
      "radio",
      "checkbox",
    ].includes(field.type);

    panel.querySelector("[data-placeholder-setting]").hidden = ![
      "text",
      "textarea",
      "email",
      "tel",
      "number",
      "pesel",
    ].includes(field.type);

    panel.querySelector("[data-repeatable-group-settings]").hidden =
      field.type !== "repeatable_group";
  };
}
