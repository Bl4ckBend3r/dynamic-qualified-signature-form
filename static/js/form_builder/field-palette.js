export function initializeFieldPalette(palette, {addField}) {
    palette?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-add-field]");
        if (button) addField(button.dataset.addField);
    });
    palette?.addEventListener("dragstart", (event) => {
        const button = event.target.closest("[data-add-field]");
        if (!button) return;
        event.dataTransfer.effectAllowed = "copy";
        event.dataTransfer.setData("text/plain", `new:${button.dataset.addField}`);
    });
}
