document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-mail-variable-scope]").forEach((scope) => {
        const panel = scope.querySelector("[data-variable-panel]");
        if (!panel) return;

        const targets = Array.from(scope.querySelectorAll("[data-variable-target]"));
        const fallback = scope.querySelector("[data-variable-default-target]")
            || scope.querySelector('[name="body_html"]')
            || scope.querySelector('[name="html_body"]');
        const feedback = panel.querySelector("[data-variable-feedback]");
        const storageKey = panel.dataset.variableStorageKey || "admin-mail-variables-collapsed";
        let target = fallback;

        targets.forEach((field) => field.addEventListener("focus", () => {
            target = field;
            if (feedback) feedback.textContent = "";
        }));

        const setCollapsed = (collapsed) => {
            panel.classList.toggle("is-collapsed", collapsed);
            panel.querySelector("[data-variable-collapse]")?.setAttribute("aria-expanded", String(!collapsed));
            try {
                localStorage.setItem(storageKey, collapsed ? "1" : "0");
            } catch (_error) {
                // Brak localStorage nie może blokować edycji wiadomości.
            }
        };

        try {
            setCollapsed(localStorage.getItem(storageKey) === "1");
        } catch (_error) {
            setCollapsed(false);
        }
        panel.querySelector("[data-variable-collapse]")?.addEventListener("click", () => setCollapsed(true));
        panel.querySelector("[data-variable-expand]")?.addEventListener("click", () => setCollapsed(false));

        panel.addEventListener("click", (event) => {
            const button = event.target.closest("[data-insert-variable]");
            if (!button) return;
            target = target || fallback;
            if (!target || typeof target.setRangeText !== "function") {
                if (feedback) feedback.textContent = "Kliknij pole, do którego chcesz wstawić zmienną.";
                return;
            }
            const start = target.selectionStart ?? target.value.length;
            const end = target.selectionEnd ?? start;
            target.setRangeText(button.dataset.insertVariable, start, end, "end");
            target.focus();
            target.dispatchEvent(new Event("input", {bubbles: true}));
            if (feedback) feedback.textContent = `Wstawiono ${button.dataset.insertVariable}.`;
        });

        const filter = panel.querySelector("[data-variable-filter]");
        filter?.addEventListener("input", () => {
            const phrase = filter.value.trim().toLocaleLowerCase("pl");
            let visible = 0;
            panel.querySelectorAll("[data-variable-row]").forEach((row) => {
                row.hidden = Boolean(phrase)
                    && !row.dataset.variableSearch.toLocaleLowerCase("pl").includes(phrase);
                if (!row.hidden) visible += 1;
            });
            panel.querySelector("[data-variable-empty]").hidden = visible > 0;
        });
    });
});
