const TYPE_LABELS = {
    text: "Tekst", textarea: "Dłuższy tekst", email: "E-mail", tel: "Telefon",
    number: "Liczba", date: "Data", select: "Lista", radio: "Radio",
    checkbox: "Checkbox", pesel: "PESEL", file: "Plik",
};

export function createFieldItem(field, index, selected, actions) {
    const item = document.createElement("article");
    item.className = `form-builder__field${selected ? " is-selected" : ""}`;
    item.draggable = true;
    item.dataset.fieldKey = field._key;
    item.dataset.fieldIndex = String(index);
    item.dataset.fieldType = field.type;
    item.style.setProperty("--field-span", String(field.width_span || 12));
    item.tabIndex = 0;
    item.setAttribute("aria-label", `${field.label}, ${TYPE_LABELS[field.type] || field.type}`);

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
    item.append(head, previewControl(field));
    item.addEventListener("click", () => actions.select());
    item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); actions.select(); }
    });
    return item;
}

function fieldTools(actions) {
    const tools = document.createElement("div");
    tools.className = "form-builder__field-tools";
    const handle = toolButton("⠿", "Przeciągnij pole", "form-builder__drag-handle");
    const menuButton = toolButton("⋮", "Akcje pola");
    menuButton.setAttribute("aria-expanded", "false");
    const menu = document.createElement("div");
    menu.className = "form-builder__field-menu";
    menu.hidden = true;
    [
        ["Duplikuj", actions.duplicate], ["Przenieś wyżej", actions.moveUp],
        ["Przenieś niżej", actions.moveDown], ["Usuń", actions.remove],
    ].forEach(([text, callback]) => {
        const button = document.createElement("button");
        button.type = "button"; button.textContent = text;
        button.addEventListener("click", (event) => { event.stopPropagation(); menu.hidden = true; callback(); });
        menu.append(button);
    });
    menuButton.addEventListener("click", (event) => {
        event.stopPropagation(); menu.hidden = !menu.hidden;
        menuButton.setAttribute("aria-expanded", String(!menu.hidden));
    });
    tools.append(handle, menuButton, menu);
    return tools;
}

function toolButton(text, label, className = "") {
    const button = document.createElement("button");
    button.type = "button"; button.textContent = text; button.className = className;
    button.setAttribute("aria-label", label);
    button.addEventListener("click", (event) => event.stopPropagation());
    return button;
}

function previewControl(field) {
    if (["radio", "checkbox"].includes(field.type)) {
        const list = document.createElement("div");
        list.className = "form-builder__option-preview";
        const options = field.options?.length ? field.options : ["Opcja 1"];
        options.slice(0, 4).forEach((option) => {
            const span = document.createElement("span");
            span.textContent = typeof option === "object" ? option.label || option.value : option;
            list.append(span);
        });
        return list;
    }
    const control = field.type === "textarea" ? document.createElement("textarea")
        : field.type === "select" ? document.createElement("select") : document.createElement("input");
    control.className = "form-builder__control";
    control.disabled = true;
    if (field.type === "file") control.value = "Wybierz plik";
    else if (field.type === "select") {
        const option = document.createElement("option"); option.textContent = "— wybierz —"; control.append(option);
    } else control.placeholder = field.placeholder || "";
    return control;
}

function beginInlineLabelEdit(label, current, commit) {
    const input = document.createElement("input");
    input.className = "form-builder__inline-label"; input.value = current || "";
    const finish = () => commit(input.value.trim() || current);
    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") { event.preventDefault(); finish(); }
        if (event.key === "Escape") commit(current);
    });
    input.addEventListener("blur", finish, {once: true});
    label.replaceWith(input); input.focus(); input.select();
}
