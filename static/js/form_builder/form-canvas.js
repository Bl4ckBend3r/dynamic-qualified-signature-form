import {createFieldItem} from "./field-item.js";

export function renderCanvas(canvas, fields, selectedKey, actions) {
    canvas.replaceChildren();
    let currentSection = null;
    fields.forEach((field, index) => {
        if (field.section && field.section !== currentSection) {
            const heading = document.createElement("h4");
            heading.className = "form-builder__section-title";
            heading.textContent = field.section;
            canvas.append(heading);
            currentSection = field.section;
        }
        canvas.append(createFieldItem(field, index, field._key === selectedKey, {
        select: () => actions.select(field._key),
        rename: (label) => actions.update(field._key, {label}),
        duplicate: () => actions.duplicate(field._key),
        remove: () => actions.remove(field._key),
        moveUp: () => actions.move(field._key, -1),
        moveDown: () => actions.move(field._key, 1),
        }));
    });
}
