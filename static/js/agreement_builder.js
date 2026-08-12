(function () {
    "use strict";

    const parseJson = (node, fallback) => {
        try { return JSON.parse(node?.textContent || node?.value || ""); } catch (_) { return fallback; }
    };
    const clone = (value) => JSON.parse(JSON.stringify(value));
    const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[char]);
    const BUILDER_SCHEMA_VERSION = 1;
    const formatLastSaved = (value) => {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value || "");
        const pad = (part) => String(part).padStart(2, "0");
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}, ${pad(date.getHours())}:${pad(date.getMinutes())}`;
    };
    let agreementBuilderWindow = null;
    let agreementBuilderIdentity = null;

    function updateLastSavedStatus(status, value) {
        if (!status || !value) return;
        status.replaceChildren("Ostatni zapis: ");
        const time = document.createElement("time");
        time.dateTime = value;
        time.dataset.documentLastSaved = "";
        time.textContent = formatLastSaved(value);
        status.append(time);
    }

    function initializePopupButtons() {
        document.querySelectorAll("[data-agreement-builder-popup], [data-document-builder-popup]").forEach((button) => {
            button.addEventListener("click", () => {
                const formId = String(button.dataset.formId || "");
                const documentType = String(button.dataset.documentType || "agreement");
                const identity = `${documentType}:${formId}`;
                const builderUrl = button.dataset.builderUrl;
                const launcher = button.closest("[data-source-panel=\"builder\"], .agreement-builder-launcher, .document-builder-launcher");
                const error = launcher?.querySelector("[data-agreement-builder-popup-error], [data-document-builder-popup-error]");
                if (agreementBuilderWindow && !agreementBuilderWindow.closed) {
                    if (agreementBuilderIdentity !== identity) {
                        agreementBuilderWindow.location.href = builderUrl;
                        agreementBuilderIdentity = identity;
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
                agreementBuilderIdentity = identity;
                agreementBuilderWindow.focus();
                if (error) error.hidden = true;
            });
        });
        window.addEventListener("message", (event) => {
            if (event.origin !== window.location.origin || !["agreement-builder-saved", "document-builder-saved"].includes(event.data?.type)) return;
            const button = event.data.documentType
                ? document.querySelector(`[data-document-builder-popup][data-document-type="${String(event.data.documentType)}"][data-form-id="${String(event.data.formId)}"]`)
                : document.querySelector(`[data-agreement-builder-popup][data-form-id="${String(event.data.formId)}"]`);
            const scope = button?.closest("[data-document-source-scope]");
            const status = scope?.querySelector("[data-agreement-builder-parent-status], [data-document-builder-parent-status]");
            updateLastSavedStatus(status, event.data.updatedAt || new Date().toISOString());
            if (event.data.activated) {
                const radio = scope?.querySelector('input[type="radio"][value="builder"]');
                if (radio) {
                    radio.checked = true;
                    radio.dispatchEvent(new Event("change", {bubbles: true}));
                }
            }
        });
    }

    function initializeSourceSwitching() {
        ["contract", "declaration"].forEach((prefix) => {
            const radios = [...document.querySelectorAll(`input[name="${prefix}_template_source"]`)];
            if (!radios.length) return;
            const scope = radios[0].closest("[data-document-source-scope]") || document;
            const sync = () => {
                const source = radios.find((radio) => radio.checked)?.value || "builder";
                scope.querySelectorAll("[data-source-panel]").forEach((panel) => { panel.hidden = panel.dataset.sourcePanel !== source; });
                scope.querySelectorAll("[data-document-variable-catalog]").forEach((catalog) => { catalog.hidden = source === "builder"; });
                scope.querySelectorAll("[data-document-source-option]").forEach((option) => {
                    const active = option.dataset.source === source;
                    option.classList.toggle("is-active", active);
                    option.dataset.active = String(active);
                });
                scope.dataset.templateSource = source;
            };
            radios.forEach((radio) => radio.addEventListener("change", sync));
            sync();
        });
    }

    function initializeLastSavedTimestamps() {
        document.querySelectorAll("[data-document-last-saved]").forEach((time) => {
            const value = time.getAttribute("datetime") || time.textContent;
            time.textContent = formatLastSaved(value);
        });
    }

    function initializeDocxDropzones() {
        document.querySelectorAll(".document-docx-dropzone").forEach((dropzone) => {
            const input = dropzone.querySelector('input[type="file"]');
            if (!input) return;
            ["dragenter", "dragover"].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
                event.preventDefault();
                dropzone.classList.add("is-dragover");
            }));
            ["dragleave", "drop"].forEach((eventName) => dropzone.addEventListener(eventName, () => {
                dropzone.classList.remove("is-dragover");
            }));
            dropzone.addEventListener("drop", (event) => {
                event.preventDefault();
                const files = event.dataTransfer?.files;
                if (!files?.length || !String(files[0].name || "").toLocaleLowerCase("pl-PL").endsWith(".docx")) return;
                input.files = files;
                input.dispatchEvent(new Event("change", {bubbles: true}));
            });
        });
    }

    async function copyText(value) {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(value);
            return;
        }
        const fallback = document.createElement("textarea");
        fallback.value = value;
        fallback.setAttribute("readonly", "");
        fallback.style.position = "fixed";
        fallback.style.opacity = "0";
        document.body.append(fallback);
        fallback.select();
        const copied = document.execCommand("copy");
        fallback.remove();
        if (!copied) throw new Error("copy_failed");
    }

    function initializeVariableCatalogs() {
        document.querySelectorAll("[data-document-variable-catalog]").forEach((catalog) => {
            const search = catalog.querySelector("[data-document-variable-search]");
            const status = catalog.querySelector("[data-document-variable-copy-status]");
            const empty = catalog.querySelector("[data-document-variable-empty]");
            let statusTimer = null;
            const filter = () => {
                const query = String(search?.value || "").trim().toLocaleLowerCase("pl-PL");
                let visibleCount = 0;
                catalog.querySelectorAll("[data-document-variable-card]").forEach((item) => {
                    const visible = !query || String(item.dataset.variableSearchText || "").toLocaleLowerCase("pl-PL").includes(query);
                    item.hidden = !visible;
                    if (visible) visibleCount += 1;
                });
                catalog.querySelectorAll("[data-document-variable-category]").forEach((category) => {
                    category.hidden = !category.querySelector("[data-document-variable-card]:not([hidden])");
                });
                if (empty) empty.hidden = visibleCount > 0;
            };
            search?.addEventListener("input", filter);
            catalog.addEventListener("click", async (event) => {
                const item = event.target.closest("[data-document-variable-card]");
                if (!item || !catalog.contains(item)) return;
                const value = item.dataset.copyVariable || "";
                try {
                    await copyText(value);
                    item.classList.add("is-copied");
                    if (status) status.textContent = `Skopiowano: ${value}`;
                    window.clearTimeout(statusTimer);
                    statusTimer = window.setTimeout(() => {
                        item.classList.remove("is-copied");
                        if (status) status.textContent = "";
                    }, 1400);
                } catch (_) {
                    if (status) status.textContent = "Nie udało się skopiować zmiennej. Zaznacz ją ręcznie.";
                }
            });
            filter();
        });
    }

    function initializeBuilder(root) {
        const documentType = root.dataset.documentType || "agreement";
        const initialNode = root.querySelector("[data-agreement-builder-initial]");
        const hiddenJson = root.querySelector("[data-agreement-builder-json]");
        const variables = parseJson(root.querySelector("[data-agreement-builder-variables]"), []);
        const submissions = parseJson(root.querySelector("[data-agreement-builder-submissions]"), []);
        const criteriaVariables = variables.filter((item) => item.type === "criterion");
        const variableNames = new Set(variables.filter((item) => item.type !== "criterion").map((item) => item.name));
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
        const draftChoice = root.querySelector("[data-builder-draft-choice]");
        const csrfToken = document.querySelector('input[name="csrf_token"]')?.value || "";
        const formId = Number(root.dataset.formId);
        const localKey = root.dataset.storageKey || `document-builder:${documentType}:form:${formId}:v${BUILDER_SCHEMA_VERSION}`;
        const legacyLocalKey = root.dataset.legacyStorageKey || `document-builder-${documentType}-draft-${formId}`;
        const closeDialog = root.querySelector("[data-builder-close-dialog]");
        const uiStorageKey = `${localKey}:ui`;
        const backendIsNew = root.dataset.backendIsNew === "true";
        const diagnosticsEnabled = root.dataset.debug === "true";
        let backendUpdatedAt = root.dataset.backendUpdatedAt || "";
        const embeddedBackendDocument = parseJson(initialNode, {version: 1, document_type: documentType, blocks: []});
        let documentModel = embeddedBackendDocument;
        let pendingLocalDraft = null;
        let activeEditable = null;
        let previewTimer = null;
        let historyTimer = null;
        let previewController = null;
        let history = [];
        let historyIndex = -1;
        let activeSelection = null;
        let savedTextSelection = null;
        let activeBlockIndex = -1;
        let pendingPreviewScroll = {left: 0, top: 0};
        let resetScrollOnNextPreview = false;
        let blockedPreviewFingerprint = null;
        let previewRequestGeneration = 0;
        const builderState = {
            viewMode: "split",
            variablesVisible: true,
            workspaceSizeMode: "balanced",
            previewZoom: 0.8,
            fitWidth: false,
            dirty: false,
        };

        function debugEvent(eventName, details = {}) {
            if (!diagnosticsEnabled) return;
            console.debug(`[document-builder] ${eventName}`, {documentType, formId, ...details});
        }

        try {
            const savedUi = JSON.parse(localStorage.getItem(uiStorageKey) || "null");
            if (["split", "editor", "preview"].includes(savedUi?.viewMode)) builderState.viewMode = savedUi.viewMode;
            if (typeof savedUi?.variablesVisible === "boolean") builderState.variablesVisible = savedUi.variablesVisible;
            if (["balanced", "editor-wide", "preview-wide"].includes(savedUi?.workspaceSizeMode)) builderState.workspaceSizeMode = savedUi.workspaceSizeMode;
            if (Number.isFinite(savedUi?.previewZoom)) builderState.previewZoom = Math.max(0.5, Math.min(savedUi.previewZoom, 1.5));
            if (typeof savedUi?.fitWidth === "boolean") builderState.fitWidth = savedUi.fitWidth;
        } catch (_) {}

        const defaultRun = (text, bold = false) => ({text, bold, italic: false, underline: false});
        const blockDefaults = (type) => ({
            heading: {type: "heading", level: 2, format: {alignment: "left"}, content: "Nowy nagłówek", runs: [defaultRun("Nowy nagłówek", true)]},
            paragraph: {type: "paragraph", format: {alignment: "left"}, content: "Nowy akapit", runs: [defaultRun("Nowy akapit")]},
            agreement_section: {type: "agreement_section", number: "§ 1.", title: "Tytuł paragrafu"},
            ordered_list: {type: "ordered_list", items: [{content: "Pierwszy punkt", runs: [defaultRun("Pierwszy punkt")], level: 0}], list_styles: defaultListStyles()},
            bullet_list: {type: "bullet_list", items: [{content: "Pierwszy punkt", runs: [defaultRun("Pierwszy punkt")], level: 0}]},
            table: {type: "table", header: true, rows: [["Nagłówek 1", "Nagłówek 2"], ["Treść", "Treść"]]},
            training_table: {type: "training_table", scope: "selected_trainings", columns: ["index", "name", "price"], show_total: true},
            participant_data: {type: "participant_data", fields: ["participant_name", "pesel", "participant_address", "telefon"]},
            signatures: {type: "signatures", left_label: "Beneficjent", right_label: "Uczestnik projektu"},
            form_field: {type: "form_field", field: variables.find((item) => item.category === "Pola formularza" && !item.name.endsWith("_display") && !item.name.endsWith("_yes_no"))?.name || "", label: "Pole formularza", display: "display"},
            participant_address: {type: "participant_address"},
            participant_contact: {type: "participant_contact", fields: ["email", "telefon"]},
            statement: {type: "statement", format: {alignment: "justify"}, content: "Treść oświadczenia", runs: [defaultRun("Treść oświadczenia")]},
            participant_signature: {type: "participant_signature", label: "Czytelny podpis uczestnika"},
            criteria_table: {type: "criteria_table", criteria: [], show_number: true, show_header: true},
            project_info: {type: "project_info", fields: ["project_name", "project_number", "project_program", "funding_source"], show_logo: false},
            page_break: {type: "page_break"},
        }[type]);

        const listMarkerLabels = [
            ["decimal-dot", "1."],
            ["decimal-paren", "1)"],
            ["decimal-compound", "1.1"],
            ["alpha-paren", "a)"],
            ["alpha-dot", "a."],
            ["lower-roman-dot", "i."],
            ["upper-roman-dot", "I."],
        ];

        function defaultListStyles() {
            return [
                {level: 0, marker: "decimal-dot", indent_mm: 0},
                {level: 1, marker: "decimal-compound", indent_mm: 7},
                {level: 2, marker: "alpha-paren", indent_mm: 14},
                {level: 3, marker: "lower-roman-dot", indent_mm: 21},
            ];
        }

        function normalizeListStyles(value) {
            const defaults = defaultListStyles();
            const source = Array.isArray(value) ? value : [];
            return defaults.map((fallback, level) => {
                const raw = source.find((item) => Number(item?.level) === level) || {};
                const marker = listMarkerLabels.some(([name]) => name === raw.marker) ? raw.marker : fallback.marker;
                const requestedIndent = Number(raw.indent_mm);
                return {
                    level,
                    marker,
                    indent_mm: Number.isFinite(requestedIndent) ? Math.max(0, Math.min(requestedIndent, 60)) : fallback.indent_mm,
                };
            });
        }

        function textBlockFormat(block) {
            const textBlock = ["heading", "paragraph", "statement"].includes(block?.type);
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

        function normalizeRuns(value, fallbackText = "", fallbackStyle = {}) {
            const source = Array.isArray(value) ? value : [{text: fallbackText, ...fallbackStyle}];
            const result = [];
            source.forEach((item) => {
                if (!item || typeof item !== "object") return;
                const run = {
                    text: String(item.text ?? ""),
                    bold: item.bold === true,
                    italic: item.italic === true,
                    underline: item.underline === true,
                };
                if (!run.text) return;
                const previous = result.at(-1);
                if (previous && ["bold", "italic", "underline"].every((name) => previous[name] === run[name])) previous.text += run.text;
                else result.push(run);
            });
            return result;
        }

        function legacyHtmlToRuns(value, fallbackStyle = {}) {
            const template = document.createElement("template");
            template.innerHTML = String(value ?? "");
            const runs = [];
            const walk = (node, style) => {
                if (node.nodeType === Node.TEXT_NODE) {
                    normalizeRuns([{text: node.nodeValue || "", ...style}]).forEach((run) => {
                        const previous = runs.at(-1);
                        if (previous && ["bold", "italic", "underline"].every((name) => previous[name] === run[name])) previous.text += run.text;
                        else runs.push(run);
                    });
                    return;
                }
                if (node.nodeType !== Node.ELEMENT_NODE) return;
                const tag = node.tagName.toLowerCase();
                if (tag === "br") { runs.push({text: "\n", ...style}); return; }
                const next = {
                    bold: style.bold || ["strong", "b"].includes(tag),
                    italic: style.italic || ["em", "i"].includes(tag),
                    underline: style.underline || tag === "u" || node.classList.contains("document-inline-underline") || node.classList.contains("document-text-underline"),
                };
                node.childNodes.forEach((child) => walk(child, next));
            };
            template.content.childNodes.forEach((node) => walk(node, {bold: fallbackStyle.bold === true, italic: fallbackStyle.italic === true, underline: fallbackStyle.underline === true}));
            return normalizeRuns(runs);
        }

        function normalizeHolder(holder, fallbackStyle = {}) {
            if (!holder || typeof holder !== "object") return;
            holder.runs = Array.isArray(holder.runs)
                ? normalizeRuns(holder.runs)
                : legacyHtmlToRuns(holder.content || "", fallbackStyle);
            holder.content = holder.runs.map((run) => run.text).join("");
        }

        function normalizeDocumentModel(value) {
            const model = value && typeof value === "object" ? clone(value) : {version: 1, blocks: []};
            model.version = 1;
            model.document_type = documentType;
            model.blocks = Array.isArray(model.blocks) ? model.blocks : [];
            model.blocks.forEach((block) => {
                const formatting = textBlockFormat(block);
                if (formatting) {
                    normalizeHolder(block, formatting);
                    block.format = {alignment: formatting.alignment};
                    delete block.alignment;
                }
                if (["ordered_list", "bullet_list"].includes(block.type)) {
                    let previousLevel = 0;
                    block.items = (Array.isArray(block.items) ? block.items : []).map((rawItem, itemIndex) => {
                        const item = rawItem && typeof rawItem === "object" ? rawItem : {content: String(rawItem ?? "")};
                        normalizeHolder(item);
                        const requestedLevel = Math.max(0, Math.min(Number.parseInt(item.level, 10) || 0, 3));
                        item.level = itemIndex === 0 ? 0 : Math.min(requestedLevel, previousLevel + 1);
                        previousLevel = item.level;
                        return item;
                    });
                    if (block.type === "ordered_list") block.list_styles = normalizeListStyles(block.list_styles);
                }
                if (block.type === "table") {
                    block.rows = (Array.isArray(block.rows) ? block.rows : []).map((row) => (Array.isArray(row) ? row : []).map((cell) => {
                        const holder = cell && typeof cell === "object" ? cell : {content: String(cell ?? "")};
                        normalizeHolder(holder);
                        return holder;
                    }));
                }
                if (block.type === "criteria_table") {
                    const used = new Set();
                    block.criteria = (Array.isArray(block.criteria) ? block.criteria : []).flatMap((item) => {
                        const fieldKey = String(item?.field_key ?? item ?? "").trim();
                        if (!fieldKey || used.has(fieldKey)) return [];
                        used.add(fieldKey);
                        return [{field_key: fieldKey}];
                    });
                    block.show_number = block.show_number !== false;
                    block.show_header = block.show_header !== false;
                }
                delete block.style;
            });
            return model;
        }

        function storageRemove(key) {
            if (!key) return;
            try { localStorage.removeItem(key); } catch (_) {}
        }

        function storageWrite(key, value) {
            if (!key) return false;
            try {
                localStorage.setItem(key, JSON.stringify(value));
                return true;
            } catch (_) {
                return false;
            }
        }

        function timestampValue(value) {
            const parsed = Date.parse(String(value || ""));
            return Number.isFinite(parsed) ? parsed : null;
        }

        function holderHasText(holder) {
            if (!holder || typeof holder !== "object") return false;
            if (Array.isArray(holder.runs)) return holder.runs.some((run) => String(run?.text || "").trim());
            return Boolean(String(holder.content || "").trim());
        }

        function blockHasRealContent(block) {
            if (!block || typeof block !== "object") return false;
            if (["heading", "paragraph", "statement"].includes(block.type)) return holderHasText(block);
            if (block.type === "agreement_section") return Boolean(String(block.number || block.title || "").trim());
            if (["ordered_list", "bullet_list"].includes(block.type)) return Array.isArray(block.items) && block.items.some(holderHasText);
            if (block.type === "table") return Array.isArray(block.rows) && block.rows.some((row) => Array.isArray(row) && row.some((cell) => typeof cell === "object" ? holderHasText(cell) : Boolean(String(cell || "").trim())));
            if (block.type === "training_table") return Array.isArray(block.columns) && block.columns.length > 0;
            if (["participant_data", "participant_contact", "project_info"].includes(block.type)) return Array.isArray(block.fields) && block.fields.length > 0;
            if (block.type === "signatures") return Boolean(String(block.left_label || block.right_label || "").trim());
            if (block.type === "form_field") return Boolean(String(block.field || "").trim());
            if (block.type === "criteria_table") return Array.isArray(block.criteria) && block.criteria.length > 0;
            if (block.type === "participant_signature") return Boolean(String(block.label || "").trim());
            return block.type === "participant_address";
        }

        function documentHasRealContent(value) {
            if (!value || !Array.isArray(value.blocks) || !value.blocks.length) return false;
            const containsEmptyParagraphRuns = value.blocks.some((block) => (
                block?.type === "paragraph"
                && Array.isArray(block.runs)
                && !block.runs.some((run) => String(run?.text || "").trim())
            ));
            return !containsEmptyParagraphRuns && value.blocks.some(blockHasRealContent);
        }

        function validatedDraftEnvelope(value, {legacy = false} = {}) {
            if (!value || typeof value !== "object") return null;
            const envelope = legacy
                ? {
                    schemaVersion: BUILDER_SCHEMA_VERSION,
                    formId,
                    documentType,
                    savedAt: value.savedAt,
                    document: value.document,
                }
                : value;
            if (Number(envelope.schemaVersion) !== BUILDER_SCHEMA_VERSION) return null;
            if (Number(envelope.formId) !== formId || envelope.documentType !== documentType) return null;
            if (timestampValue(envelope.savedAt) === null) return null;
            if (Number(envelope.document?.version) !== BUILDER_SCHEMA_VERSION) return null;
            if (envelope.document?.document_type && envelope.document.document_type !== documentType) return null;
            if (!documentHasRealContent(envelope.document)) return null;
            return {
                schemaVersion: BUILDER_SCHEMA_VERSION,
                formId,
                documentType,
                savedAt: envelope.savedAt,
                document: normalizeDocumentModel(envelope.document),
            };
        }

        function readDraft(key, options = {}) {
            let raw = null;
            try { raw = localStorage.getItem(key); } catch (_) { return null; }
            if (!raw) return null;
            let parsed = null;
            try { parsed = JSON.parse(raw); } catch (_) {
                debugEvent("local draft ignored", {format: options.legacy ? "legacy" : "current", reason: "invalid-json"});
                storageRemove(key);
                return null;
            }
            const draft = validatedDraftEnvelope(parsed, options);
            if (!draft) {
                debugEvent("local draft ignored", {format: options.legacy ? "legacy" : "current", reason: "invalid-or-empty"});
                storageRemove(key);
            }
            return draft;
        }

        function loadLocalDraft() {
            let draft = readDraft(localKey);
            if (legacyLocalKey && legacyLocalKey !== localKey) {
                const legacyDraft = draft ? null : readDraft(legacyLocalKey, {legacy: true});
                storageRemove(legacyLocalKey);
                if (legacyDraft) {
                    storageWrite(localKey, legacyDraft);
                    draft = legacyDraft;
                }
            }
            if (!draft) return null;
            const savedAt = timestampValue(draft.savedAt);
            const backendTime = timestampValue(backendUpdatedAt);
            const isNewer = backendIsNew ? true : backendTime !== null && savedAt > backendTime;
            if (!isNewer) {
                debugEvent("local draft ignored", {format: "current", reason: "not-newer"});
                storageRemove(localKey);
                return null;
            }
            return draft;
        }

        function initialBackendStatus() {
            return backendIsNew ? "Utworzono nowy szablon." : "Wczytano zapisany szablon.";
        }

        function hideDraftChoice() {
            if (draftChoice) draftChoice.hidden = true;
        }

        function resetHistory() {
            history = [JSON.stringify(documentModel)];
            historyIndex = 0;
            refreshControls();
        }

        function blockFormatClasses(block) {
            const formatting = textBlockFormat(block);
            if (!formatting) return "";
            return [
                `is-align-${formatting.alignment}`,
            ].filter(Boolean).join(" ");
        }

        function conditionEditor(block, index) {
            const condition = block.condition || {};
            const options = ['<option value="">Bez warunku</option>', ...variables.filter((item) => !["html", "collection", "criterion"].includes(item.type)).map((item) => `<option value="${escapeHtml(item.name)}" ${condition.variable === item.name ? "selected" : ""}>${escapeHtml(item.label)}</option>`)].join("");
            return `<details class="agreement-builder-block__condition"><summary>Warunek wyświetlania</summary><div><select data-builder-condition-variable data-index="${index}">${options}</select><select data-builder-condition-operator data-index="${index}"><option value="not_empty" ${condition.operator === "not_empty" ? "selected" : ""}>ma wartość</option><option value="empty" ${condition.operator === "empty" ? "selected" : ""}>jest puste</option><option value="equals" ${condition.operator === "equals" ? "selected" : ""}>równa się</option><option value="not_equals" ${condition.operator === "not_equals" ? "selected" : ""}>nie równa się</option></select><input data-builder-condition-value data-index="${index}" value="${escapeHtml(condition.value || "")}" placeholder="Wartość" ${["equals", "not_equals"].includes(condition.operator) ? "" : "hidden"}></div></details>`;
        }

        function runsMarkup(holder) {
            return normalizeRuns(holder?.runs, holder?.content || "").map((run, runIndex) => `<span class="document-builder-run${run.bold ? " is-bold" : ""}${run.italic ? " is-italic" : ""}${run.underline ? " is-underline" : ""}" data-builder-run="${runIndex}" data-bold="${run.bold}" data-italic="${run.italic}" data-underline="${run.underline}">${escapeHtml(run.text)}</span>`).join("");
        }

        function editable(value, field, index, extra = "") {
            const rich = value && typeof value === "object" && Array.isArray(value.runs);
            const content = rich ? runsMarkup(value) : escapeHtml(value || "");
            return `<div class="agreement-builder-block__editable" contenteditable="true" role="textbox" data-builder-field="${field}" data-index="${index}" data-builder-editor-key="block-${index}-${field}" ${extra}>${content}</div>`;
        }

        function alphaNumber(value) {
            let number = Math.max(1, value);
            let result = "";
            while (number) {
                number -= 1;
                result = String.fromCharCode(97 + (number % 26)) + result;
                number = Math.floor(number / 26);
            }
            return result;
        }

        function romanNumber(value) {
            let number = Math.max(1, value);
            let result = "";
            [[1000, "M"], [900, "CM"], [500, "D"], [400, "CD"], [100, "C"], [90, "XC"], [50, "L"], [40, "XL"], [10, "X"], [9, "IX"], [5, "V"], [4, "IV"], [1, "I"]].forEach(([amount, symbol]) => {
                while (number >= amount) { result += symbol; number -= amount; }
            });
            return result;
        }

        function orderedListMarkers(block) {
            const counters = [0, 0, 0, 0];
            const styles = normalizeListStyles(block.list_styles);
            return (block.items || []).map((item) => {
                const level = Math.max(0, Math.min(Number(item.level) || 0, 3));
                counters[level] += 1;
                counters.fill(0, level + 1);
                const marker = styles[level].marker;
                if (marker === "decimal-paren") return `${counters[level]})`;
                if (marker === "decimal-compound") return counters.slice(0, level + 1).join(".");
                if (marker === "alpha-paren") return `${alphaNumber(counters[level])})`;
                if (marker === "alpha-dot") return `${alphaNumber(counters[level])}.`;
                if (marker === "lower-roman-dot") return `${romanNumber(counters[level]).toLowerCase()}.`;
                if (marker === "upper-roman-dot") return `${romanNumber(counters[level])}.`;
                return `${counters[level]}.`;
            });
        }

        function renderListBlock(block, index) {
            const ordered = block.type === "ordered_list";
            const styles = ordered ? normalizeListStyles(block.list_styles) : defaultListStyles();
            const markers = ordered ? orderedListMarkers(block) : (block.items || []).map(() => "•");
            const items = (block.items || []).map((item, itemIndex) => {
                const level = Math.max(0, Math.min(Number(item.level) || 0, 3));
                const indent = ordered ? styles[level].indent_mm : level * 7;
                return `<li data-level="${level}" style="--builder-list-indent:${indent}mm"><span class="agreement-builder-list__marker" aria-hidden="true">${escapeHtml(markers[itemIndex])}</span><div contenteditable="true" data-builder-list-item data-index="${index}" data-item-index="${itemIndex}" data-builder-editor-key="list-${index}-${itemIndex}">${runsMarkup(item)}</div><button type="button" class="builder-button builder-button--icon builder-button--danger builder-button--compact" aria-label="Usuń punkt" data-builder-remove-item data-index="${index}" data-item-index="${itemIndex}">×</button></li>`;
            }).join("");
            const configuration = ordered ? `<fieldset class="agreement-builder-list-config"><legend>Numeracja i wcięcia poziomów</legend>${styles.map((style) => `<div><strong>Poziom ${style.level + 1}</strong><label>Marker<select data-builder-list-marker data-index="${index}" data-level="${style.level}">${listMarkerLabels.map(([value, label]) => `<option value="${value}" ${style.marker === value ? "selected" : ""}>${label}</option>`).join("")}</select></label><label>Wcięcie (mm)<input type="number" min="0" max="60" step="1" value="${style.indent_mm}" data-builder-list-indent data-index="${index}" data-level="${style.level}"></label></div>`).join("")}</fieldset>` : "";
            return `<${ordered ? "ol" : "ul"} class="agreement-builder-list">${items}</${ordered ? "ol" : "ul"}><button type="button" class="builder-button builder-button--secondary builder-button--compact" data-builder-add-item data-index="${index}">Dodaj punkt</button>${configuration}`;
        }

        function renderCriteriaTableBlock(block, index) {
            const catalog = new Map(criteriaVariables.map((item) => [item.field_key, item]));
            const selected = (block.criteria || []).map((item, criterionIndex) => {
                const criterion = catalog.get(item.field_key);
                const label = criterion?.label || `Nieaktywne kryterium: ${item.field_key}`;
                return `<li><span><strong>${criterionIndex + 1}.</strong> ${escapeHtml(label)}<code>${escapeHtml(item.field_key)}</code></span><span><button type="button" class="builder-button builder-button--icon builder-button--compact" data-builder-move-criterion="up" data-index="${index}" data-criterion-index="${criterionIndex}" aria-label="Przesuń kryterium w górę">↑</button><button type="button" class="builder-button builder-button--icon builder-button--compact" data-builder-move-criterion="down" data-index="${index}" data-criterion-index="${criterionIndex}" aria-label="Przesuń kryterium w dół">↓</button><button type="button" class="builder-button builder-button--danger builder-button--compact" data-builder-remove-criterion data-index="${index}" data-criterion-index="${criterionIndex}">Usuń</button></span></li>`;
            }).join("");
            const selectedKeys = new Set((block.criteria || []).map((item) => item.field_key));
            const available = criteriaVariables.filter((item) => !selectedKeys.has(item.field_key));
            const options = available.length
                ? available.map((item) => `<option value="${escapeHtml(item.field_key)}">${escapeHtml(item.label)}</option>`).join("")
                : '<option value="">Brak kolejnych kryteriów</option>';
            return `<fieldset class="agreement-builder-criteria-config"><legend>Tabela kryteriów kwalifikacyjnych</legend><label><input type="checkbox" data-builder-field="show_number" data-index="${index}" ${block.show_number ? "checked" : ""}> Kolumna Lp.</label><label><input type="checkbox" data-builder-field="show_header" data-index="${index}" ${block.show_header ? "checked" : ""}> Wiersz nagłówka</label><ol class="agreement-builder-criteria-list">${selected || "<li>Nie wybrano kryteriów.</li>"}</ol><div><label>Dodaj kryterium<select data-builder-criterion-select data-index="${index}">${options}</select></label><button type="button" class="builder-button builder-button--secondary builder-button--compact" data-builder-add-criterion data-index="${index}" ${available.length ? "" : "disabled"}>Dodaj</button></div><p class="workflow-help">Lista pochodzi z aktywnych warunków kwalifikacyjnych formularza. W dokumencie zapisywane są tylko klucze pól.</p></fieldset>`;
        }

        function renderBlock(block, index) {
            let body = "";
            if (block.type === "heading") {
                body = `<label>Poziom <select data-builder-field="level" data-index="${index}"><option value="1" ${block.level === 1 ? "selected" : ""}>H1</option><option value="2" ${block.level === 2 ? "selected" : ""}>H2</option><option value="3" ${block.level === 3 ? "selected" : ""}>H3</option></select></label>${editable(block, "content", index)}`;
            } else if (["paragraph", "statement"].includes(block.type)) {
                body = editable(block, "content", index);
            } else if (block.type === "agreement_section") {
                body = `<div class="agreement-builder-block__section">${editable(block.number, "number", index)}${editable(block.title, "title", index)}</div>`;
            } else if (["ordered_list", "bullet_list"].includes(block.type)) {
                body = renderListBlock(block, index);
            } else if (block.type === "table") {
                body = `<table><tbody>${(block.rows || []).map((row, rowIndex) => `<tr>${row.map((cell, cellIndex) => `<td contenteditable="true" data-builder-table-cell data-index="${index}" data-row-index="${rowIndex}" data-cell-index="${cellIndex}" data-builder-editor-key="cell-${index}-${rowIndex}-${cellIndex}">${runsMarkup(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table><button type="button" class="builder-button builder-button--secondary builder-button--compact" data-builder-add-row data-index="${index}">Dodaj wiersz</button>`;
            } else if (block.type === "training_table") {
                const columnOptions = [["index", "Lp."], ["name", "Nazwa szkolenia"], ["price", "Cena"], ["date", "Termin"], ["location", "Lokalizacja"]];
                body = `<fieldset><legend>Kolumny tabeli</legend>${columnOptions.map(([value, label]) => `<label><input type="checkbox" data-builder-array="columns" data-index="${index}" value="${value}" ${(block.columns || []).includes(value) ? "checked" : ""}> ${label}</label>`).join("")}</fieldset><label>Zakres <select data-builder-field="scope" data-index="${index}"><option value="selected_trainings" ${block.scope === "selected_trainings" ? "selected" : ""}>Bieżące szkolenie</option><option value="all_selected_trainings" ${block.scope === "all_selected_trainings" ? "selected" : ""}>Wszystkie wybrane</option><option value="locked_trainings" ${block.scope === "locked_trainings" ? "selected" : ""}>Zablokowane</option></select></label><label><input type="checkbox" data-builder-field="show_total" data-index="${index}" ${block.show_total ? "checked" : ""}> Pokaż sumę</label>`;
            } else if (block.type === "participant_data") {
                const choices = [["participant_name", "Imię i nazwisko"], ["pesel", "PESEL"], ["participant_address", "Adres"], ["telefon", "Telefon"], ["email", "E-mail"], ["data_urodzenia", "Data urodzenia"]];
                body = `<fieldset><legend>Dane uczestnika</legend>${choices.map(([value, label]) => `<label><input type="checkbox" data-builder-array="fields" data-index="${index}" value="${value}" ${(block.fields || []).includes(value) ? "checked" : ""}> ${label}</label>`).join("")}</fieldset>`;
            } else if (block.type === "signatures") {
                body = `<div class="agreement-builder-block__signature-config"><label>Lewa strona<input data-builder-field="left_label" data-index="${index}" value="${escapeHtml(block.left_label || "")}"></label><label>Prawa strona<input data-builder-field="right_label" data-index="${index}" value="${escapeHtml(block.right_label || "")}"></label></div>`;
            } else if (block.type === "form_field") {
                const fieldVariables = variables.filter((item) => item.category === "Pola formularza" && !item.name.endsWith("_display") && !item.name.endsWith("_yes_no"));
                const selectedField = fieldVariables.find((item) => item.name === block.field);
                const booleanField = ["checkbox", "boolean", "bool"].includes(String(selectedField?.type || "").toLowerCase());
                body = `<div class="agreement-builder-block__field-config"><label>Pole<select data-builder-field="field" data-index="${index}">${fieldVariables.map((item) => `<option value="${escapeHtml(item.name)}" ${block.field === item.name ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}</select></label><label>Etykieta<input data-builder-field="label" data-index="${index}" value="${escapeHtml(block.label || "")}"></label><label>Prezentacja<select data-builder-field="display" data-index="${index}"><option value="display" ${block.display === "display" ? "selected" : ""}>Czytelna</option><option value="value" ${block.display === "value" ? "selected" : ""}>Surowa</option><option value="yes_no" ${block.display === "yes_no" ? "selected" : ""} ${booleanField ? "" : "disabled"}>Tak / Nie (pole logiczne)</option></select></label></div>`;
            } else if (block.type === "participant_address") {
                body = '<p class="workflow-help">Gotowy adres uczestnika z pól formularza.</p>';
            } else if (block.type === "participant_contact") {
                const choices = [["email", "E-mail"], ["telefon", "Telefon"]];
                body = `<fieldset><legend>Dane kontaktowe</legend>${choices.map(([value, label]) => `<label><input type="checkbox" data-builder-array="fields" data-index="${index}" value="${value}" ${(block.fields || []).includes(value) ? "checked" : ""}> ${label}</label>`).join("")}</fieldset>`;
            } else if (block.type === "participant_signature") {
                body = `<label>Etykieta podpisu<input data-builder-field="label" data-index="${index}" value="${escapeHtml(block.label || "")}"></label>`;
            } else if (block.type === "criteria_table") {
                body = renderCriteriaTableBlock(block, index);
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

        function render({restoreSelection = null} = {}) {
            documentModel = normalizeDocumentModel(documentModel);
            const importButton = root.dataset.importUrl
                ? `<button type="submit" class="builder-button builder-button--secondary" formaction="${escapeHtml(root.dataset.importUrl)}" formmethod="post" formnovalidate>Importuj Word</button>`
                : "";
            blocksNode.innerHTML = documentModel.blocks.length
                ? documentModel.blocks.map(renderBlock).join("")
                : backendIsNew
                    ? `<div data-builder-empty-state role="status"><strong>Ten dokument nie zawiera jeszcze bloków.</strong><p>Dodaj pierwszy element albo zaimportuj istniejący szablon Word.</p><div><button type="button" class="builder-button builder-button--primary" data-builder-add="paragraph">Dodaj akapit</button><button type="button" class="builder-button builder-button--secondary" data-builder-add="heading">Dodaj nagłówek</button>${importButton}</div></div>`
                    : '<div data-builder-empty-state role="alert"><strong>Zapisany szablon nie zawiera bloków.</strong><p>Odśwież stronę lub skontaktuj się z administratorem.</p></div>';
            hiddenJson.value = JSON.stringify(documentModel);
            activeEditable = null;
            if (restoreSelection) restoreEditableSelection(restoreSelection);
            else { activeSelection = null; savedTextSelection = null; }
            refreshControls();
        }

        function holderForEditable(target) {
            const block = documentModel.blocks[Number(target?.dataset.index)];
            if (!block || !target) return null;
            if (target.matches("[data-builder-list-item]")) return block.items?.[Number(target.dataset.itemIndex)] || null;
            if (target.matches("[data-builder-table-cell]")) return block.rows?.[Number(target.dataset.rowIndex)]?.[Number(target.dataset.cellIndex)] || null;
            if (target.dataset.builderField === "content" && ["heading", "paragraph", "statement"].includes(block.type)) return block;
            return null;
        }

        function runsFromEditable(target) {
            const result = [];
            const append = (text, style) => {
                normalizeRuns([{text, ...style}]).forEach((run) => {
                    const previous = result.at(-1);
                    if (previous && ["bold", "italic", "underline"].every((name) => previous[name] === run[name])) previous.text += run.text;
                    else result.push(run);
                });
            };
            const walk = (node, style) => {
                if (node.nodeType === Node.TEXT_NODE) { append(node.nodeValue || "", style); return; }
                if (node.nodeType !== Node.ELEMENT_NODE) return;
                const tag = node.tagName.toLowerCase();
                if (tag === "br") { append("\n", style); return; }
                const next = {
                    bold: style.bold || ["strong", "b"].includes(tag) || node.dataset.bold === "true",
                    italic: style.italic || ["em", "i"].includes(tag) || node.dataset.italic === "true",
                    underline: style.underline || tag === "u" || node.dataset.underline === "true",
                };
                node.childNodes.forEach((child) => walk(child, next));
                if (["div", "p"].includes(tag) && node !== target && node.nextSibling) append("\n", next);
            };
            target.childNodes.forEach((node) => walk(node, {bold: false, italic: false, underline: false}));
            return normalizeRuns(result);
        }

        function syncEditableToModel(target) {
            const holder = holderForEditable(target);
            if (holder) {
                holder.runs = runsFromEditable(target);
                holder.content = holder.runs.map((run) => run.text).join("");
                return;
            }
            const block = documentModel.blocks[Number(target.dataset.index)];
            if (block && target.dataset.builderField) block[target.dataset.builderField] = target.textContent || "";
        }

        function editorForRange(range) {
            if (!range) return null;
            const container = range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
                ? range.commonAncestorContainer
                : range.commonAncestorContainer.parentElement;
            const target = container?.closest?.('[contenteditable="true"][data-builder-editor-key]');
            if (!target || !blocksNode.contains(target)) return null;
            if (!target.contains(range.startContainer) || !target.contains(range.endContainer)) return null;
            return target;
        }

        function editorContainsRange(range, target = activeEditable) {
            return Boolean(target && blocksNode.contains(target) && editorForRange(range) === target);
        }

        function selectionOffsets(target, range) {
            if (!target || !range || !editorContainsRange(range, target)) return null;
            const beforeStart = document.createRange();
            beforeStart.selectNodeContents(target);
            beforeStart.setEnd(range.startContainer, range.startOffset);
            const beforeEnd = document.createRange();
            beforeEnd.selectNodeContents(target);
            beforeEnd.setEnd(range.endContainer, range.endOffset);
            return {key: target.dataset.builderEditorKey, start: beforeStart.toString().length, end: beforeEnd.toString().length};
        }

        function editableSelection(target = activeEditable) {
            const selection = window.getSelection();
            if (!target || !selection?.rangeCount) return null;
            return selectionOffsets(target, selection.getRangeAt(0));
        }

        function saveTextSelection() {
            const selection = window.getSelection();
            if (!selection?.rangeCount) return false;
            const range = selection.getRangeAt(0);
            const target = editorForRange(range);
            if (!target) return false;
            const article = target.closest("[data-builder-block]");
            const index = Number(article?.dataset.index);
            if (!Number.isInteger(index) || index !== activeBlockIndex) return false;
            const saved = selectionOffsets(target, range);
            if (!saved) return false;
            activeEditable = target;
            activeSelection = saved;
            savedTextSelection = range.cloneRange();
            return true;
        }

        function restoreTextSelection() {
            if (savedTextSelection && editorContainsRange(savedTextSelection)) {
                try {
                    activeEditable.focus({preventScroll: true});
                    const selection = window.getSelection();
                    selection.removeAllRanges();
                    selection.addRange(savedTextSelection);
                    activeSelection = selectionOffsets(activeEditable, savedTextSelection) || activeSelection;
                    return true;
                } catch (_) {
                    savedTextSelection = null;
                }
            }
            if (!activeSelection) return false;
            restoreEditableSelection(activeSelection);
            return Boolean(savedTextSelection);
        }

        function restoreEditableSelection(saved) {
            const target = blocksNode.querySelector(`[data-builder-editor-key="${CSS.escape(saved.key || "")}"]`);
            if (!target) { activeSelection = null; savedTextSelection = null; return; }
            const nodes = [];
            const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) nodes.push(walker.currentNode);
            if (!nodes.length) { const node = document.createTextNode(""); target.append(node); nodes.push(node); }
            const point = (offset) => {
                let remaining = Math.max(0, offset);
                for (const node of nodes) {
                    if (remaining <= node.nodeValue.length) return [node, remaining];
                    remaining -= node.nodeValue.length;
                }
                return [nodes.at(-1), nodes.at(-1).nodeValue.length];
            };
            const [startNode, startOffset] = point(saved.start);
            const [endNode, endOffset] = point(saved.end);
            const range = document.createRange();
            range.setStart(startNode, startOffset);
            range.setEnd(endNode, endOffset);
            target.focus({preventScroll: true});
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            activeEditable = target;
            activeSelection = {...saved};
            savedTextSelection = range.cloneRange();
        }

        function readField(target) {
            const index = Number(target.dataset.index);
            const block = documentModel.blocks[index];
            if (!block) return;
            if (target.matches("[data-builder-list-marker], [data-builder-list-indent]")) {
                block.list_styles = normalizeListStyles(block.list_styles);
                const level = Math.max(0, Math.min(Number(target.dataset.level) || 0, 3));
                if (target.matches("[data-builder-list-marker]")) block.list_styles[level].marker = target.value;
                else block.list_styles[level].indent_mm = Math.max(0, Math.min(Number(target.value) || 0, 60));
            }
            const field = target.dataset.builderField;
            if (field) {
                if (target.type === "checkbox") block[field] = target.checked;
                else if (target.isContentEditable) syncEditableToModel(target);
                else if (field === "level") block[field] = Number(target.value);
                else block[field] = target.value;
                if (field === "field" && block.display === "yes_no") {
                    const selected = variables.find((item) => item.name === block.field);
                    if (!["checkbox", "boolean", "bool"].includes(String(selected?.type || "").toLowerCase())) block.display = "display";
                    render();
                }
            }
            if (target.dataset.builderArray) {
                block[target.dataset.builderArray] = [...root.querySelectorAll(`[data-builder-array="${target.dataset.builderArray}"][data-index="${index}"]:checked`)].map((item) => item.value);
            }
            if (target.matches("[data-builder-list-item], [data-builder-table-cell]")) syncEditableToModel(target);
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
            builderState.dirty = true;
            storageWrite(localKey, {
                schemaVersion: BUILDER_SCHEMA_VERSION,
                formId,
                documentType,
                savedAt: new Date().toISOString(),
                document: documentModel,
            });
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

        function activeListItem() {
            if (!activeEditable?.matches?.("[data-builder-list-item]")) return null;
            const blockIndex = Number(activeEditable.dataset.index);
            const itemIndex = Number(activeEditable.dataset.itemIndex);
            const block = documentModel.blocks[blockIndex];
            if (!["ordered_list", "bullet_list"].includes(block?.type) || !block.items?.[itemIndex]) return null;
            return {block, blockIndex, itemIndex, item: block.items[itemIndex]};
        }

        function changeListItemLevel(direction) {
            const active = activeListItem();
            if (!active) return false;
            const saved = editableSelection(activeEditable) || activeSelection;
            syncEditableToModel(activeEditable);
            const current = Number(active.item.level) || 0;
            const previous = active.itemIndex > 0 ? Number(active.block.items[active.itemIndex - 1].level) || 0 : -1;
            const maximum = active.itemIndex === 0 ? 0 : Math.min(3, previous + 1);
            const requested = direction === "increase" ? current + 1 : current - 1;
            const next = Math.max(0, Math.min(requested, maximum));
            if (next === current) return false;
            active.item.level = next;
            render({restoreSelection: saved});
            changed({checkpointNow: true});
            return true;
        }

        function addCriterion(blockIndex) {
            const select = blocksNode.querySelector(`[data-builder-criterion-select][data-index="${blockIndex}"]`);
            const fieldKey = String(select?.value || "").trim();
            const block = documentModel.blocks[blockIndex];
            if (!fieldKey || block?.type !== "criteria_table") return;
            block.criteria = Array.isArray(block.criteria) ? block.criteria : [];
            if (!block.criteria.some((item) => item.field_key === fieldKey)) block.criteria.push({field_key: fieldKey});
            render();
            changed({checkpointNow: true});
        }

        function moveCriterion(blockIndex, criterionIndex, direction) {
            const block = documentModel.blocks[blockIndex];
            if (block?.type !== "criteria_table") return;
            const targetIndex = direction === "up" ? criterionIndex - 1 : criterionIndex + 1;
            if (targetIndex < 0 || targetIndex >= block.criteria.length) return;
            [block.criteria[criterionIndex], block.criteria[targetIndex]] = [block.criteria[targetIndex], block.criteria[criterionIndex]];
            render();
            changed({checkpointNow: true});
        }

        function refreshFormattingControls() {
            const block = activeTextBlock();
            const formatting = textBlockFormat(block);
            const target = activeEditable && document.contains(activeEditable) ? activeEditable : null;
            const holder = holderForEditable(target);
            const saved = target ? (editableSelection(target) || activeSelection) : activeSelection;
            const inlineState = holder ? inlineFormattingState(holder.runs, saved) : null;
            root.querySelectorAll("[data-builder-format]").forEach((button) => {
                const active = Boolean(inlineState?.[button.dataset.builderFormat]);
                button.disabled = !holder;
                button.classList.toggle("is-active", active);
                button.setAttribute("aria-pressed", String(active));
            });
            root.querySelectorAll("[data-builder-align]").forEach((button) => {
                const active = formatting?.alignment === button.dataset.builderAlign;
                button.disabled = !block;
                button.classList.toggle("is-active", active);
                button.setAttribute("aria-pressed", String(active));
            });
            const activeList = activeListItem();
            root.querySelectorAll("[data-builder-list-level]").forEach((button) => {
                const direction = button.dataset.builderListLevel;
                const current = Number(activeList?.item.level) || 0;
                const previous = activeList && activeList.itemIndex > 0 ? Number(activeList.block.items[activeList.itemIndex - 1].level) || 0 : -1;
                const maximum = activeList?.itemIndex > 0 ? Math.min(3, previous + 1) : 0;
                button.disabled = !activeList || (direction === "decrease" ? current <= 0 : current >= maximum);
            });
        }

        function inlineFormattingState(runs, saved) {
            const normalized = normalizeRuns(runs);
            if (!normalized.length) return {bold: false, italic: false, underline: false};
            let start = saved?.start ?? 0;
            let end = saved?.end ?? start;
            if (start === end) { start = Math.max(0, start - 1); end = Math.max(start + 1, end); }
            let cursor = 0;
            const selected = normalized.filter((run) => {
                const overlaps = cursor < end && cursor + run.text.length > start;
                cursor += run.text.length;
                return overlaps;
            });
            const relevant = selected.length ? selected : normalized.slice(0, 1);
            return Object.fromEntries(["bold", "italic", "underline"].map((name) => [name, relevant.every((run) => run[name] === true)]));
        }

        function applyInlineFormat(name) {
            if (!["bold", "italic", "underline"].includes(name) || !restoreTextSelection()) return;
            const target = activeEditable && document.contains(activeEditable) ? activeEditable : null;
            const holder = holderForEditable(target);
            if (!target || !holder) return;
            const saved = editableSelection(target) || activeSelection;
            if (!saved || saved.start === saved.end) return;
            syncEditableToModel(target);
            const runs = normalizeRuns(holder.runs);
            const total = runs.reduce((sum, run) => sum + run.text.length, 0);
            let start = Math.max(0, Math.min(saved.start, total));
            let end = Math.max(start, Math.min(saved.end, total));
            if (start === end) return;
            const nextValue = !inlineFormattingState(runs, {start, end})[name];
            const result = [];
            let cursor = 0;
            runs.forEach((run) => {
                const runStart = cursor;
                const runEnd = cursor + run.text.length;
                const cutStart = Math.max(runStart, start);
                const cutEnd = Math.min(runEnd, end);
                if (runStart < cutStart) result.push({...run, text: run.text.slice(0, cutStart - runStart)});
                if (cutStart < cutEnd) result.push({...run, [name]: nextValue, text: run.text.slice(cutStart - runStart, cutEnd - runStart)});
                if (cutEnd < runEnd) result.push({...run, text: run.text.slice(cutEnd - runStart)});
                cursor = runEnd;
            });
            holder.runs = normalizeRuns(result);
            holder.content = holder.runs.map((run) => run.text).join("");
            render({restoreSelection: saved});
            changed({checkpointNow: true});
        }

        function alignBlock(alignment) {
            const block = activeTextBlock();
            if (!block || !["left", "center", "right", "justify"].includes(alignment)) return;
            block.format = {alignment};
            render({restoreSelection: activeSelection});
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
            if (!missing.length) return payload?.error || "";
            const backendMessage = String(payload?.error || "").trim();
            return `${backendMessage ? `${backendMessage}\n\n` : ""}Brakujące zmienne:\n${missing.map((name) => `• {{ ${name} }}`).join("\n")}`;
        }

        function setPreviewState(state, detail = "") {
            previewState.dataset.previewState = state;
            if (state === "success") {
                previewState.hidden = true;
                previewCanvas.hidden = false;
                return;
            }
            previewState.hidden = false;
            if (state === "loading") {
                previewState.textContent = "Generowanie podglądu…";
                if (!previewFrame.srcdoc) previewCanvas.hidden = true;
                return;
            }
            if (state === "waiting") {
                previewState.textContent = detail;
                previewCanvas.hidden = true;
                return;
            }
            const generic = "Nie udało się wygenerować podglądu.";
            previewState.textContent = detail ? `${generic}\n\n${detail}` : generic;
            previewCanvas.hidden = true;
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
            if (root.hidden) {
                setPreviewState("waiting", "Podgląd uruchomi się po otwarciu kreatora.");
                return;
            }
            const previewFingerprint = JSON.stringify([documentModel, previewData?.value || "", previewTraining?.value || ""]);
            if (!force && blockedPreviewFingerprint === previewFingerprint) return;
            const requestGeneration = ++previewRequestGeneration;
            previewController?.abort();
            previewController = null;
            pendingPreviewScroll = resetScroll || !previewViewport
                ? {left: 0, top: 0}
                : {left: previewViewport.scrollLeft, top: previewViewport.scrollTop};
            if (previewTraining && previewData?.value && !previewTraining.value) {
                setPreviewState("waiting", "Wybierz konkretne szkolenie.");
                return;
            }
            const controller = new AbortController();
            previewController = controller;
            setPreviewState("loading");
            debugEvent("preview request started", {
                requestGeneration,
                blockCount: documentModel.blocks.length,
                previewMode: previewData?.value ? "submission" : "example",
            });
            try {
                const response = await fetch(root.dataset.previewUrl, {method: "POST", body: requestFormData(), signal: controller.signal, headers: {Accept: "application/json"}});
                const payload = await response.json().catch(() => ({}));
                if (requestGeneration !== previewRequestGeneration) return;
                if (!response.ok || !payload.ok) {
                    debugEvent("preview request failed", {requestGeneration, status: response.status});
                    if (response.status === 422) blockedPreviewFingerprint = previewFingerprint;
                    showErrors(payload, "Nie można wygenerować podglądu.");
                    setPreviewState("error", previewErrorMessage(payload));
                    return;
                }
                blockedPreviewFingerprint = null;
                errorsNode.hidden = true;
                previewFrame.srcdoc = payload.html;
                if (htmlPreview) htmlPreview.value = payload.html;
                setPreviewState("success");
                debugEvent("preview request success", {requestGeneration, status: response.status});
            } catch (error) {
                if (error.name === "AbortError" || requestGeneration !== previewRequestGeneration) return;
                debugEvent("preview request failed", {requestGeneration, status: "network-error"});
                setPreviewState("error", "Sprawdź połączenie i spróbuj ponownie.");
            } finally {
                if (requestGeneration !== previewRequestGeneration) return;
                if (previewController === controller) previewController = null;
                if (previewState.dataset.previewState === "loading") setPreviewState("error");
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
            builderState.dirty = false;
            statusNode.textContent = `Szablon zapisany ${new Date().toLocaleTimeString("pl-PL", {hour: "2-digit", minute: "2-digit"})}.`;
            backendUpdatedAt = payload.updated_at || new Date().toISOString();
            storageRemove(localKey);
            storageRemove(legacyLocalKey);
            hideDraftChoice();
            window.opener?.postMessage({
                type: documentType === "agreement" ? "agreement-builder-saved" : "document-builder-saved",
                formId,
                documentType,
                updatedAt: payload.updated_at || new Date().toISOString(),
                activated: action === "activate",
            }, window.location.origin);
            if (action === "activate") {
                const radio = document.querySelector(`input[name="${documentType === "agreement" ? "contract" : "declaration"}_template_source"][value="builder"]`);
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
            if (!builderState.dirty) { window.close(); return; }
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
            link.href = url; link.download = documentType === "agreement" ? "przykladowa-umowa.pdf" : "przykladowa-deklaracja.pdf"; link.click();
            window.setTimeout(() => URL.revokeObjectURL(url), 1000);
            if (!builderState.dirty) statusNode.textContent = "PDF pobrany";
        }

        function insertVariable(name) {
            if (!variableNames.has(name)) return;
            let target = activeEditable && document.contains(activeEditable) ? activeEditable : blocksNode.querySelector("[contenteditable=true]");
            if (!target) {
                documentModel.blocks.push(blockDefaults("paragraph"));
                documentModel.blocks.at(-1).content = "";
                documentModel.blocks.at(-1).runs = [];
                activeBlockIndex = documentModel.blocks.length - 1;
                render();
                target = blocksNode.querySelector("[data-builder-block]:last-child [contenteditable=true]");
            }
            activeEditable = target;
            const holder = holderForEditable(target);
            if (!holder) return;
            const saved = editableSelection(target) || activeSelection || {key: target.dataset.builderEditorKey, start: holder.content?.length || 0, end: holder.content?.length || 0};
            syncEditableToModel(target);
            const token = `{{ ${name} }}`;
            const runs = normalizeRuns(holder.runs);
            const start = Math.max(0, Math.min(saved.start, holder.content.length));
            const end = Math.max(start, Math.min(saved.end, holder.content.length));
            const styleSource = runAtOffset(runs, start);
            const result = [];
            let cursor = 0;
            let inserted = false;
            runs.forEach((run) => {
                const runStart = cursor;
                const runEnd = cursor + run.text.length;
                if (runStart < start) result.push({...run, text: run.text.slice(0, Math.min(run.text.length, start - runStart))});
                if (!inserted && runEnd >= start) { result.push({text: token, ...styleSource}); inserted = true; }
                if (runEnd > end) result.push({...run, text: run.text.slice(Math.max(0, end - runStart))});
                cursor = runEnd;
            });
            if (!inserted) result.push({text: token, ...styleSource});
            holder.runs = normalizeRuns(result);
            holder.content = holder.runs.map((run) => run.text).join("");
            const after = {...saved, start: start + token.length, end: start + token.length};
            render({restoreSelection: after});
            changed({checkpointNow: true});
            rememberVariable(name);
        }

        function runAtOffset(runs, offset) {
            let cursor = 0;
            for (const run of runs) {
                if (offset <= cursor + run.text.length) return {bold: run.bold, italic: run.italic, underline: run.underline};
                cursor += run.text.length;
            }
            const last = runs.at(-1);
            return {bold: Boolean(last?.bold), italic: Boolean(last?.italic), underline: Boolean(last?.underline)};
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
            saveTextSelection();
            refreshFormattingControls();
        };
        root.addEventListener("focusin", (event) => {
            const article = event.target.closest?.("[data-builder-block]");
            if (article) setActiveBlock(Number(article.dataset.index));
            if (event.target.isContentEditable) { activeEditable = event.target; rememberEditableRange(); }
        });
        root.addEventListener("keyup", rememberEditableRange);
        root.addEventListener("mouseup", rememberEditableRange);
        root.addEventListener("keydown", (event) => {
            if (event.key !== "Tab" || !event.target.matches?.("[data-builder-list-item]")) return;
            event.preventDefault();
            changeListItemLevel(event.shiftKey ? "decrease" : "increase");
        });
        document.addEventListener("selectionchange", rememberEditableRange);
        root.addEventListener("input", (event) => readField(event.target));
        root.addEventListener("change", (event) => readField(event.target));
        root.addEventListener("click", async (event) => {
            if (event.target.closest("[data-builder-draft-restore]") && pendingLocalDraft) {
                documentModel = normalizeDocumentModel(pendingLocalDraft.document);
                pendingLocalDraft = null;
                builderState.dirty = true;
                hideDraftChoice();
                statusNode.textContent = "Przywrócono lokalny szkic";
                render();
                resetHistory();
                schedulePreview({resetScroll: true});
                return;
            }
            if (event.target.closest("[data-builder-draft-discard]")) {
                pendingLocalDraft = null;
                storageRemove(localKey);
                storageRemove(legacyLocalKey);
                hideDraftChoice();
                builderState.dirty = false;
                statusNode.textContent = initialBackendStatus();
                return;
            }
            const selectedArticle = event.target.closest("[data-builder-block]");
            if (selectedArticle) setActiveBlock(Number(selectedArticle.dataset.index));
            const add = event.target.closest("[data-builder-add]");
            if (add) { documentModel.blocks.push(clone(blockDefaults(add.dataset.builderAdd))); activeBlockIndex = documentModel.blocks.length - 1; render(); changed(); return; }
            const remove = event.target.closest("[data-builder-remove]");
            if (remove) { const removed = Number(remove.dataset.index); documentModel.blocks.splice(removed, 1); activeBlockIndex = Math.min(removed, documentModel.blocks.length - 1); render(); changed(); return; }
            const move = event.target.closest("[data-builder-move]");
            if (move) { const from = Number(move.dataset.index); const to = move.dataset.builderMove === "up" ? from - 1 : from + 1; if (to >= 0 && to < documentModel.blocks.length) { [documentModel.blocks[from], documentModel.blocks[to]] = [documentModel.blocks[to], documentModel.blocks[from]]; activeBlockIndex = to; render(); changed(); } return; }
            const listLevel = event.target.closest("[data-builder-list-level]");
            if (listLevel) { changeListItemLevel(listLevel.dataset.builderListLevel); return; }
            const addItem = event.target.closest("[data-builder-add-item]");
            if (addItem) { const block = documentModel.blocks[Number(addItem.dataset.index)]; const level = Number(block.items.at(-1)?.level) || 0; block.items.push({content: "Nowy punkt", runs: [defaultRun("Nowy punkt")], level}); render(); changed(); return; }
            const removeItem = event.target.closest("[data-builder-remove-item]");
            if (removeItem) { documentModel.blocks[Number(removeItem.dataset.index)].items.splice(Number(removeItem.dataset.itemIndex), 1); render(); changed(); return; }
            const addCriterionButton = event.target.closest("[data-builder-add-criterion]");
            if (addCriterionButton) { addCriterion(Number(addCriterionButton.dataset.index)); return; }
            const removeCriterion = event.target.closest("[data-builder-remove-criterion]");
            if (removeCriterion) { const block = documentModel.blocks[Number(removeCriterion.dataset.index)]; block.criteria.splice(Number(removeCriterion.dataset.criterionIndex), 1); render(); changed({checkpointNow: true}); return; }
            const moveCriterionButton = event.target.closest("[data-builder-move-criterion]");
            if (moveCriterionButton) { moveCriterion(Number(moveCriterionButton.dataset.index), Number(moveCriterionButton.dataset.criterionIndex), moveCriterionButton.dataset.builderMoveCriterion); return; }
            const addRow = event.target.closest("[data-builder-add-row]");
            if (addRow) { const table = documentModel.blocks[Number(addRow.dataset.index)]; table.rows.push(Array(table.rows[0]?.length || 2).fill("")); render(); changed(); return; }
            const variable = event.target.closest("[data-builder-insert-variable]");
            if (variable) { insertVariable(variable.dataset.builderInsertVariable); return; }
            const copy = event.target.closest("[data-builder-copy-variable]");
            if (copy) { await navigator.clipboard.writeText(`{{ ${copy.dataset.builderCopyVariable} }}`); if (!builderState.dirty) statusNode.textContent = "Zmienna skopiowana"; return; }
            const format = event.target.closest("[data-builder-format]");
            if (format) { applyInlineFormat(format.dataset.builderFormat); return; }
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
            if (event.target.closest("[data-builder-close-discard]")) { builderState.dirty = false; closeDialog?.close(); window.close(); }
            if (event.target.closest("[data-builder-close-save]")) { if (await save("draft")) { closeDialog?.close(); window.close(); } }
        });

        root.querySelectorAll("[data-builder-format]").forEach((button) => {
            button.addEventListener("mousedown", (event) => event.preventDefault());
        });
        root.addEventListener("mousedown", (event) => {
            if (event.target.closest("[data-builder-align], [data-builder-insert-variable], [data-builder-list-level]")) event.preventDefault();
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
            if (previewTraining) {
                previewTraining.replaceChildren(new Option("Wybierz szkolenie", ""), ...trainings.map((item) => new Option(item.label, item.id)));
                previewTrainingField.hidden = !previewData.value;
                if (trainings.length === 1) previewTraining.value = trainings[0].id;
            }
            schedulePreview({resetScroll: true});
        });
        previewTraining?.addEventListener("change", () => schedulePreview({resetScroll: true}));
        root.addEventListener("agreement-builder-visible", schedulePreview);
        previewFrame?.addEventListener("load", () => {
            if (previewState.dataset.previewState !== "success") return;
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
            if (!builderState.dirty) return;
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

        function initializeDocumentBuilder() {
            documentModel = normalizeDocumentModel(embeddedBackendDocument);
            statusNode.textContent = initialBackendStatus();
            pendingLocalDraft = loadLocalDraft();
            if (pendingLocalDraft) {
                statusNode.textContent = "Znaleziono niezapisany szkic.";
                if (draftChoice) draftChoice.hidden = false;
            } else {
                hideDraftChoice();
            }
            render();
            resetHistory();
            renderRecent();
            if (mobileQuery.matches && builderState.viewMode === "split") builderState.viewMode = "editor";
            applyBuilderLayout({persist: false});
            schedulePreview();
            debugEvent("initialized", {
                source: pendingLocalDraft ? "backend-with-newer-local-draft" : (backendIsNew ? "new" : "backend"),
                blockCount: documentModel.blocks.length,
            });
        }

        try {
            initializeDocumentBuilder();
        } catch (error) {
            debugEvent("initialization failed", {error: error?.name || "Error"});
            statusNode.textContent = "Nie udało się uruchomić kreatora.";
            setPreviewState("error", "Odśwież stronę i spróbuj ponownie.");
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        initializeSourceSwitching();
        initializeLastSavedTimestamps();
        initializeDocxDropzones();
        initializeVariableCatalogs();
        initializePopupButtons();
        document.querySelectorAll("[data-document-builder], [data-agreement-builder]").forEach(initializeBuilder);
    });
})();
