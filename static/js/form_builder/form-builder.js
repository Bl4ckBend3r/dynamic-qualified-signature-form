import {initializeFieldPalette} from "./field-palette.js";
import {renderCanvas} from "./form-canvas.js";
import {initializePropertiesPanel} from "./properties-panel.js";
import {initializeDragAndDrop} from "./drag-and-drop.js";
import {readBuilderConfig, serializeFields, uniqueFieldName, withKey} from "./serialization.js";

const builder = document.querySelector("[data-form-builder]");
if (builder) {
    const config = readBuilderConfig(document);
    let fields = config.fields;
    let selectedKey = fields[0]?._key || null;
    const canvas = builder.querySelector("[data-form-canvas]");
    const panel = builder.querySelector("[data-properties-panel]");
    const stateInput = builder.querySelector("[data-builder-state]");
    const status = builder.querySelector("[data-builder-status]");

    const selected = () => fields.find((field) => field._key === selectedKey) || null;
    const markChanged = (message = "Niezapisane zmiany") => { status.textContent = message; };
    const updateSelected = (updates) => {
        if (!selectedKey) return;
        fields = fields.map((field) => field._key === selectedKey ? {...field, ...updates} : field);
        render(); markChanged();
    };
    const removeSelected = () => {
        if (!selectedKey) return;
        const index = fields.findIndex((field) => field._key === selectedKey);
        fields = fields.filter((field) => field._key !== selectedKey);
        selectedKey = fields[Math.min(index, fields.length - 1)]?._key || null;
        render(); markChanged("Pole zostanie usunięte po zapisaniu.");
    };
    const addField = (type, index = fields.length) => {
        const name = uniqueFieldName(fields, type);
        const label = type === "textarea" ? "Dłuższy tekst" : type === "select" ? "Wybierz opcję" : type === "file" ? "Załącz plik" : `Nowe pole ${name}`;
        const field = withKey({id: null, name, label, type, required: false, width: "full", width_span: 12, placeholder: "", section: "", document_label: "", options: ["Opcja 1", "Opcja 2"]});
        fields.splice(Math.max(0, Math.min(index, fields.length)), 0, field);
        fields = [...fields]; selectedKey = field._key; render(); markChanged("Dodano nowe pole. Zapisz formularz, aby utrwalić zmianę.");
    };
    const duplicate = (key = selectedKey) => {
        const index = fields.findIndex((field) => field._key === key); if (index < 0) return;
        const source = fields[index];
        const copy = withKey({...structuredClone(source), id: null, name: uniqueFieldName(fields, source.type, `${source.name}_kopia`), label: `${source.label} (kopia)`, _key: null});
        fields.splice(index + 1, 0, copy); fields = [...fields]; selectedKey = copy._key; render(); markChanged("Pole zostało zduplikowane.");
    };
    const move = (key, delta) => {
        const from = fields.findIndex((field) => field._key === key); const to = Math.max(0, Math.min(fields.length - 1, from + delta));
        if (from < 0 || from === to) return; const [field] = fields.splice(from, 1); fields.splice(to, 0, field); fields = [...fields]; render(); markChanged();
    };
    const moveTo = (key, targetIndex) => {
        const from = fields.findIndex((field) => field._key === key); if (from < 0) return;
        const [field] = fields.splice(from, 1); const adjusted = from < targetIndex ? targetIndex - 1 : targetIndex;
        fields.splice(Math.max(0, Math.min(adjusted, fields.length)), 0, field); fields = [...fields]; selectedKey = key; render(); markChanged();
    };
    const renderProperties = initializePropertiesPanel(panel, config, {update: updateSelected, remove: removeSelected, duplicate: () => duplicate(), close: () => { selectedKey = null; render(); }});
    const render = () => {
        renderCanvas(canvas, fields, selectedKey, {
            select: (key) => { selectedKey = key; render(); },
            update: (key, updates) => { selectedKey = key; updateSelected(updates); },
            duplicate, remove: (key) => { selectedKey = key; removeSelected(); }, move,
        });
        renderProperties(selected());
        builder.querySelector("[data-builder-empty]").classList.toggle("is-visible", fields.length === 0);
        builder.querySelector("[data-field-count]").textContent = `${fields.length} ${fields.length === 1 ? "pole" : "pól"}`;
        stateInput.value = JSON.stringify(serializeFields(fields));
    };
    initializeFieldPalette(builder.querySelector("[data-field-palette]"), {addField});
    initializeDragAndDrop(canvas, {moveField: moveTo, addField});
    builder.querySelectorAll("[data-builder-mode]").forEach((button) => button.addEventListener("click", () => {
        const preview = button.dataset.builderMode === "preview";
        builder.classList.toggle("is-preview", preview);
        builder.querySelectorAll("[data-builder-mode]").forEach((item) => item.classList.toggle("is-active", item === button));
    }));
    builder.addEventListener("submit", () => { stateInput.value = JSON.stringify(serializeFields(fields)); status.textContent = "Zapisywanie…"; });
    render();
}
