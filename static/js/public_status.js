const statusForm = document.getElementById('public-status-form');
const statusInput = document.getElementById('public-submission-id');
const statusResult = document.getElementById('public-status-result');
const statusMessage = document.getElementById('public-status-message');
const statusLabel = document.getElementById('public-status-label');
const resultId = document.getElementById('public-status-id');
const submitButton = statusForm.querySelector('button[type="submit"]');
const copyButton = document.getElementById('public-status-copy');
const copyMessage = document.getElementById('public-status-copy-message');
const lookupError = 'Nie można odnaleźć statusu dla podanego numeru zgłoszenia.';
let lookupSequence = 0;
let lookupPending = false;

function clearStatus() {
    statusResult.hidden = true;
    statusMessage.hidden = true;
    statusMessage.textContent = '';
    statusLabel.textContent = '';
    resultId.textContent = '';
    copyMessage.hidden = true;
    copyMessage.textContent = '';
}

function showMessage(message, isError = false) {
    statusMessage.classList.toggle('status-page__message--error', isError);
    statusMessage.textContent = message;
    statusMessage.hidden = false;
}

async function lookupPublicStatus(event) {
    event?.preventDefault();
    if (lookupPending) return;
    const id = statusInput.value.trim();
    if (!id) return;
    const sequence = ++lookupSequence;
    lookupPending = true;
    submitButton.disabled = true;
    submitButton.textContent = 'Sprawdzanie…';
    statusForm.setAttribute('aria-busy', 'true');
    clearStatus();
    showMessage('Sprawdzanie statusu…');
    try {
        const response = await fetch(statusForm.dataset.statusUrl.replace('__ID__', encodeURIComponent(id)), {credentials: 'omit', cache: 'no-store'});
        const data = await response.json();
        if (sequence !== lookupSequence) return;
        if (!response.ok || typeof data.status_label !== 'string' || !data.status_label.trim()) {
            showMessage(response.status === 429 ? 'Zbyt wiele prób. Spróbuj ponownie za chwilę.' : lookupError, true);
            return;
        }
        statusMessage.hidden = true;
        statusMessage.textContent = '';
        statusLabel.textContent = data.status_label;
        resultId.textContent = id;
        statusResult.hidden = false;
    } catch (_) {
        if (sequence === lookupSequence) showMessage(lookupError, true);
    } finally {
        lookupPending = false;
        submitButton.disabled = false;
        submitButton.textContent = 'Sprawdź status';
        statusForm.removeAttribute('aria-busy');
    }
}

copyButton.addEventListener('click', async () => {
    const id = resultId.textContent;
    if (!id) return;
    const sequence = lookupSequence;
    try {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(id);
        } else {
            const selection = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(resultId);
            selection.removeAllRanges();
            selection.addRange(range);
            if (!document.execCommand('copy')) throw new Error('copy_failed');
            selection.removeAllRanges();
        }
        if (sequence !== lookupSequence) return;
        copyMessage.textContent = 'Skopiowano numer zgłoszenia.';
    } catch (_) {
        if (sequence !== lookupSequence) return;
        copyMessage.textContent = 'Nie udało się skopiować. Zaznacz numer zgłoszenia i skopiuj go ręcznie.';
    }
    copyMessage.hidden = false;
});

statusForm.addEventListener('submit', lookupPublicStatus);
statusInput.addEventListener('input', () => {
    lookupSequence++;
    clearStatus();
});
if (statusInput.value.trim()) lookupPublicStatus();
