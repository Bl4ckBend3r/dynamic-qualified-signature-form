const submissionInput = document.getElementById("submission_id");
const statusBox = document.getElementById("acceptance-status");
const statusTiles = document.getElementById("submission-status-tiles");
const documentsSection = document.getElementById("documents-section");
const generateButton = document.getElementById("generate-button");
const acceptanceSelect = document.getElementById("akceptacja");
const signDocumentsForm = document.getElementById("sign-documents-form");
const instructionWindow = document.getElementById("user-instruction-window");
document.querySelectorAll('[data-composite-document] a[href*="/download/"]').forEach(link => {
    link.addEventListener('click', () => setTimeout(checkAcceptanceStatus, 700));
});
document.querySelectorAll('[data-document-upload]').forEach(form => {
    const input = form.querySelector('input[type="file"]');
    const zone = form.querySelector('.composite-document-dropzone');
    zone.addEventListener('dragover', event => event.preventDefault());
    zone.addEventListener('drop', event => {
        event.preventDefault();
        if (event.dataTransfer.files.length === 1) input.files = event.dataTransfer.files;
    });
    form.addEventListener('submit', () => {
        form.querySelector('button[type="submit"]').disabled = true;
        form.querySelector('[data-document-upload-status]').textContent = 'Wysyłamy dokument i sprawdzamy wymagane podpisy…';
    });
});
const instructionTitle = document.getElementById("user-instruction-title");
const formInstructionSection = document.getElementById("form-instruction-section");
const formInstructionContent = document.getElementById("form-instruction-content");
const instructionStagesSection = document.getElementById("instruction-stages-section");
const instructionSteps = document.getElementById("instruction-steps");
const currentStageLabelSection = document.getElementById("current-stage-label-section");
const currentStageLabel = document.getElementById("current-stage-label");
const currentStageDescriptionSection = document.getElementById("current-stage-description-section");
const currentStageDescription = document.getElementById("current-stage-description");
const nextActionSection = document.getElementById("next-action-section");
const nextActionContent = document.getElementById("next-action-content");
const nextStageSection = document.getElementById("next-stage-section");
const nextStageLabel = document.getElementById("next-stage-label");
const instructionMinimizeButton = document.getElementById("user-instruction-minimize");
const instructionCloseButton = document.getElementById("user-instruction-close");
const instructionRestoreButton = document.getElementById("user-instruction-restore");
const participantAccessToken = document.getElementById("participant-access-token");
const initialCredentialId = submissionInput?.value.trim() || "";
const initialCredential = participantAccessToken?.value || "";
const accessDenied = signDocumentsForm?.dataset.accessDenied === "true";

function credentialForSubmission(id) {
    if (id === initialCredentialId && initialCredential) return initialCredential;
    try { return sessionStorage.getItem(`participant-access:${window.APP_BASE_PATH}:${id}`) || ""; }
    catch (_) { return ""; }
}

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
    const stageLabel = String(nested?.current_stage_label ?? data.current_step_label ?? "").trim();
    const followingStageLabel = String(nested?.next_stage_label ?? data.next_stage_label ?? "").trim();
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
        formInstructionContent.textContent = instruction;
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
        currentStageDescription.textContent = stageDescription;
    }
    if (stageDescription) {
        showElement(currentStageDescriptionSection);
    } else {
        hideElement(currentStageDescriptionSection);
    }
    if (currentStageLabel) currentStageLabel.textContent = stageLabel;
    if (stageLabel) showElement(currentStageLabelSection); else hideElement(currentStageLabelSection);
    if (nextActionContent) {
        nextActionContent.textContent = nextAction;
    }
    if (nextAction) {
        showElement(nextActionSection);
    } else {
        hideElement(nextActionSection);
    }
    if (nextStageLabel) nextStageLabel.textContent = followingStageLabel;
    if (followingStageLabel) showElement(nextStageSection); else hideElement(nextStageSection);
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
    statusTiles.replaceChildren();
    const tile = document.createElement("div");
    tile.className = `status-tile status-tile--${variant}`;
    tile.dataset.publicCurrentStatus = "";
    tile.setAttribute("role", variant === "danger" ? "alert" : "status");
    const iconBox = document.createElement("span");
    iconBox.className = "status-tile__icon";
    iconBox.setAttribute("aria-hidden", "true");
    iconBox.textContent = icon;
    const content = document.createElement("div");
    const heading = document.createElement("p");
    heading.className = "status-tile__title";
    heading.textContent = String(title || "");
    const text = document.createElement("p");
    text.className = "status-tile__description";
    text.textContent = String(description || "");
    content.append(heading, text);
    tile.append(iconBox, content);
    statusTiles.appendChild(tile);
}

function clearStatusTile() {
    if (statusTiles) {
        statusTiles.innerHTML = "";
    }
}

function renderSubmissionStatus(data) {
    if (!data.exists) {
        renderAccessLinkMessage();
        return;
    }

    const currentStatus = data.status && typeof data.status === "object" ? data.status : null;
    const statusTitle = currentStatus?.title || data.status_title;
    if (statusTitle) {
        const rawVariant = currentStatus?.variant || data.status_variant;
        const variant = ["success", "warning", "danger"].includes(rawVariant) ? rawVariant : "neutral";
        const icon = variant === "success" ? "✓" : variant === "danger" ? "!" : variant === "warning" ? "…" : "i";
        const description = [
            currentStatus?.message || data.status_description || data.message || "",
            currentStatus?.reason || data.status_reason || "",
            currentStatus?.next_action || data.next_action || "",
        ].filter(Boolean).join(" ");
        renderStatusTile({variant, icon, title: statusTitle, description});
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
    }

    if (agreementIsCompleted) {
        hideDeclarationStage();
        hideAgreementStage();
        hideInitialSigningForm();
        disableGenerateButton();
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

function renderAccessLinkMessage() {
    hideElement(signDocumentsForm);
    hideElement(document.querySelector('.documents-instructions'));
    renderStatusTile({
        variant: "warning",
        icon: "i",
        title: "Otwórz link dostępu",
        description: "Otwórz link dostępu otrzymany po wysłaniu zgłoszenia — na stronie potwierdzenia lub w wiadomości e-mail.",
    });
}

async function checkAcceptanceStatus() {
    const submissionId = submissionInput ? submissionInput.value.trim() : "";

    resetState();

    const token = credentialForSubmission(submissionId).trim();
    if (!submissionId || !token || accessDenied) {
        renderAccessLinkMessage();
        return;
    }
    if (participantAccessToken) participantAccessToken.value = token;
    showElement(signDocumentsForm);

    renderStatusTile({
        variant: "warning",
        icon: "…",
        title: "Sprawdzanie statusu wniosku",
        description: "Sprawdzamy, czy urzędnik zaakceptował Twój wniosek.",
    });

    try {
        const headers = {Authorization: `Bearer ${token}`};
        const response = await fetch(buildAcceptanceStatusUrl(submissionId), {headers});
        const data = await response.json();
        if (submissionInput.value.trim() !== submissionId) return;
        if (data.authorized && token) {
            try {
                sessionStorage.setItem(`participant-access:${window.APP_BASE_PATH}:${submissionId}`, token);
                sessionStorage.setItem(`participant-last-submission:${window.APP_BASE_PATH}`, submissionId);
            }
            catch (_) { /* Storage is optional; keep using the provided link credential. */ }
        }

        renderSubmissionStatus(data);
        if (!data.authorized) return;
        if (!data.agreement_blocked) showElement(document.querySelector('.documents-instructions'));
        applyProcessStageVisibility(data);
        showUserInstruction(data, submissionId);
        if (data.composite_document) {
            hideElement(document.querySelector('.documents-instructions'));
            hideElement(document.querySelector('.sign-actions'));
            return;
        }

        if (
            data.exists
            && (data.can_sign_documents || data.can_view_status_details)
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
        if (submissionInput.value.trim() !== submissionId) return;
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
        resetState();
        if (participantAccessToken) participantAccessToken.value = credentialForSubmission(submissionInput.value.trim());
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
    const normalized = String(value || "")
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLowerCase()
        .replace(/\.[^.]+$/, "")
        .replace(/[^a-z0-9]+/g, "");
    return normalized.replace(/(?:podpisana|podpisany|signed)$/, "");
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

    function suggestedAgreement(file, used) {
        const normalizedFile = normalizeAgreementMatchText(file.name);
        const matches = availableAgreements.filter((agreement) => {
            if (used.has(String(agreement.id))) return false;
            return normalizeAgreementMatchText(agreement.filename) === normalizedFile;
        });
        const match = matches.length === 1 ? matches[0] : null;
        if (match) used.add(String(match.id));
        return match;
    }

    function renderRows() {
        rowsHolder.replaceChildren();
        const used = new Set();
        selectedFiles.forEach((file, index) => {
            const suggested = suggestedAgreement(file, used);
            const row = document.createElement("div"); row.className = "bulk-agreement-row"; row.dataset.bulkAgreementRow = String(index);
            const fileName = document.createElement("span"); fileName.textContent = file.name;
            const agreementName = document.createElement("span");
            agreementName.textContent = suggested ? `${suggested.training_name || "Umowa"} — ${suggested.filename}` : "Brak pasującej umowy";
            const status = document.createElement("span"); status.className = `bulk-upload-status ${suggested ? "is-pending" : "is-error"}`; status.dataset.bulkAgreementStatus = ""; status.textContent = suggested ? "Dopasowano" : "Niedopasowany plik";
            row.dataset.agreementId = String(suggested?.id || "");
            row.append(fileName, agreementName, status); rowsHolder.appendChild(row);
        });
        matches.hidden = !selectedFiles.length;
        submit.disabled = !selectedFiles.length || !availableAgreements.length;
        if (filename) filename.textContent = selectedFiles.length ? `Wybrano ${selectedFiles.length} plików` : "Nie wybrano plików";
        const matched = new Set(Array.from(rowsHolder.children).map((row) => row.dataset.agreementId).filter(Boolean));
        const missing = availableAgreements.filter((agreement) => !matched.has(String(agreement.id)));
        if (summary) summary.textContent = missing.length
            ? `Brakuje plików dla ${missing.length} ${missing.length === 1 ? "umowy" : "umów"}. Pozostałe poprawne pliki możesz wysłać.`
            : "Wszystkie oczekujące umowy mają dopasowany plik.";
    }

    input?.addEventListener("change", () => { selectedFiles = Array.from(input.files || []); renderRows(); });
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const rows = Array.from(rowsHolder.querySelectorAll("[data-bulk-agreement-row]"));
        if (!rows.length) return;
        submit.disabled = true;
        const payload = new FormData();
        rows.forEach((row, index) => {
            payload.append("signed_agreement_files", selectedFiles[index], selectedFiles[index].name);
            const status = row.querySelector("[data-bulk-agreement-status]"); status.textContent = "Wysyłanie"; status.className = "bulk-upload-status is-uploading";
        });
        payload.append("access_token", form.querySelector("[data-bulk-agreement-token]")?.value || "");
        try {
            const response = await fetch(form.action, {method: "POST", body: payload, headers: {Accept: "application/json"}});
            const data = await response.json();
            (data.results || []).forEach((result, index) => {
                const status = rows[index]?.querySelector("[data-bulk-agreement-status]");
                if (!status) return;
                status.textContent = result.status === "uploaded" ? "Wgrano" : `Błąd: ${result.message || "Nie udało się wgrać"}`;
                status.className = `bulk-upload-status ${result.status === "uploaded" ? "is-uploaded" : "is-error"}`;
            });
            const pending = Array.isArray(data.pending_agreements) ? data.pending_agreements.length : 0;
            if (summary) summary.textContent = `Wgrano: ${data.uploaded || 0}. Wymaga poprawy: ${data.failed || 0}. Nadal oczekuje: ${pending}.`;
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

if (submissionInput && !submissionInput.value.trim() && !accessDenied) {
    try {
        const lastId = sessionStorage.getItem(`participant-last-submission:${window.APP_BASE_PATH}`) || "";
        if (lastId && credentialForSubmission(lastId)) submissionInput.value = lastId;
    } catch (_) { /* Access remains available through the confirmation or email link. */ }
}
checkAcceptanceStatus();
