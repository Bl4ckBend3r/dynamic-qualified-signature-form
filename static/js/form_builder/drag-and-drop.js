export function initializeDragAndDrop(canvas, {moveField, addField}) {
    let marker = null;
    const clearMarker = () => {
        marker?.element.classList.remove("is-drop-before", "is-drop-after");
        marker = null;
    };
    canvas.addEventListener("dragstart", (event) => {
        const item = event.target.closest("[data-field-key]");
        if (!item) return;
        item.classList.add("is-dragging");
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", `field:${item.dataset.fieldKey}`);
    });
    canvas.addEventListener("dragend", (event) => {
        event.target.closest("[data-field-key]")?.classList.remove("is-dragging");
        clearMarker();
    });
    canvas.addEventListener("dragover", (event) => {
        event.preventDefault();
        clearMarker();
        const item = event.target.closest("[data-field-key]");
        if (!item) return;
        const rect = item.getBoundingClientRect();
        const after = event.clientY > rect.top + rect.height / 2;
        item.classList.add(after ? "is-drop-after" : "is-drop-before");
        marker = {element: item, index: Number(item.dataset.fieldIndex) + (after ? 1 : 0)};
    });
    canvas.addEventListener("dragleave", (event) => {
        if (!canvas.contains(event.relatedTarget)) clearMarker();
    });
    canvas.addEventListener("drop", (event) => {
        event.preventDefault();
        const payload = event.dataTransfer.getData("text/plain");
        const index = marker?.index ?? canvas.querySelectorAll("[data-field-key]").length;
        clearMarker();
        if (payload.startsWith("new:")) addField(payload.slice(4), index);
        else if (payload.startsWith("field:")) moveField(payload.slice(6), index);
    });
}
