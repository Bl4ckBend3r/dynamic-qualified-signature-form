document.addEventListener("DOMContentLoaded", () => {
    const picker = document.querySelector("[data-training-picker]");
    if (!picker) return;

    const inputs = [...picker.querySelectorAll("[data-training-price]")];
    const usedNode = picker.querySelector("[data-training-used]");
    const pendingNode = picker.querySelector("[data-training-pending]");
    const remainingNode = picker.querySelector("[data-training-remaining]");
    const remainingSelectionNode = picker.querySelector("[data-training-remaining-selection]");
    const alertNode = picker.querySelector("[data-training-limit-alert]");
    const submitButton = picker.querySelector("[data-training-submit]");
    const currency = picker.dataset.trainingCurrency || "PLN";
    const rawLimit = picker.dataset.trainingLimit;
    const limit = rawLimit === "" ? null : Number(rawLimit);
    const formatter = new Intl.NumberFormat("pl-PL", {
        style: "currency",
        currency,
        useGrouping: "always",
    });

    const update = () => {
        const selected = inputs
            .filter(input => input.checked)
            .reduce((total, input) => total + Number(input.dataset.trainingPrice || 0), 0);
        const locked = Number(picker.dataset.trainingLockedTotal || 0);
        const pending = Math.max(0, selected - locked);
        const exceeded = limit !== null && selected > limit;

        if (usedNode) usedNode.textContent = formatter.format(locked);
        if (pendingNode) pendingNode.textContent = formatter.format(pending);
        if (remainingNode && limit !== null) {
            remainingNode.textContent = formatter.format(Math.max(0, limit - locked));
        }
        if (remainingSelectionNode && limit !== null) {
            remainingSelectionNode.textContent = formatter.format(Math.max(0, limit - selected));
        }
        if (alertNode) {
            alertNode.hidden = !exceeded;
            alertNode.textContent = exceeded
                ? `Wybrane szkolenia przekraczają limit o ${formatter.format(selected - limit)}. Odznacz szkolenie, aby zapisać wybór.`
                : "";
        }
        if (submitButton) {
            submitButton.disabled = exceeded;
            submitButton.setAttribute("aria-disabled", exceeded ? "true" : "false");
        }

        inputs.forEach(input => {
            const card = input.closest("[data-training-card]");
            if (!card) return;
            card.classList.toggle("is-selected", input.checked);
            const status = card.querySelector("[data-training-status]");
            if (status && input.dataset.trainingLocked !== "true") {
                status.textContent = input.checked
                    ? input.dataset.trainingDefaultStatus === "Dostępne"
                        ? "Wybrane"
                        : input.dataset.trainingDefaultStatus || "Wybrane"
                    : "Dostępne";
            }
        });
    };

    inputs.forEach(input => input.addEventListener("change", update));
    picker.addEventListener("submit", event => {
        if (limit === null) return;
        const used = inputs
            .filter(input => input.checked)
            .reduce((total, input) => total + Number(input.dataset.trainingPrice || 0), 0);
        if (used > limit) {
            event.preventDefault();
            update();
            alertNode?.focus();
        }
    });
    update();
});
