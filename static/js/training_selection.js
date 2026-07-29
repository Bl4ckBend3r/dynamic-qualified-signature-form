document.addEventListener("DOMContentLoaded", () => {
    const picker = document.querySelector("[data-training-picker]");
    if (!picker) return;

    const inputs = [...picker.querySelectorAll("[data-training-price]")];
    const usedNode = picker.querySelector("[data-training-used]");
    const remainingNode = picker.querySelector("[data-training-remaining]");
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
        const used = inputs
            .filter(input => input.checked)
            .reduce((total, input) => total + Number(input.dataset.trainingPrice || 0), 0);
        const exceeded = limit !== null && used > limit;

        if (usedNode) usedNode.textContent = formatter.format(used);
        if (remainingNode && limit !== null) {
            remainingNode.textContent = formatter.format(Math.max(0, limit - used));
        }
        if (alertNode) {
            alertNode.hidden = !exceeded;
            alertNode.textContent = exceeded
                ? `Wybrane szkolenia przekraczają limit o ${formatter.format(used - limit)}. Odznacz szkolenie, aby zapisać wybór.`
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
