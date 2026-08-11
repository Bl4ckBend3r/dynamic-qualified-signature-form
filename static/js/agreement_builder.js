(function () {
    "use strict";

    const parseJson = (node, fallback) => {
        try { return JSON.parse(node?.textContent || node?.value || ""); } catch (_) { return fallback; }
    };
    const clone = (value) => JSON.parse(JSON.stringify(value));
    const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[char]);
    let agreementBuilderWindow = null;
    let agreementBuilderFormId = null;

    function initializePopupButtons() {
        document.querySelectorAll("[data-agreement-builder-popup]").forEach((button) => {
            button.addEventListener("click", () => {
                const formId = String(button.dataset.formId || "");
                const builderUrl = button.dataset.builderUrl;
                const error = button.closest(".agreement-builder-launcher")?.querySelector("[data-agreement-builder-popup-error]");
                if (agreementBuilderWindow && !agreementBuilderWindow.closed) {
                    if (agreementBuilderFormId !== formId) {
                        agreementBuilderWindow.location.href = builderUrl;
                        agreementBuilderFormId = formId;
                    }
                    agreementBuilderWindow.focus();
                    if (error) error.hidden = true;
                    return;
                }
                const width = Math.min(screen.availWidth, 1800);
                const height = Math.min(screen.availHeight, 1100);
                agreementBuilderWindow = window.open(
                    builderUrl,
                    "agreementBuilder",
                    `popup=yes,width=${width},height=${height},left=0,top=0,resizable=yes,scrollbars=yes`,
                );
                if (!agreementBuilderWindow) {
                    if (error) {
                        error.textContent = "Przeglądarka zablokowała otwarcie kreatora w nowym oknie. Zezwól na wyskakujące okna dla tej strony i spróbuj ponownie.";
                        error.hidden = false;
                    }
                    return;
                }
                agreementBuilderFormId = formId;
                agreementBuilderWindow.focus();
                if (error) error.hidden = true;
            });
        });
        window.addEventListener("message", (event) => {
            if (event.origin !== window.location.origin || event.data?.type !== "agreement-builder-saved") return;
            const button = document.querySelector(`[data-agreement-builder-popup][data-form-id="${String(event.data.formId)}"]`);
            const status = button?.closest(".agreement-builder-launcher")?.querySelector("[data-agreement-builder-parent-status]");
            if (status) status.textContent = `Szablon zapisany ${new Date().toLocaleTimeString("pl-PL", {hour: "2-digit", minute: "2-digit"})}.`;
        });
    }

    function initializeSourceSwitching() {
        const radios = [...document.querySelectorAll('input[name="contract_template_source"]')];
        if (!radios.length) return;
        const sync = () => {
            const source = radios.find((radio) => radio.checked)?.value || "builder";
            document.querySelectorAll("[data-source-panel]").forEach((panel) => { panel.hidden = panel.dataset.sourcePanel !== source; });
            if (source === "builder") document.querySelector("[data-agreement-builder]")?.dispatchEvent(new CustomEvent("agreement-builder-visible"));
        };
        radios.forEach((radio) => radio.addEventListener("change", sync));
        sync();
    }

    function initializeBuilder(root) {
        const initialNode = root.querySelector("[data-agreement-builder-initial]");
        const hiddenJson = root.querySelector("[data-agreement-builder-json]");
        const variables = parseJson(root.querySelector("[data-agreement-builder-variables]"), []);
        const submissions = parseJson(root.querySelector("[data-agreement-builder-submissions]"), []);
        const variableNames = new Set(variables.map((item) => item.name));
        const blocksNode = root.querySelector("[data-agreement-builder-blocks]");
        const statusNode = root.querySelector("[data-agreement-builder-status]");
        const errorsNode = root.querySelector("[data-agreement-builder-errors]");
        const previewFrame = root.querySelector("[data-agreement-builder-preview]");
        const previewPage = root.querySelector("[data-agreement-builder-preview-page]");
        const previewCanvas = root.querySelector("[data-builder-preview-canvas]");
        const previewViewport = root.querySelector("[data-builder-preview-viewport]");
        const previewState = root.querySelector("[data-agreement-builder-preview-state]");
        const editorPane = root.querySelector("[data-builder-editor-pane]");
        const previewPane = root.querySelector("[data-builder-preview-pane]");
        const variablesPane = root.querySelector("[data-agreement-builder-variable-panel]");
        const previewData = root.querySelector("[data-builder-preview-data]");
        const previewTraining = root.querySelector("[data-builder-preview-training]");
        const previewTrainingField = root.querySelector("[data-builder-preview-training-field]");
        const htmlPreview = root.querySelector("[data-agreement-builder-html]");
        const csrfToken = document.querySelector('input[name="csrf_token"]')?.value || "";
        const localKey = root.dataset.storageKey;
        const formId = Number(root.dataset.formId);
        const closeDialog = root.querySelector("[data-builder-close-dialog]");
        const uiStorageKey = `${localKey}-ui`;
        let documentModel = parseJson(initialNode, {version: 1, blocks: []});
        let activeEditable = null;
        let previewTimer = null;
        let historyTimer = null;
        let previewController = null;
        let history = [];
        let historyIndex = -1;
        let dirty = false;
        let activeRange = null;
        let activeBlockIndex = -1;
        let pendingPreviewScroll = {left: 0, top: 0};
        let resetScrollOnNextPreview = false;
        let blockedPreviewFingerprint = null;
        const builderState = {
            viewMode: "split",
            variablesVisible: true,
            workspaceSizeMode: "balanced",
            previewZoom: 0.8,
            fitWidth: false,
        };

        try {
            const localDraft = JSON.parse(localStorage.getItem(localKey) || "null");
            if (localDraft?.document?.blocks?.length) {
                documentModel = localDraft.document;
                dirty = true;
                statusNode.textContent = "Przywrócono lokalny szkic";
            }
        } catch (_) {}

        documentModel = normalizeDocumentModel(documentModel);

        try {
            const savedUi = JSON.parse(localStorage.getItem(uiStorageKey) || "null");
            if (["split", "editor", "preview"].includes(savedUi?.viewMode)) builderState.viewMode = savedUi.viewMode;
            if (typeof savedUi?.variablesVisible === "boolean") builderState.variablesVisible = savedUi.variablesVisible;
            if (["balanced", "editor-wide", "preview-wide"].includes(savedUi?.workspaceSizeMode)) builderState.workspaceSizeMode = savedUi.workspaceSizeMode;
            if (Number.isFinite(savedUi?.previewZoom)) builderState.previewZoom = Math.max(0.5, Math.min(savedUi.previewZoom, 1.5));
            if (typeof savedUi?.fitWidth === "boolean") builderState.fitWidth = savedUi.fitWidth;
        } catch (_) {}

        const blockDefaults = (type) => ({
            heading: {type: "heading", level: 2, format: {bold: true, italic: false, underline: false, alignment: "left"}, content: "Nowy nagłówek"},
            paragraph: {type: "paragraph", format: {bold: false, italic: false, underline: false, alignment: "left"}, content: "Nowy akapit"},
            agreement_section: {type: "agreement_section", number: "§ 1.", title: "Tytuł paragrafu"},
            ordered_list: {type: "ordered_list", items: [{content: "Pierwszy punkt", level: 0}]},
            bullet_list: {type: "bullet_list", items: [{content: "Pierwszy punkt", level: 0}]},
            table: {type: "table", header: true, rows: [["Nagłówek 1", "Nagłówek 2"], ["Treść", "Treść"]]},
            training_table: {type: "training_table", scope: "selected_trainings", columns: ["index", "name", "price"], show_total: true},
            participant_data: {type: "participant_data", fields: ["participant_name", "pesel", "participant_address", "telefon"]},
            signatures: {type: "signatures", left_label: "Beneficjent", right_label: "Uczestnik projektu"},
            project_info: {type: "project_info", fields: ["project_name", "project_number", "project_program", "funding_source"], show_logo: false},
            page_break: {type: "page_break"},
        }[type]);

        function textBlockFormat(block) {
            const textBlock = ["heading", "paragraph"].includes(block?.type);
            if (!textBlock) return null;
            const raw = block.format && typeof block.format === "object" ? block.format : {};
            const alignment = ["left", "center", "right", "justify"].includes(raw.alignment)
                ? raw.alignment
                : (["left", "center", "right", "justify"].includes(block.alignment) ? block.alignment : "left");
            return {
                bold: Object.prototype.hasOwnProperty.call(raw, "bold") ? raw.bold === true : block.type === "heading",
                italic: raw.italic === true,
                underline: raw.underline === true,
                alignment,
            };
        }

        function normalizeDocumentModel(value) {
            const model = value && typeof value === "object" ? clone(value) : {version: 1, blocks: []};
            model.version = 1;
            model.blocks = Array.isArray(model.blocks) ? model.blocks : [];
            model.blocks.forEach((block) => {
                const formatting = textBlockFormat(block);
                if (!formatting) return;
                block.format = formatting;
                delete block.alignment;
                delete block.style;
            });
            return model;
        }

        function blockFormatClasses(block) {
            const formatting = textBlockFormat(block);
            if (!formatting) return "";
            return [
                formatting.bold ? "is-format-bold" : "",
                formatting.italic ? "is-format-italic" : "",
                formatting.underline ? "is-format-underline" : "",
                `is-align-${formatting.alignment}`,
            ].filter(Boolean).join(" ");
        }

        function conditionEditor(block, index) {
            const condition = block.condition || {};
            const options = ['<option value="">Bez warunku</option>', ...variables.filter((item) => !["html", "collection"].includes(item.type)).map((item) => `<option value="${escapeHtml(item.name)}" ${condition.variable === item.name ? "selected" : ""}>${escapeHtml(item.label)}</option>`)].join("");
            return `<details class="agreement-builder-block__condition"><summary>Warunek wyświetlania</summary><div><select data-builder-condition-variable data-index="${index}">${options}</select><select data-builder-condition-operator data-index="${index}"><option value="not_empty" ${condition.operator === "not_empty" ? "selected" : ""}>ma wartość</option><option value="empty" ${condition.operator === "empty" ? "selected" : ""}>jest puste</option><option value="equals" ${condition.operator === "equals" ? "selected" : ""}>równa się</option><option value="not_equals" ${condition.operator === "not_equals" ? "selected" : ""}>nie równa się</option></select><input data-builder-condition-value data-index="${index}" value="${escapeHtml(condition.value || "")}" placeholder="Wartość" ${["equals", "not_equals"].includes(condition.operator) ? "" : "hidden"}></div></details>`;
        }

        function editable(value, field, index, extra = "") {
            return `<div class="agreement-builder-block__editable" contenteditable="true" role="textbox" data-builder-field="${field}" data-index="${index}" ${extra}>${value || ""}</div>`;
        }

        function renderBlock(block, index) {
            let body = "";
            if (block.type === "heading") {
                body = `<label>Poziom <select data-builder-field="level" data-index="${index}"><option value="1" ${block.level === 1 ? "selected" : ""}>H1</option><option value="2" ${block.level === 2 ? "selected" : ""}>H2</option><option value="3" ${block.level === 3 ? "selected" : ""}>H3</option></select></label>${editable(block.content, "content", index)}`;
            } else if (block.type === "paragraph") {
                body = editable(block.content, "content", index);
            } else if (block.type === "agreement_section") {
                body = `<div class="agreement-builder-block__section">${editable(block.number, "number", index)}${editable(block.title, "title", index)}</div>`;
            } else if (["ordered_list", "bullet_list"].includes(block.type)) {
                const tag = block.type === "ordered_list" ? "ol" : "ul";
                body = `<${tag}>${(block.items || []).map((item, itemIndex) => `<li data-level="${Number(item.level || 0)}"><div contenteditable="true" data-builder-list-item data-index="${index}" data-item-index="${itemIndex}">${item.content || ""}</div><button type="button" class="builder-button builder-button--icon builder-button--danger builder-button--compact" aria-label="Usuń punkt" data-builder-remove-item data-index="${index}" data-item-index="${itemIndex}">×</button></li>`).join("")}</${tag}><button type="button" class="builder-button builder-button--secondary builder-button--compact" data-builder-add-item data-index="${index}">Dodaj punkt</button>`;
            } else if (block.type === "table") {
                body = `<table><tbody>${(block.rows || []).map((row, rowIndex) => `<tr>${row.map((cell, cellIndex) => `<td contenteditable="true" data-builder-table-cell data-index="${index}" data-row-index="${rowIndex}" data-cell-index="${cellIndex}">${cell || ""}</td>`).join("")}</tr>`).join("")}</tbody></table><button type="button" class="builder-button builder-button--secondary builder-button--compact" data-builder-add-row data-index="${index}">Dodaj wiersz</button>`;
            } else if (block.type === "training_table") {
                const columnOptions = [["index", "Lp."], ["name", "Nazwa szkolenia"], ["price", "Cena"], ["date", "Termin"], ["location", "Lokalizacja"]];
                body = `<fieldset><legend>Kolumny tabeli</legend>${columnOptions.map(([value, label]) => `<label><input type="checkbox" data-builder-array="columns" data-index="${index}" value="${value}" ${(block.columns || []).includes(value) ? "checked" : ""}> ${label}</label>`).join("")}</fieldset><label>Zakres <select data-builder-field="scope" data-index="${index}"><option value="selected_trainings" ${block.scope === "selected_trainings" ? "selected" : ""}>Bieżące szkolenie</option><option value="all_selected_trainings" ${block.scope === "all_selected_trainings" ? "selected" : ""}>Wszystkie wybrane</option><option value="locked_trainings" ${block.scope === "locked_trainings" ? "selected" : ""}>Zablokowane</option></select></label><label><input type="checkbox" data-builder-field="show_total" data-index="${index}" ${block.show_total ? "checked" : ""}> Pokaż sumę</label>`;
            } else if (block.type === "participant_data") {
                const choices = [["participant_name", "Imię i nazwisko"], ["pesel", "PESEL"], ["participant_address", "Adres"], ["telefon", "Telefon"], ["email", "E-mail"], ["data_urodzenia", "Data urodzenia"]];
                body = `<fieldset><legend>Dane uczestnika</legend>${choices.map(([value, label]) => `<label><input type="checkbox" data-builder-array="fields" data-index="${index}" value="${value}" ${(block.fields || []).includes(value) ? "checked" : ""}> ${label}</label>`).join("")}</fieldset>`;
            } else if (block.type === "signatures") {
                body = `<div class="agreement-builder-block__signature-config"><label>Lewa strona<input data-builder-field="left_label" data-index="${index}" value="${escapeHtml(block.left_label || "")}"></label><label>Prawa strona<input data-builder-field="right_label" data-index="${index}" value="${escapeHtml(block.right_label || "")}"></label></div>`;
            } else if (block.type === "project_info") {
                const choices = [["project_name", "Nazwa projektu"], ["project_number", "Numer projektu"], ["project_program", "Program"], ["project_action", "Działanie"], ["funding_source", "Źródło finansowania"], ["institution_name", "Instytucja"]];
                body = `<fieldset><legend>Informacje o projekcie</legend>${choices.map(([value, label]) => `<label><input type="checkbox" data-builder-array="fields" data-index="${index}" value="${value}" ${(block.fields || []).includes(value) ? "checked" : ""}> ${label}</label>`).join("")}<label><input type="checkbox" data-builder-field="show_logo" data-index="${index}" ${block.show_logo ? "checked" : ""}> Logo projektu</label></fieldset>`;
            } else if (block.type === "page_break") {
                body = '<div class="agreement-builder-block__page-break">Podział strony</div>';
            }
            const selectedClass = index === activeBlockIndex ? " is-selected" : "";
            const formatClasses = blockFormatClasses(block);
            return `<article class="agreement-builder-block${selectedClass}${formatClasses ? ` ${formatClasses}` : ""}" draggable="true" data-builder-block data-index="${index}" data-block-type="${escapeHtml(block.type)}" aria-selected="${index === activeBlockIndex}"><header><strong>${escapeHtml(block.type.replaceAll("_", " "))}</strong><span><button type="button" class="builder-button builder-button--icon builder-button--compact" title="Przesuń w górę" aria-label="Przesuń blok w górę" data-builder-move="up" data-index="${index}">↑</button><button type="button" class="builder-button builder-button--icon builder-button--compact" title="Przesuń w dół" aria-label="Przesuń blok w dół" data-builder-move="down" data-index="${index}">↓</button><button type="button" class="builder-button builder-button--danger builder-button--compact" title="Usuń blok" data-builder-remove data-index="${index}">Usuń</button></span></header><div class="agreement-builder-block__body">${body}</div>${conditionEditor(block, index)}</article>`;
        }

        function render() {
            blocksNode.innerHTML = documentModel.blocks.map(renderBlock).join("");
            hiddenJson.value = JSON.stringify(documentModel);
            activeEditable = null;
            activeRange = null;
            refreshControls();
        }

        function readField(target) {
            const index = Number(target.dataset.index);
            const block = documentModel.blocks[index];
            if (!block) return;
            const field = target.dataset.builderField;
            if (field) {
                if (target.type === "checkbox") block[field] = target.checked;
                else if (target.isContentEditable) block[field] = target.innerHTML;
                else if (field === "level") block[field] = Number(target.value);
                else block[field] = target.value;
            }
            if (target.dataset.builderArray) {
                block[target.dataset.builderArray] = [...root.querySelectorAll(`[data-builder-array="${target.dataset.builderArray}"][data-index="${index}"]:checked`)].map((item) => item.value);
            }
            if (target.matches("[data-builder-list-item]")) block.items[Number(target.dataset.itemIndex)].content = target.innerHTML;
            if (target.matches("[data-builder-table-cell]")) block.rows[Number(target.dataset.rowIndex)][Number(target.dataset.cellIndex)] = target.innerHTML;
            if (target.matches("[data-builder-condition-variable]")) {
                if (target.value) block.condition = {...(block.condition || {}), variable: target.value, operator: block.condition?.operator || "not_empty"};
                else delete block.condition;
                render();
            }
            if (target.matches("[data-builder-condition-operator]")) { block.condition = {...(block.condition || {}), operator: target.value}; render(); }
            if (target.matches("[data-builder-condition-value]")) block.condition = {...(block.condition || {}), value: target.value};
            changed();
        }

        function changed({checkpointNow = false} = {}) {
            hiddenJson.value = JSON.stringify(documentModel);
            statusNode.textContent = "Niezapisane zmiany";
            dirty = true;
            try { localStorage.setItem(localKey, JSON.stringify({savedAt: new Date().toISOString(), document: documentModel})); } catch (_) {}
            window.clearTimeout(historyTimer);
            if (checkpointNow) checkpoint();
            else historyTimer = window.setTimeout(checkpoint, 350);
            schedulePreview();
        }

        function checkpoint() {
            const serialized = JSON.stringify(documentModel);
            if (history[historyIndex] === serialized) return;
            history = history.slice(0, historyIndex + 1);
            history.push(serialized);
            if (history.length > 60) history.shift();
            historyIndex = history.length - 1;
            refreshControls();
        }

        function restoreHistory(nextIndex) {
            if (nextIndex < 0 || nextIndex >= history.length) return;
            historyIndex = nextIndex;
            documentModel = JSON.parse(history[historyIndex]);
            activeBlockIndex = Math.min(activeBlockIndex, documentModel.blocks.length - 1);
            render();
            changed();
        }

        function refreshControls() {
            root.querySelector('[data-builder-action="undo"]')?.toggleAttribute("disabled", historyIndex <= 0);
            root.querySelector('[data-builder-action="redo"]')?.toggleAttribute("disabled", historyIndex >= history.length - 1);
            refreshFormattingControls();
        }

        function activeTextBlock() {
            const block = documentModel.blocks[activeBlockIndex];
            return textBlockFormat(block) ? block : null;
        }

        function setActiveBlock(index) {
            activeBlockIndex = Number.isInteger(index) && documentModel.blocks[index] ? index : -1;
            root.querySelectorAll("[data-builder-block]").forEach((article) => {
                const selected = Number(article.dataset.index) === activeBlockIndex;
                article.classList.toggle("is-selected", selected);
                article.setAttribute("aria-selected", String(selected));
            });
            refreshFormattingControls();
        }

        function refreshFormattingControls() {
            const block = activeTextBlock();
            const formatting = textBlockFormat(block);
            root.querySelectorAll("[data-builder-format]").forEach((button) => {
                const active = Boolean(formatting?.[button.dataset.builderFormat]);
                button.disabled = !block;
                button.classList.toggle("is-active", active);
                button.setAttribute("aria-pressed", String(active));
            });
            root.querySelectorAll("[data-builder-align]").forEach((button) => {
                const active = formatting?.alignment === button.dataset.builderAlign;
                button.disabled = !block;
                button.classList.toggle("is-active", active);
                button.setAttribute("aria-pressed", String(active));
            });
        }

        function toggleBlockFormat(name) {
            const block = activeTextBlock();
            if (!block || !["bold", "italic", "underline"].includes(name)) return;
            block.format = textBlockFormat(block);
            block.format[name] = !block.format[name];
            render();
            changed({checkpointNow: true});
        }

        function alignBlock(alignment) {
            const block = activeTextBlock();
            if (!block || !["left", "center", "right", "justify"].includes(alignment)) return;
            block.format = textBlockFormat(block);
            block.format.alignment = alignment;
            render();
            changed({checkpointNow: true});
        }

        function selectedParams(formData) {
            const submissionId = previewData?.value || "";
            formData.set("preview_mode", submissionId ? "submission" : "example");
            if (submissionId) formData.set("submission_id", submissionId);
            if (previewTraining?.value) formData.set("training_id", previewTraining.value);
        }

        function requestFormData() {
            const data = new FormData();
            data.set("csrf_token", csrfToken);
            data.set("builder_json", JSON.stringify(documentModel));
            selectedParams(data);
            return data;
        }

        function previewErrorMessage(payload) {
            const missing = Array.isArray(payload?.missing_variables) ? payload.missing_variables.filter(Boolean) : [];
            if (!missing.length) return payload?.error || "Nie udało się wygenerować podglądu umowy.";
            return `Nie można wygenerować podglądu.\n\nBrakujące zmienne:\n${missing.map((name) => `• {{ ${name} }}`).join("\n")}`;
        }

        function showErrors(payload, heading = "Nie można aktywować szablonu.") {
            const missing = Array.isArray(payload?.missing_variables) ? payload.missing_variables.filter(Boolean) : [];
            const errors = payload?.errors?.length
                ? payload.errors
                : missing.length
                    ? missing.map((name) => ({message: `Brak zmiennej {{ ${name} }}`}))
                    : [{message: payload?.error || "Nie udało się wykonać operacji."}];
            errorsNode.hidden = false;
            errorsNode.innerHTML = `<strong>${escapeHtml(heading)}</strong><ul>${errors.map((error) => `<li><button type="button" class="agreement-builder__error-link" data-builder-error-path="${escapeHtml(error.path || "")}">${escapeHtml(error.message)}</button></li>`).join("")}</ul>`;
            statusNode.textContent = errors[0].message;
        }

        async function preview({resetScroll = false, force = false} = {}) {
            if (root.hidden) return;
            const previewFingerprint = JSON.stringify([documentModel, previewData?.value || "", previewTraining?.value || ""]);
            if (!force && blockedPreviewFingerprint === previewFingerprint) return;
            pendingPreviewScroll = resetScroll || !previewViewport
                ? {left: 0, top: 0}
                : {left: previewViewport.scrollLeft, top: previewViewport.scrollTop};
            if (previewData?.value && !previewTraining?.value) {
                previewState.textContent = "Wybierz konkretne szkolenie.";
                previewState.hidden = false;
                previewCanvas.hidden = true;
                return;
            }
            previewController?.abort();
            previewController = new AbortController();
            previewState.textContent = "Generowanie podglądu…";
            previewState.hidden = false;
            if (!previewFrame.srcdoc) previewCanvas.hidden = true;
            try {
                const response = await fetch(root.dataset.previewUrl, {method: "POST", body: requestFormData(), signal: previewController.signal, headers: {Accept: "application/json"}});
                const payload = await response.json().catch(() => ({}));
                if (!response.ok || !payload.ok) {
                    if (response.status === 422) blockedPreviewFingerprint = previewFingerprint;
                    showErrors(payload, "Nie można wygenerować podglądu.");
                    throw new Error(previewErrorMessage(payload));
                }
                blockedPreviewFingerprint = null;
                errorsNode.hidden = true;
                previewFrame.srcdoc = payload.html;
                if (htmlPreview) htmlPreview.value = payload.html;
                previewState.hidden = true;
                previewCanvas.hidden = false;
                if (!dirty) statusNode.textContent = "Podgląd aktualny";
            } catch (error) {
                if (error.name === "AbortError") return;
                previewState.textContent = error.message;
                previewState.hidden = false;
                if (!previewFrame.srcdoc) previewCanvas.hidden = true;
            }
        }

        function schedulePreview({resetScroll = false} = {}) {
            resetScrollOnNextPreview = resetScrollOnNextPreview || resetScroll;
            window.clearTimeout(previewTimer);
            previewTimer = window.setTimeout(() => {
                const shouldResetScroll = resetScrollOnNextPreview;
                resetScrollOnNextPreview = false;
                preview({resetScroll: shouldResetScroll});
            }, 550);
        }

        async function save(action) {
            const data = requestFormData();
            data.set("action", action);
            statusNode.textContent = action === "activate" ? "Aktywowanie…" : "Zapisywanie…";
            const response = await fetch(root.dataset.saveUrl, {method: "POST", body: data, headers: {Accept: "application/json"}});
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.ok) { showErrors(payload); return false; }
            errorsNode.hidden = true;
            dirty = false;
            statusNode.textContent = `Szablon zapisany ${new Date().toLocaleTimeString("pl-PL", {hour: "2-digit", minute: "2-digit"})}.`;
            try { localStorage.removeItem(localKey); } catch (_) {}
            window.opener?.postMessage({type: "agreement-builder-saved", formId}, window.location.origin);
            if (action === "activate") {
                const radio = document.querySelector('input[name="contract_template_source"][value="builder"]');
                if (radio) radio.checked = true;
            }
            return true;
        }

        function persistBuilderState() {
            try { localStorage.setItem(uiStorageKey, JSON.stringify(builderState)); } catch (_) {}
        }

        function applyBuilderLayout({persist = true, refit = true} = {}) {
            root.dataset.viewMode = builderState.viewMode;
            root.dataset.layout = builderState.workspaceSizeMode;
            root.dataset.variablesVisible = String(builderState.variablesVisible);
            root.dataset.fitWidth = String(builderState.fitWidth);
            root.dataset.previewZoom = String(Math.round(builderState.previewZoom * 100));

            const editorVisible = builderState.viewMode !== "preview";
            const previewVisible = builderState.viewMode !== "editor";
            editorPane.hidden = !editorVisible;
            editorPane.setAttribute("aria-hidden", String(!editorVisible));
            previewPane.hidden = !previewVisible;
            previewPane.setAttribute("aria-hidden", String(!previewVisible));
            variablesPane.hidden = !builderState.variablesVisible;
            variablesPane.setAttribute("aria-hidden", String(!builderState.variablesVisible));

            root.querySelectorAll("[data-builder-view-mode]").forEach((button) => {
                const active = button.dataset.builderViewMode === builderState.viewMode;
                button.classList.toggle("is-active", active);
                button.setAttribute("aria-pressed", String(active));
            });
            root.querySelectorAll("[data-builder-size-mode]").forEach((button) => {
                const active = button.dataset.builderSizeMode === builderState.workspaceSizeMode;
                button.classList.toggle("is-active", active);
                button.setAttribute("aria-pressed", String(active));
            });
            root.querySelectorAll("[data-builder-variables-toggle]").forEach((button) => {
                button.classList.toggle("is-active", builderState.variablesVisible);
                button.setAttribute("aria-pressed", String(builderState.variablesVisible));
            });
            const fitButton = root.querySelector('[data-builder-zoom="fit"]');
            fitButton?.classList.toggle("is-active", builderState.fitWidth);
            fitButton?.setAttribute("aria-pressed", String(builderState.fitWidth));
            previewPage.style.zoom = String(builderState.previewZoom);
            const output = root.querySelector("[data-builder-zoom-value]");
            if (output) output.textContent = `${Math.round(builderState.previewZoom * 100)}%`;
            if (persist) persistBuilderState();
            if (builderState.fitWidth && previewVisible && refit) window.requestAnimationFrame(() => fitPreview({persist}));
        }

        function setZoom(value, {fitWidth = false, persist = true} = {}) {
            builderState.previewZoom = Math.max(0.5, Math.min(Number(value) || 0.8, 1.5));
            builderState.fitWidth = fitWidth;
            applyBuilderLayout({persist, refit: false});
        }

        function fitPreview({persist = true} = {}) {
            if (!previewViewport || !previewPage) return;
            const renderedWidth = previewPage.getBoundingClientRect().width;
            const pageWidth = renderedWidth > 0 ? renderedWidth / builderState.previewZoom : ((210 / 25.4) * 96);
            const available = Math.max(previewViewport.clientWidth - 48, 1);
            setZoom(Math.min(1, available / pageWidth), {fitWidth: true, persist});
        }

        function requestClose() {
            if (!dirty) { window.close(); return; }
            if (typeof closeDialog?.showModal === "function") closeDialog.showModal();
        }

        async function downloadPdf() {
            statusNode.textContent = "Generowanie PDF…";
            const response = await fetch(root.dataset.pdfUrl, {method: "POST", body: requestFormData()});
            const contentType = response.headers.get("content-type") || "";
            if (!response.ok || contentType.includes("application/json")) {
                showErrors(await response.json().catch(() => ({})), "Nie można wygenerować przykładowego PDF.");
                return;
            }
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url; link.download = "przykladowa-umowa.pdf"; link.click();
            window.setTimeout(() => URL.revokeObjectURL(url), 1000);
            if (!dirty) statusNode.textContent = "PDF pobrany";
        }

        function insertVariable(name) {
            if (!variableNames.has(name)) return;
            let target = activeEditable && document.contains(activeEditable) ? activeEditable : blocksNode.querySelector("[contenteditable=true]");
            if (!target) {
                documentModel.blocks.push(blockDefaults("paragraph"));
                documentModel.blocks.at(-1).content = "";
                render();
                target = blocksNode.querySelector("[data-builder-block]:last-child [contenteditable=true]");
            }
            target.focus();
            if (activeRange && target.contains(activeRange.commonAncestorContainer)) {
                const selection = window.getSelection();
                selection.removeAllRanges();
                selection.addRange(activeRange);
            }
            document.execCommand("insertText", false, `{{ ${name} }}`);
            target.dispatchEvent(new Event("input", {bubbles: true}));
            rememberVariable(name);
        }

        function rememberVariable(name) {
            const key = `${localKey}-recent-variables`;
            let recent = [];
            try { recent = JSON.parse(localStorage.getItem(key) || "[]"); } catch (_) {}
            recent = [name, ...recent.filter((item) => item !== name)].slice(0, 6);
            try { localStorage.setItem(key, JSON.stringify(recent)); } catch (_) {}
            renderRecent(recent);
        }

        function renderRecent(explicit) {
            const wrapper = root.querySelector("[data-builder-recent-variables]");
            const list = root.querySelector("[data-builder-recent-list]");
            let recent = explicit;
            if (!recent) try { recent = JSON.parse(localStorage.getItem(`${localKey}-recent-variables`) || "[]"); } catch (_) { recent = []; }
            const valid = (recent || []).filter((name) => variableNames.has(name));
            wrapper.hidden = !valid.length;
            list.innerHTML = valid.map((name) => `<button type="button" class="builder-button builder-button--secondary builder-button--compact" data-builder-insert-variable="${escapeHtml(name)}">{{ ${escapeHtml(name)} }}</button>`).join("");
        }

        const rememberEditableRange = () => {
            const selection = window.getSelection();
            if (!activeEditable || !selection?.rangeCount) return;
            const range = selection.getRangeAt(0);
            if (activeEditable.contains(range.commonAncestorContainer)) activeRange = range.cloneRange();
        };
        root.addEventListener("focusin", (event) => {
            const article = event.target.closest?.("[data-builder-block]");
            if (article) setActiveBlock(Number(article.dataset.index));
            if (event.target.isContentEditable) { activeEditable = event.target; rememberEditableRange(); }
        });
        root.addEventListener("keyup", rememberEditableRange);
        root.addEventListener("mouseup", rememberEditableRange);
        root.addEventListener("input", (event) => readField(event.target));
        root.addEventListener("change", (event) => readField(event.target));
        root.addEventListener("click", async (event) => {
            const selectedArticle = event.target.closest("[data-builder-block]");
            if (selectedArticle) setActiveBlock(Number(selectedArticle.dataset.index));
            const add = event.target.closest("[data-builder-add]");
            if (add) { documentModel.blocks.push(clone(blockDefaults(add.dataset.builderAdd))); activeBlockIndex = documentModel.blocks.length - 1; render(); changed(); return; }
            const remove = event.target.closest("[data-builder-remove]");
            if (remove) { const removed = Number(remove.dataset.index); documentModel.blocks.splice(removed, 1); activeBlockIndex = Math.min(removed, documentModel.blocks.length - 1); render(); changed(); return; }
            const move = event.target.closest("[data-builder-move]");
            if (move) { const from = Number(move.dataset.index); const to = move.dataset.builderMove === "up" ? from - 1 : from + 1; if (to >= 0 && to < documentModel.blocks.length) { [documentModel.blocks[from], documentModel.blocks[to]] = [documentModel.blocks[to], documentModel.blocks[from]]; activeBlockIndex = to; render(); changed(); } return; }
            const addItem = event.target.closest("[data-builder-add-item]");
            if (addItem) { documentModel.blocks[Number(addItem.dataset.index)].items.push({content: "Nowy punkt", level: 0}); render(); changed(); return; }
            const removeItem = event.target.closest("[data-builder-remove-item]");
            if (removeItem) { documentModel.blocks[Number(removeItem.dataset.index)].items.splice(Number(removeItem.dataset.itemIndex), 1); render(); changed(); return; }
            const addRow = event.target.closest("[data-builder-add-row]");
            if (addRow) { const table = documentModel.blocks[Number(addRow.dataset.index)]; table.rows.push(Array(table.rows[0]?.length || 2).fill("")); render(); changed(); return; }
            const variable = event.target.closest("[data-builder-insert-variable]");
            if (variable) { insertVariable(variable.dataset.builderInsertVariable); return; }
            const copy = event.target.closest("[data-builder-copy-variable]");
            if (copy) { await navigator.clipboard.writeText(`{{ ${copy.dataset.builderCopyVariable} }}`); if (!dirty) statusNode.textContent = "Zmienna skopiowana"; return; }
            const format = event.target.closest("[data-builder-format]");
            if (format) { toggleBlockFormat(format.dataset.builderFormat); return; }
            const align = event.target.closest("[data-builder-align]");
            if (align) { alignBlock(align.dataset.builderAlign); return; }
            const action = event.target.closest("[data-builder-action]")?.dataset.builderAction;
            if (action === "undo") restoreHistory(historyIndex - 1);
            if (action === "redo") restoreHistory(historyIndex + 1);
            if (action === "variables") {
                builderState.variablesVisible = true;
                applyBuilderLayout();
                root.querySelector("[data-builder-variable-search]")?.focus();
            }
            const saveButton = event.target.closest("[data-builder-save]");
            if (saveButton) await save(saveButton.dataset.builderSave);
            if (event.target.closest("[data-builder-preview-now]")) await preview({force: true});
            if (event.target.closest("[data-builder-download-pdf]")) await downloadPdf();
            const errorPath = event.target.closest("[data-builder-error-path]")?.dataset.builderErrorPath;
            if (errorPath?.startsWith("blocks.")) blocksNode.querySelector(`[data-index="${Number(errorPath.split(".")[1])}"]`)?.scrollIntoView({behavior: "smooth", block: "center"});
            const view = event.target.closest("[data-builder-view-mode]");
            if (view) {
                builderState.viewMode = view.dataset.builderViewMode;
                applyBuilderLayout();
            }
            const size = event.target.closest("[data-builder-size-mode]");
            if (size) {
                const requestedMode = size.dataset.builderSizeMode;
                builderState.workspaceSizeMode = builderState.workspaceSizeMode === requestedMode ? "balanced" : requestedMode;
                applyBuilderLayout();
            }
            if (event.target.closest("[data-builder-variables-toggle]")) {
                builderState.variablesVisible = !builderState.variablesVisible;
                applyBuilderLayout();
            }
            const zoom = event.target.closest("[data-builder-zoom]")?.dataset.builderZoom;
            if (zoom === "in") setZoom(builderState.previewZoom + 0.1);
            if (zoom === "out") setZoom(builderState.previewZoom - 0.1);
            if (zoom === "fit") fitPreview();
            if (zoom === "100") setZoom(1);
            if (event.target.closest("[data-builder-close]")) requestClose();
            if (event.target.closest("[data-builder-close-cancel]")) closeDialog?.close();
            if (event.target.closest("[data-builder-close-discard]")) { dirty = false; closeDialog?.close(); window.close(); }
            if (event.target.closest("[data-builder-close-save]")) { if (await save("draft")) { closeDialog?.close(); window.close(); } }
        });

        root.addEventListener("dragstart", (event) => {
            const variable = event.target.closest("[data-builder-variable]");
            if (variable) event.dataTransfer.setData("text/agreement-variable", variable.dataset.builderVariable);
            const block = event.target.closest("[data-builder-block]");
            if (block && !variable) event.dataTransfer.setData("text/agreement-block", block.dataset.index);
        });
        blocksNode.addEventListener("dragover", (event) => event.preventDefault());
        blocksNode.addEventListener("drop", (event) => {
            event.preventDefault();
            const variableName = event.dataTransfer.getData("text/agreement-variable");
            if (variableName) { const editableTarget = event.target.closest("[contenteditable=true]"); if (editableTarget) activeEditable = editableTarget; insertVariable(variableName); return; }
            const from = Number(event.dataTransfer.getData("text/agreement-block"));
            const target = event.target.closest("[data-builder-block]");
            if (Number.isInteger(from) && target) { const [block] = documentModel.blocks.splice(from, 1); activeBlockIndex = Number(target.dataset.index); documentModel.blocks.splice(activeBlockIndex, 0, block); render(); changed(); }
        });

        root.querySelector("[data-builder-variable-search]")?.addEventListener("input", (event) => {
            const query = event.target.value.trim().toLocaleLowerCase("pl");
            root.querySelectorAll("[data-builder-variable]").forEach((item) => { item.hidden = Boolean(query) && !item.dataset.builderVariableSearchText.includes(query); });
            root.querySelectorAll("[data-builder-variable-category]").forEach((category) => { category.hidden = !category.querySelector("[data-builder-variable]:not([hidden])"); });
        });
        previewData?.addEventListener("change", () => {
            const submission = submissions.find((item) => item.submission_id === previewData.value);
            const trainings = submission?.trainings || [];
            previewTraining.replaceChildren(new Option("Wybierz szkolenie", ""), ...trainings.map((item) => new Option(item.label, item.id)));
            previewTrainingField.hidden = !previewData.value;
            if (trainings.length === 1) previewTraining.value = trainings[0].id;
            schedulePreview({resetScroll: true});
        });
        previewTraining?.addEventListener("change", () => schedulePreview({resetScroll: true}));
        root.addEventListener("agreement-builder-visible", schedulePreview);
        previewFrame?.addEventListener("load", () => {
            const previewDocument = previewFrame.contentDocument;
            previewDocument?.documentElement?.classList.add("agreement-preview-document");
            const height = Math.max(previewDocument?.documentElement?.scrollHeight || 0, 1123);
            previewFrame.style.height = `${height}px`;
            previewCanvas.hidden = false;
            window.requestAnimationFrame(() => {
                if (!previewViewport) return;
                previewViewport.scrollLeft = pendingPreviewScroll.left;
                previewViewport.scrollTop = pendingPreviewScroll.top;
            });
        });
        window.addEventListener("beforeunload", (event) => {
            if (!dirty) return;
            event.preventDefault();
            event.returnValue = "";
        });
        const mobileQuery = window.matchMedia("(max-width: 900px)");
        mobileQuery.addEventListener("change", (event) => {
            if (event.matches && builderState.viewMode === "split") {
                builderState.viewMode = "editor";
                applyBuilderLayout();
            }
        });
        if (typeof ResizeObserver === "function" && previewViewport) {
            new ResizeObserver(() => {
                if (builderState.fitWidth && !previewPane.hidden) fitPreview({persist: false});
            }).observe(previewViewport);
        }

        render();
        history = [JSON.stringify(documentModel)];
        historyIndex = 0;
        renderRecent();
        if (mobileQuery.matches && builderState.viewMode === "split") builderState.viewMode = "editor";
        applyBuilderLayout({persist: false});
        schedulePreview();
    }

    document.addEventListener("DOMContentLoaded", () => {
        initializeSourceSwitching();
        initializePopupButtons();
        document.querySelectorAll("[data-agreement-builder]").forEach(initializeBuilder);
    });
})();
