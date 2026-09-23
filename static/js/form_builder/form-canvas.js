import { createFieldItem } from "./field-item.js";

export function renderCanvas(
    canvas,
    fields,
    selectedPath,
    actions,
) {
    canvas.replaceChildren();

    let currentSection = null;

    fields.forEach((field, index) => {
        if (
            field.section &&
            field.section !== currentSection
        ) {
            const heading =
                document.createElement("h4");

            heading.className =
                "form-builder__section-title";

            heading.textContent =
                field.section;

            canvas.append(heading);

            currentSection =
                field.section;
        }

        const selected =
            selectedPath?.groupKey === null &&
            selectedPath?.fieldKey ===
                field._key;

        const item = createFieldItem(
            field,
            index,
            selected,
            {
                /*
                 * Zwykłe pole.
                 */
                select: () =>
                    actions.select(
                        field._key,
                    ),

                rename: (label) =>
                    actions.update(
                        field._key,
                        { label },
                    ),

                duplicate: () =>
                    actions.duplicate(
                        field._key,
                    ),

                remove: () =>
                    actions.remove(
                        field._key,
                    ),

                moveUp: () =>
                    actions.move(
                        field._key,
                        -1,
                    ),

                moveDown: () =>
                    actions.move(
                        field._key,
                        1,
                    ),

                /*
                 * Obsługa pól wewnątrz
                 * repeatable_group.
                 */
                selectNested:
                    actions.selectNested,

                updateNested:
                    actions.updateNested,

                addNested:
                    actions.addNested,

                duplicateNested:
                    actions.duplicateNested,

                removeNested:
                    actions.removeNested,

                moveNested:
                    actions.moveNested,

                moveNestedTo:
                    actions.moveNestedTo,

                isNestedSelected:
                    actions.isNestedSelected,
            },
        );

        canvas.append(item);
    });
}