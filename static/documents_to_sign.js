const submissionInput = document.getElementById("submission_id");
const statusBox = document.getElementById("acceptance-status");
const statusTiles = document.getElementById("submission-status-tiles");
const documentsSection = document.getElementById("documents-section");
const generateButton = document.getElementById("generate-button");
const acceptanceSelect = document.getElementById("akceptacja");
const signDocumentsForm = document.getElementById("sign-documents-form");
const processCompletedBox = document.getElementById("process-completed-box");
const instructionWindow = document.getElementById("user-instruction-window");
const instructionTitle = document.getElementById("user-instruction-title");
const formInstructionSection = document.getElementById("form-instruction-section");
const formInstructionContent = document.getElementById("form-instruction-content");
const instructionStagesSection = document.getElementById("instruction-stages-section");
const instructionSteps = document.getElementById("instruction-steps");
const currentStageDescriptionSection = document.getElementById("current-stage-description-section");
const currentStageDescription = document.getElementById("current-stage-description");
const nextActionSection = document.getElementById("next-action-section");
const nextActionContent = document.getElementById("next-action-content");
const instructionMinimizeButton = document.getElementById("user-instruction-minimize");
const instructionCloseButton = document.getElementById("user-instruction-close");
const instructionRestoreButton = document.getElementById("user-instruction-restore");

let timeoutId = null;
let currentInstruction = null;

function normalizeBasePath(value) {
    const path = String(value || "").trim();

    if (!path || path === "/") {
        return "";
    }

    return `/${path.replace(/^\/+|\/+$/g, "")}`;
}

function detectBasePathFromLocation() {
    const pathname = window.location ? window.location.pathname || "" : "";
    const segments = pathname.split("/").filter(Boolean);
    const knownRootRoutes = new Set([
        "additional-fields",
        "admin",
        "agreements",
        "api",
        "declaration",
        "do-podpisania",
        "downloads",
        "upload-declaration-signed",
        "upload-signed",
    ]);

    if (!segments.length || knownRootRoutes.has(segments[0])) {
        return "";
    }

    return normalizeBasePath(segments[0]);
}

function getBasePath() {
    return normalizeBasePath(window.APP_BASE_PATH) || detectBasePathFromLocation();
}

function buildAppUrl(path) {
    let url = String(path || "");

    if (/^[a-z][a-z0-9+.-]*:/i.test(url) || url.startsWith("//")) {
        return url;
    }

    if (!url.startsWith("/")) {
        url = `/${url}`;
    }

    url = url.replace(/\/{2,}/g, "/");

    const basePath = getBasePath();
    if (!basePath || url === basePath || url.startsWith(`${basePath}/`)) {
        return url;
    }

    return `${basePath}${url}`;
}

function buildApiUrl(path) {
    return buildAppUrl(String(path || "").replace(/^\/{2,}/, "/"));
}

function buildAcceptanceStatusUrl(submissionId) {
    const encodedSubmissionId = encodeURIComponent(submissionId);
    const template = submissionInput ? submissionInput.dataset.acceptanceStatusUrlTemplate || "" : "";

    if (template.includes("__SUBMISSION_ID__")) {
        return buildApiUrl(template.replace("__SUBMISSION_ID__", encodedSubmissionId));
    }

    return buildApiUrl(`/api/submissions/${encodedSubmissionId}/acceptance-status`);
}

function isRejectedStatus(data) {
    return Boolean(data.is_rejected);
}

function hideElements(selector) {
    document.querySelectorAll(selector).forEach((element) => {
        element.classList.add("is-hidden");
    });
}

function showElement(element) {
    if (element) {
        element.classList.remove("is-hidden");
    }
}

function hideElement(element) {
    if (element) {
        element.classList.add("is-hidden");
    }
}

function instructionStorageKey(action, submissionId, status) {
    return `instruction_${action}_${submissionId}_${status || "unknown"}`;
}

function readInstructionSession(action, submissionId, status) {
    try {
        return window.sessionStorage ? window.sessionStorage.getItem(instructionStorageKey(action, submissionId, status)) : null;
    } catch (error) {
        return null;
    }
}

function writeInstructionSession(action, submissionId, status, value) {
    try {
        if (window.sessionStorage) {
            window.sessionStorage.setItem(instructionStorageKey(action, submissionId, status), value);
        }
    } catch (error) {
        // The window remains usable when browser storage is disabled.
    }
}

function removeInstructionSession(action, submissionId, status) {
    try {
        if (window.sessionStorage) {
            window.sessionStorage.removeItem(instructionStorageKey(action, submissionId, status));
        }
    } catch (error) {
        // The window remains usable when browser storage is disabled.
    }
}

function hideInstruction() {
    hideElement(instructionWindow);
    hideElement(instructionRestoreButton);
}

function minimizeInstruction() {
    if (!currentInstruction) {
        return;
    }
    writeInstructionSession("minimized", currentInstruction.submissionId, currentInstruction.status, currentInstruction.version);
    hideElement(instructionWindow);
    showElement(instructionRestoreButton);
}

function restoreInstruction() {
    if (!currentInstruction) {
        return;
    }
    removeInstructionSession("minimized", currentInstruction.submissionId, currentInstruction.status);
    hideElement(instructionRestoreButton);
    showElement(instructionWindow);
}

function closeInstruction() {
    if (currentInstruction) {
        writeInstructionSession("closed", currentInstruction.submissionId, currentInstruction.status, currentInstruction.version);
        removeInstructionSession("minimized", currentInstruction.submissionId, currentInstruction.status);
    }
    hideInstruction();
}

function renderInstructionSteps(steps) {
    if (!instructionSteps) {
        return 0;
    }
    instructionSteps.replaceChildren();
    (Array.isArray(steps) ? steps : []).forEach((step) => {
        const item = document.createElement("li");
        item.className = "instruction-step";
        if (step.completed) {
            item.classList.add("instruction-step--completed");
        }
        if (step.current) {
            item.classList.add("instruction-step--current");
            item.setAttribute("aria-current", "step");
        }
        const label = document.createElement(step.current ? "strong" : "span");
        label.textContent = String(step.label || "");
        item.appendChild(label);
        if (step.completed) {
            const completedLabel = document.createElement("span");
            completedLabel.className = "instruction-step__state";
            completedLabel.textContent = " — zakończono";
            item.appendChild(completedLabel);
        } else if (step.current) {
            const currentLabel = document.createElement("span");
            currentLabel.className = "instruction-step__state";
            currentLabel.textContent = " — aktualny etap";
            item.appendChild(currentLabel);
        }
        instructionSteps.appendChild(item);
    });
    return instructionSteps.children.length;
}

function showUserInstruction(data, submissionId) {
    const nested = data.instruction && typeof data.instruction === "object" ? data.instruction : null;
    const instruction = String(nested?.description ?? data.form_instruction ?? "").trim();
    const nextAction = String(nested?.next_action ?? data.next_action ?? "").trim();
    const stageDescription = String(nested?.current_stage_description ?? "").trim();
    const stages = Array.isArray(nested?.stages) ? nested.stages : data.instruction_steps;
    const hasInstruction = nested ? Boolean(nested.has_instruction) : Boolean(instruction || nextAction || (Array.isArray(stages) && stages.length));
    if (!hasInstruction || !submissionId) {
        currentInstruction = null;
        hideInstruction();
        return;
    }

    const status = String(data.process_status || data.normalized_process_status || "unknown");
    const version = String(data.instruction_version || `${status}:${instruction}:${nextAction}`);
    currentInstruction = { submissionId, status, version };
    if (readInstructionSession("closed", submissionId, status) === version) {
        hideInstruction();
        return;
    }

    if (formInstructionContent) {
        formInstructionContent.innerHTML = instruction;
    }
    if (instructionTitle) {
        instructionTitle.textContent = String(nested?.title || "Instrukcja dalszego postępowania");
    }
    if (instruction) {
        showElement(formInstructionSection);
    } else {
        hideElement(formInstructionSection);
    }
    const renderedStages = renderInstructionSteps(stages);
    if (renderedStages) {
        showElement(instructionStagesSection);
    } else {
        hideElement(instructionStagesSection);
    }
    if (currentStageDescription) {
        currentStageDescription.innerHTML = stageDescription;
    }
    if (stageDescription) {
        showElement(currentStageDescriptionSection);
    } else {
        hideElement(currentStageDescriptionSection);
    }
    if (nextActionContent) {
        nextActionContent.innerHTML = nextAction;
    }
    if (nextAction) {
        showElement(nextActionSection);
    } else {
        hideElement(nextActionSection);
    }
    if (readInstructionSession("minimized", submissionId, status) === version) {
        hideElement(instructionWindow);
        showElement(instructionRestoreButton);
        return;
    }
    hideElement(instructionRestoreButton);
    showElement(instructionWindow);
}

function disableGenerateButton() {
    if (!generateButton) {
        return;
    }

    generateButton.disabled = true;
    generateButton.setAttribute("aria-disabled", "true");
}

function hideInitialSigningForm() {
    hideElement(signDocumentsForm);
}

function showProcessCompletedBox() {
    showElement(processCompletedBox);
}

function showCompletedMessage(message) {
    const messageBox = document.getElementById("completed-stage-message");

    if (!messageBox) {
        return;
    }

    messageBox.innerHTML = message;
    showElement(messageBox);
}

function hideDeclarationStage() {
    hideElements('[data-stage="declaration"]');
    hideElements('[data-stage="declaration-upload"]');
}

function hideAgreementStage() {
    hideElements('[data-stage="agreement"]');
    hideElements('[data-stage="agreement-generate"]');
    hideElements('[data-stage="agreement-upload"]');
}

function renderStatusTile({ variant = "neutral", icon = "i", title, description }) {
    if (!statusTiles) {
        return;
    }

    statusTiles.innerHTML = `
        <div class="status-tile status-tile--${variant}">
            <span class="status-tile__icon" aria-hidden="true">${icon}</span>
            <div>
                <p class="status-tile__title">${title}</p>
                <p class="status-tile__description">${description}</p>
            </div>
        </div>
    `;
}

function clearStatusTile() {
    if (statusTiles) {
        statusTiles.innerHTML = "";
    }
}

function renderSubmissionStatus(data) {
    if (!data.exists) {
        renderStatusTile({
            variant: "warning",
            icon: "?",
            title: "Nie znaleziono wniosku",
            description: data.message || "Sprawdź poprawność wpisanego ID wniosku.",
        });
        return;
    }

    if (isRejectedStatus(data)) {
        renderStatusTile({
            variant: "danger",
            icon: "!",
            title: "Wniosek odrzucony przez urzędnika",
            description: data.message || "Dla tego wniosku nie można przejść do podpisywania dokumentów.",
        });
        return;
    }

    if (data.agreement_stage_completed || data.is_final) {
        renderStatusTile({
            variant: "success",
            icon: "✓",
            title: "Proces podpisywania zakończony",
            description: data.message || "Wszystkie wymagane dokumenty zostały poprawnie obsłużone.",
        });
        return;
    }

    if (!data.can_sign_documents) {
        renderStatusTile({
            variant: "warning",
            icon: "…",
            title: "Wniosek oczekuje na akceptację urzędnika",
            description: data.message || "Dokumenty do podpisu będą dostępne dopiero po akceptacji wniosku.",
        });
        return;
    }

    renderStatusTile({
        variant: "success",
        icon: "✓",
        title: "Wniosek zaakceptowany przez urzędnika",
        description: data.message || "Możesz wygenerować dokumenty do podpisu.",
    });
}

function replaceDownloadedCard(trigger) {
    const card = trigger.closest("[data-download-replace-card]");

    if (!card) {
        return;
    }

    const editUrl = trigger.dataset.editUrl;
    const title = trigger.dataset.editTitle || "Edytuj";

    if (!editUrl) {
        return;
    }

    window.setTimeout(() => {
        card.outerHTML = `
            <div class="download-replaced-actions" data-stage="declaration">
                <a class="btn-primary" href="${editUrl}">${title}</a>
            </div>
        `;
    }, 250);
}

function bindDownloadReplacementCards() {
    document.querySelectorAll("[data-download-replace-trigger]").forEach((trigger) => {
        trigger.addEventListener("click", () => replaceDownloadedCard(trigger));
    });
}

function getPdfFiles(dataTransfer) {
    return Array.from(dataTransfer.files || []).filter((file) => file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf"));
}

function updateDropzoneFilename(dropzone, input) {
    const filenameBox = dropzone.querySelector("[data-upload-filename]");
    const filename = input.files && input.files.length ? input.files[0].name : "Nie wybrano pliku";

    dropzone.classList.toggle("has-file", Boolean(input.files && input.files.length));

    if (filenameBox) {
        filenameBox.textContent = filename;
    }
}

function bindUploadDropzones() {
    document.querySelectorAll("[data-upload-dropzone]").forEach((dropzone) => {
        const input = dropzone.querySelector("[data-upload-input]");

        if (!input) {
            return;
        }

        input.addEventListener("change", () => updateDropzoneFilename(dropzone, input));

        ["dragenter", "dragover"].forEach((eventName) => {
            dropzone.addEventListener(eventName, (event) => {
                event.preventDefault();
                event.stopPropagation();
                dropzone.classList.add("is-dragover");
            });
        });

        ["dragleave", "drop"].forEach((eventName) => {
            dropzone.addEventListener(eventName, (event) => {
                event.preventDefault();
                event.stopPropagation();
                dropzone.classList.remove("is-dragover");
            });
        });

        dropzone.addEventListener("drop", (event) => {
            const pdfFiles = getPdfFiles(event.dataTransfer);

            if (!pdfFiles.length) {
                return;
            }

            const transfer = new DataTransfer();
            transfer.items.add(pdfFiles[0]);
            input.files = transfer.files;
            updateDropzoneFilename(dropzone, input);
        });
    });
}

function applyProcessStageVisibility(data) {
    if (isRejectedStatus(data)) {
        hideDeclarationStage();
        hideAgreementStage();
        disableGenerateButton();
        return;
    }

    const agreementIsCompleted = Boolean(data.agreement_stage_completed || data.is_final);
    const declarationIsCompleted = agreementIsCompleted || Boolean(data.declaration_stage_completed);

    if (declarationIsCompleted) {
        hideDeclarationStage();
        showCompletedMessage("<strong>Deklaracja:</strong> etap zakończony poprawnie.");
    }

    if (agreementIsCompleted) {
        hideDeclarationStage();
        hideAgreementStage();
        hideInitialSigningForm();
        disableGenerateButton();
        showCompletedMessage("<strong>Proces podpisywania dokumentów:</strong> zakończony poprawnie.");
        showProcessCompletedBox();
    }
}

function resetState() {
    if (statusBox) {
        statusBox.textContent = "";
    }
    if (documentsSection) {
        hideElement(documentsSection);
    }
    if (generateButton) {
        generateButton.disabled = true;
    }
    if (acceptanceSelect) {
        acceptanceSelect.value = "";
    }
    clearStatusTile();
    currentInstruction = null;
    hideInstruction();
}

async function checkAcceptanceStatus() {
    const submissionId = submissionInput ? submissionInput.value.trim() : "";

    resetState();

    if (!submissionId) {
        renderStatusTile({
            variant: "warning",
            icon: "i",
            title: "Podaj ID wniosku",
            description: "Status wniosku zostanie sprawdzony przed pokazaniem dokumentów do podpisu.",
        });
        return;
    }

    renderStatusTile({
        variant: "warning",
        icon: "…",
        title: "Sprawdzanie statusu wniosku",
        description: "System weryfikuje, czy urzędnik zaakceptował wniosek.",
    });

    try {
        const response = await fetch(buildAcceptanceStatusUrl(submissionId));
        const data = await response.json();

        if (statusBox) {
            statusBox.textContent = data.message || "";
        }
        renderSubmissionStatus(data);
        applyProcessStageVisibility(data);
        showUserInstruction(data, submissionId);

        if (
            data.exists
            && data.can_sign_documents
            && !isRejectedStatus(data)
            && !data.agreement_stage_completed
            && !data.is_final
        ) {
            if (documentsSection) {
                showElement(documentsSection);
            }
            if (generateButton) {
                generateButton.disabled = false;
            }
            if (acceptanceSelect) {
                acceptanceSelect.value = "Tak";
            }
        }
    } catch (error) {
        if (statusBox) {
            statusBox.textContent = "Nie udało się sprawdzić statusu wniosku.";
        }
        renderStatusTile({
            variant: "danger",
            icon: "!",
            title: "Błąd sprawdzania statusu",
            description: "Spróbuj ponownie albo sprawdź połączenie z serwerem.",
        });
        if (documentsSection) {
            hideElement(documentsSection);
        }
        if (generateButton) {
            generateButton.disabled = true;
        }
    }
}

if (submissionInput) {
    submissionInput.addEventListener("input", () => {
        clearTimeout(timeoutId);
        timeoutId = setTimeout(checkAcceptanceStatus, 500);
    });
}

if (instructionMinimizeButton) {
    instructionMinimizeButton.addEventListener("click", minimizeInstruction);
}
if (instructionCloseButton) {
    instructionCloseButton.addEventListener("click", closeInstruction);
}
if (instructionRestoreButton) {
    instructionRestoreButton.addEventListener("click", restoreInstruction);
}

function normalizeAgreementMatchText(value) {
    return String(value || "")
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLowerCase()
        .replace(/\.[^.]+$/, "")
        .replace(/[^a-z0-9]+/g, "");
}

function bindBulkAgreementUpload() {
    const form = document.querySelector("[data-bulk-agreement-upload]");
    if (!form) return;
    const input = form.querySelector("[data-bulk-agreement-files]");
    const rowsHolder = form.querySelector("[data-bulk-agreement-rows]");
    const matches = form.querySelector("[data-bulk-agreement-matches]");
    const submit = form.querySelector("[data-bulk-agreement-submit]");
    const summary = form.querySelector("[data-bulk-agreement-summary]");
    const filename = form.querySelector("[data-upload-filename]");
    let agreements = [];
    let selectedFiles = [];
    try { agreements = JSON.parse(form.querySelector("[data-bulk-agreements]")?.textContent || "[]"); } catch (_) { agreements = []; }
    const availableAgreements = agreements.filter((agreement) => !agreement.signature_valid);

    function suggestedAgreement(file, index, used) {
        const normalizedFile = normalizeAgreementMatchText(file.name);
        let match = availableAgreements.find((agreement) => {
            if (used.has(String(agreement.id))) return false;
            const candidates = [agreement.filename, agreement.number, agreement.training_name].map(normalizeAgreementMatchText).filter(Boolean);
            return candidates.some((candidate) => normalizedFile.includes(candidate) || candidate.includes(normalizedFile));
        });
        if (!match) match = availableAgreements.find((agreement) => !used.has(String(agreement.id))) || availableAgreements[index];
        if (match) used.add(String(match.id));
        return match;
    }

    function renderRows() {
        rowsHolder.replaceChildren();
        const used = new Set();
        selectedFiles.forEach((file, index) => {
            const suggested = suggestedAgreement(file, index, used);
            const row = document.createElement("div"); row.className = "bulk-agreement-row"; row.dataset.bulkAgreementRow = String(index);
            const fileName = document.createElement("span"); fileName.textContent = file.name;
            const select = document.createElement("select"); select.setAttribute("aria-label", `Umowa dla pliku ${file.name}`); select.dataset.bulkAgreementSelect = "";
            select.append(new Option("Wybierz umowę", ""), ...availableAgreements.map((agreement) => new Option(`${agreement.training_name || "Umowa"} — ${agreement.number || agreement.filename}`, String(agreement.id), false, String(agreement.id) === String(suggested?.id || ""))));
            const status = document.createElement("span"); status.className = "bulk-upload-status is-pending"; status.dataset.bulkAgreementStatus = ""; status.textContent = suggested ? "Oczekuje" : "Wymaga przypisania";
            select.addEventListener("change", () => {
                status.textContent = select.value ? "Oczekuje" : "Wymaga przypisania";
                status.className = "bulk-upload-status is-pending";
            });
            row.append(fileName, select, status); rowsHolder.appendChild(row);
        });
        matches.hidden = !selectedFiles.length;
        submit.disabled = !selectedFiles.length || !availableAgreements.length;
        if (filename) filename.textContent = selectedFiles.length ? `Wybrano ${selectedFiles.length} plików` : "Nie wybrano plików";
        if (summary) summary.textContent = "";
    }

    input?.addEventListener("change", () => { selectedFiles = Array.from(input.files || []); renderRows(); });
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const rows = Array.from(rowsHolder.querySelectorAll("[data-bulk-agreement-row]"));
        if (!rows.length || rows.some((row) => !row.querySelector("[data-bulk-agreement-select]")?.value)) {
            if (summary) summary.textContent = "Przypisz każdy plik do umowy przed wysłaniem.";
            return;
        }
        const selectedAgreementIds = rows.map((row) => row.querySelector("[data-bulk-agreement-select]").value);
        if (new Set(selectedAgreementIds).size !== selectedAgreementIds.length) {
            if (summary) summary.textContent = "Każdą umowę można przypisać tylko do jednego pliku.";
            return;
        }
        submit.disabled = true;
        const payload = new FormData();
        rows.forEach((row, index) => {
            payload.append("signed_agreement_files", selectedFiles[index], selectedFiles[index].name);
            payload.append("agreement_ids", row.querySelector("[data-bulk-agreement-select]").value);
            const status = row.querySelector("[data-bulk-agreement-status]"); status.textContent = "Wysyłanie"; status.className = "bulk-upload-status is-uploading";
        });
        try {
            const response = await fetch(form.action, {method: "POST", body: payload, headers: {Accept: "application/json"}});
            const data = await response.json();
            (data.results || []).forEach((result, index) => {
                const status = rows[index]?.querySelector("[data-bulk-agreement-status]");
                if (!status) return;
                status.textContent = result.status === "uploaded" ? "Wgrano" : `Błąd: ${result.message || "Nie udało się wgrać"}`;
                status.className = `bulk-upload-status ${result.status === "uploaded" ? "is-uploaded" : "is-error"}`;
            });
            if (summary) summary.textContent = `Wgrano: ${data.uploaded || 0}. Wymaga poprawy: ${data.failed || 0}.`;
        } catch (_) {
            rows.forEach((row) => { const status = row.querySelector("[data-bulk-agreement-status]"); status.textContent = "Błąd wysyłania"; status.className = "bulk-upload-status is-error"; });
            if (summary) summary.textContent = "Nie udało się przesłać plików. Spróbuj ponownie.";
        } finally {
            submit.disabled = false;
        }
    });
}

bindDownloadReplacementCards();
bindUploadDropzones();
bindBulkAgreementUpload();

if (submissionInput && submissionInput.value.trim()) {
    checkAcceptanceStatus();
} else {
    renderStatusTile({
        variant: "warning",
        icon: "i",
        title: "Podaj ID wniosku",
        description: "Status wniosku zostanie sprawdzony przed pokazaniem dokumentów do podpisu.",
    });
}
