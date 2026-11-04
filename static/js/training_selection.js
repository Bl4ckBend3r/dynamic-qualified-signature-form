document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-training-picker], [data-document-trainings]").forEach(scope => {
        const groupedInputs = [...scope.querySelectorAll('input[type="checkbox"][data-selection-group]')];
        if (!groupedInputs.length) return;

        groupedInputs.forEach(input => {
            if (!input.dataset.trainingBaseDisabled) {
                input.dataset.trainingBaseDisabled = input.disabled ? "true" : "false";
            }
        });

        const updateSelectionGroups = () => {
            const selectedGroups = new Set(
                groupedInputs
                    .filter(input => input.checked && input.dataset.selectionGroup.trim())
                    .map(input => input.dataset.selectionGroup.trim().toLocaleLowerCase("pl-PL"))
            );

            groupedInputs.forEach(input => {
                const group = input.dataset.selectionGroup.trim().toLocaleLowerCase("pl-PL");
                const blockedByGroup = Boolean(group) && !input.checked && selectedGroups.has(group);
                input.disabled = input.dataset.trainingBaseDisabled === "true" || blockedByGroup;
            });
        };

        groupedInputs.forEach(input => input.addEventListener("change", updateSelectionGroups));
        updateSelectionGroups();
    });

    document.querySelectorAll("[data-training-disclosure]").forEach(disclosure => {
        const toggle = disclosure.querySelector("[data-training-disclosure-toggle]");
        const panel = disclosure.querySelector("[data-training-disclosure-panel]");
        if (!toggle || !panel) return;
        toggle.addEventListener("click", () => {
            const expanded = toggle.getAttribute("aria-expanded") === "true";
            toggle.setAttribute("aria-expanded", expanded ? "false" : "true");
            toggle.textContent = expanded ? "Wybierz szkolenia" : "Ukryj wybór szkoleń";
            panel.hidden = expanded;
        });
    });

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
        const totalNode = picker.querySelector('[data-training-total]');
        if (totalNode) totalNode.textContent = formatter.format(selected);
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
