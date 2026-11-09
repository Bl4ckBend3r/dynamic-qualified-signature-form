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
        const applyFilter = () => {
            const phrase = filter.value.trim().toLocaleLowerCase("pl");
            let visible = 0;
            panel.querySelectorAll("[data-variable-row]").forEach((row) => {
                row.hidden = Boolean(phrase)
                    && !row.dataset.variableSearch.toLocaleLowerCase("pl").includes(phrase);
                if (!row.hidden) visible += 1;
            });
            panel.querySelectorAll(".mail-variable-group").forEach((group) => {
                const hasMatches = Boolean(group.querySelector("[data-variable-row]:not([hidden])"));
                group.hidden = !hasMatches;
                group.open = Boolean(phrase && hasMatches);
            });
            panel.querySelector("[data-variable-empty]").hidden = visible > 0;
        };
        panel.querySelectorAll(".mail-variable-group").forEach((group) => { group.open = false; });
        filter?.addEventListener("input", applyFilter);

        const variableUrl = scope.dataset.variableUrl;
        const submissionSelect = scope.querySelector("[data-preview-submission]");
        let variableRequest;
        const renderGroups = (categories) => {
            const container = panel.querySelector(".mail-variable-groups");
            container.replaceChildren();
            categories.forEach((category) => {
                const group = document.createElement("details");
                group.className = "mail-variable-group";
                const summary = document.createElement("summary");
                summary.textContent = `${category.name} `;
                const count = document.createElement("span");
                count.textContent = category.variables.length;
                summary.append(count);
                const list = document.createElement("div");
                list.className = "mail-variable-list";
                category.variables.forEach((variable) => {
                    const row = document.createElement("article");
                    row.className = "mail-variable-row";
                    row.dataset.variableRow = "";
                    row.dataset.variableSearch = [variable.name, variable.label, variable.placeholder, variable.description].join(" ");
                    const content = document.createElement("div");
                    content.className = "mail-variable-row__content";
                    const label = document.createElement("strong");
                    label.textContent = variable.label;
                    const code = document.createElement("code");
                    code.textContent = variable.placeholder;
                    code.title = variable.placeholder;
                    content.append(label, code);
                    if (variable.type) {
                        const type = document.createElement("small");
                        type.textContent = `Typ: ${variable.type}`;
                        content.append(type);
                    }
                    if (variable.example) {
                        if (variable.example.length > 100 || variable.example.includes("<")) {
                            const example = document.createElement("details");
                            example.className = "mail-variable-example";
                            const heading = document.createElement("summary");
                            heading.textContent = "Przykład";
                            const value = document.createElement("code");
                            value.textContent = variable.example;
                            example.append(heading, value);
                            content.append(example);
                        } else {
                            const example = document.createElement("small");
                            example.className = "mail-variable-example";
                            example.textContent = `Przykład: ${variable.example}`;
                            content.append(example);
                        }
                    }
                    const insert = document.createElement("button");
                    insert.type = "button";
                    insert.className = "admin-button-secondary mail-variable-insert";
                    insert.dataset.insertVariable = variable.placeholder;
                    insert.textContent = "Wstaw";
                    row.append(content, insert);
                    list.append(row);
                });
                group.append(summary, list);
                container.append(group);
            });
            applyFilter();
        };
        const renderAttachments = (files) => {
            const container = scope.querySelector("[data-preview-attachments]");
            if (!container) return;
            container.replaceChildren();
            if (!files.length) {
                const empty = document.createElement("p");
                empty.className = "admin-empty-state";
                empty.textContent = "Brak plików dla wybranego zgłoszenia.";
                container.append(empty);
            }
            files.forEach((file) => {
                const label = document.createElement("label");
                label.className = "admin-check";
                const input = document.createElement("input");
                input.type = "checkbox";
                input.value = file.id;
                input.dataset.previewAttachment = "";
                input.disabled = !file.available;
                const text = document.createElement("span");
                text.textContent = `${file.filename} · ${file.document_type} · ${file.size_label} · ${file.available ? "Dostępny" : "Niedostępny"}`;
                label.append(input, text);
                container.append(label);
            });
        };
        submissionSelect?.addEventListener("change", async () => {
            if (!variableUrl) return;
            variableRequest?.abort();
            const controller = new AbortController();
            variableRequest = controller;
            const url = new URL(variableUrl, window.location.href);
            if (submissionSelect.value) url.searchParams.set("submission_id", submissionSelect.value);
            try {
                const response = await fetch(url, {signal: controller.signal});
                if (!response.ok) throw new Error("Nie udało się pobrać zmiennych.");
                const payload = await response.json();
                if (controller !== variableRequest) return;
                renderGroups(payload.categories || []);
                renderAttachments(payload.attachments || []);
                if (feedback) feedback.textContent = "Zmienne zostały zaktualizowane.";
            } catch (error) {
                if (error.name !== "AbortError" && feedback) feedback.textContent = error.message;
            }
        });
    });
});
