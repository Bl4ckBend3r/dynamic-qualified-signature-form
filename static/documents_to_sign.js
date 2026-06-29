const submissionInput = document.getElementById("submission_id");
const statusBox = document.getElementById("acceptance-status");
const statusTiles = document.getElementById("submission-status-tiles");
const documentsSection = document.getElementById("documents-section");
const generateButton = document.getElementById("generate-button");
const acceptanceSelect = document.getElementById("akceptacja");
const signDocumentsForm = document.getElementById("sign-documents-form");
const processCompletedBox = document.getElementById("process-completed-box");

let timeoutId = null;

function buildAcceptanceStatusUrl(submissionId) {
    const encodedSubmissionId = encodeURIComponent(submissionId);
    const template = submissionInput ? submissionInput.dataset.acceptanceStatusUrlTemplate || "" : "";

    if (template.includes("__SUBMISSION_ID__")) {
        return template.replace("__SUBMISSION_ID__", encodedSubmissionId);
    }

    return `/api/submissions/${encodedSubmissionId}/acceptance-status`;
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

bindDownloadReplacementCards();
bindUploadDropzones();

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
