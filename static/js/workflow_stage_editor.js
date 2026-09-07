/* Stage editing for the existing workflow v2 graph and decision snapshots. */
window.WorkflowStageEditor = (() => {
    const types = {user_action: 'Akcja użytkownika', officer_action: 'Akcja urzędnika', system: 'Automatyczny', decision: 'Decyzja', document: 'Dokument', final: 'Zakończenie procesu'};
    const read = (card, key) => card.querySelector(`[data-stage-field="${key}"]`);
    const value = (card, key) => read(card, key)?.value || '';
    const optionIdentity = option => option.definition_id != null ? `id:${option.definition_id}` : `code:${option.code}`;
    const repeatableGroups = () => (window.workflowFields || []).filter(field => field.type === 'repeatable_group' && field.name);
    const documentCapabilities = {generate: 'Generowanie', download: 'Pobranie', participant_signature: 'Podpis użytkownika', upload_required: 'Wgranie przez użytkownika', signature_verification: 'Weryfikacja podpisu', office_signature: 'Podpis urzędu'};
    const documentSubstates = {collect_data: 'Uzupełnienie danych dokumentu', verification_error: 'Weryfikacja chwilowo niedostępna', generating: 'Generowanie dokumentu', ready: 'Dokument gotowy do pobrania', awaiting_signature: 'Podpisanie dokumentu', upload: 'Wgranie dokumentu', verifying: 'Weryfikacja podpisu', verification_failed: 'Błąd weryfikacji', participant_signed: 'Podpis uczestnika zapisany', awaiting_office_signature: 'Oczekiwanie na podpis urzędu', office_signed: 'Podpis urzędu zapisany', signed: 'Dokument podpisany', completed: 'Etap zakończony', failed: 'Błąd generowania'};
    const node = (tag, text, className) => {
        const el = document.createElement(tag);
        if (text) el.textContent = text;
        if (className) el.className = className;
        return el;
    };
    function field(parent, title, key, initial = '', choices = null) {
        const label = node('label');
        label.append(node('span', title));
        const input = node(choices ? 'select' : 'input');
        input.dataset.stageField = key;
        if (choices) choices.forEach(([code, name]) => input.add(new Option(name, code)));
        input.value = initial;
        label.append(input); parent.append(label);
        return input;
    }
    function check(parent, title, key, checked = false) {
        const label = node('label', '', 'admin-check');
        const input = node('input'); input.type = 'checkbox'; input.dataset.stageField = key; input.checked = checked;
        label.append(input, node('span', title)); parent.append(label); return input;
    }
    function initialize(card, workflow) {
        if (card.dataset.stageEditorReady) return;
        card.dataset.stageEditorReady = 'true';
        const source = JSON.parse(card.querySelector('[data-workflow-step-source]')?.textContent || '{}');
        const assigned = (workflow.decision_types || []).filter(o => o.step_id === source.id && o.active !== false);
        const groups = repeatableGroups();
        const legacyGroups = groups.filter(g => (g.decision_completion?.step_ids || []).includes(source.id));
        const legacyGroup = workflow.flow_mode !== 'explicit' && legacyGroups.length === 1 ? legacyGroups[0] : null;
        const type = card.querySelector('[data-step-stage-type]');
        const original = type.value;
        type.replaceChildren(...Object.entries(types).map(([code, name]) => new Option(name, code)));
        type.value = assigned.length || source.stage_type === 'decision' || source.type === 'manual_decision' || source.type === 'decision' ? 'decision' : original === 'automatic' ? 'system' : original;
        const editor = card.querySelector('[data-workflow-step-editor]');
        const actions = node('div', '', 'workflow-stage-fields');
        actions.dataset.stageActions = '';
        field(actions, 'Akcja', 'action', source.action || (source.type === 'generate_document' ? 'generate_document' : source.type === 'upload_document' ? 'await_signature' : 'none'), [
            ['none', 'Brak dodatkowej akcji'], ['generate_document', 'Wygeneruj dokument'], ['await_signature', 'Oczekuj na podpis dokumentu'],
        ]);
        const documentChoices = (window.workflowDocuments || []).filter(d => d.enabled !== false).map(d => [d.id, d.label || d.id]);
        if (source.document_id && !documentChoices.some(([id]) => id === source.document_id)) documentChoices.push([source.document_id, `Niedostępny dokument: ${source.document_id}`]);
        field(actions, 'Dokument *', 'document_id', source.document_id || '', [['', 'Wybierz dokument'], ...documentChoices]);
        editor.append(actions);
        const documentEditor = node('section', '', 'workflow-document-editor'); documentEditor.dataset.documentEditor = '';
        const compositeControl = check(documentEditor, 'Obsługuj cały cykl dokumentu w tym etapie', 'document_composite', source.document_lifecycle === 'composite' || source.stage_type !== 'document');
        // A new document is always a whole lifecycle. The toggle only adapts legacy draft steps.
        compositeControl.closest('label').hidden = source.document_lifecycle === 'composite' || source.stage_type !== 'document';
        const documentSettings = node('div'); documentSettings.dataset.documentSettings = '';
        documentSettings.append(node('h4', 'Przebieg dokumentu'));
        const capabilityFields = node('div', '', 'workflow-stage-fields');
        const documentConfig = (window.workflowDocuments || []).find(d => d.id === source.document_id) || {};
        const documentPolicy = {generate: (documentConfig.kind || 'generated_pdf') === 'generated_pdf', download: true, participant_signature: documentConfig.signature_required !== false,
            upload_required: documentConfig.signature_required !== false, signature_verification: true, office_signature: false,
            ...(documentConfig.lifecycle || {}), ...(source.document_options || {})};
        Object.entries(documentCapabilities).forEach(([key, title]) => check(capabilityFields, title, `document_${key}`, documentPolicy[key]));
        read(card, 'document_id').addEventListener('change', () => {
            const config = (window.workflowDocuments || []).find(d => d.id === value(card, 'document_id')) || {};
            const policy = {generate: (config.kind || 'generated_pdf') === 'generated_pdf', download: true,
                participant_signature: config.signature_required !== false, upload_required: config.signature_required !== false,
                signature_verification: true, office_signature: false, ...(config.lifecycle || {})};
            Object.keys(documentCapabilities).forEach(key => { read(card, `document_${key}`).checked = Boolean(policy[key]); });
        });
        documentSettings.append(capabilityFields, node('h4', 'Instrukcje podetapów'));
        Object.entries(documentSubstates).forEach(([key, title]) => {
            const details = node('details'); details.dataset.documentSubstate = key;
            details.append(node('summary', `${title} — edytuj instrukcję`));
            ['description', 'next_action'].forEach(part => {
                const label = node('label', part === 'description' ? 'Instrukcja' : 'Co użytkownik ma zrobić dalej?');
                const textarea = node('textarea'); textarea.dataset.stageField = `document_instruction_${key}_${part}`;
                textarea.rows = 3; textarea.value = source.document_instructions?.[key]?.[part] || '';
                label.append(textarea); details.append(label);
            });
            documentSettings.append(details);
        });
        documentEditor.append(documentSettings); editor.append(documentEditor);
        const section = node('section', '', 'workflow-stage-decision-editor');
        section.dataset.stageDecisionEditor = '';
        section.append(node('h4', 'Decyzja'));
        const base = node('div', '', 'workflow-stage-fields');
        field(base, 'Nazwa decyzji *', 'decision_name', source.decision_name || source.admin_label || source.label || '');
        const scope = field(base, 'Decyzja dotyczy', 'decision_scope', source.decision_scope || (legacyGroup || assigned[0]?.scope === 'item' ? 'item' : 'submission'), [['submission', 'Całego zgłoszenia'], ['item', 'Elementów grupy powtarzalnej']]);
        if (!groups.length) {
            scope.options[1].disabled = true;
            scope.closest('label').append(node('small', 'Ten formularz nie zawiera grupy powtarzalnej.'));
        }
        const groupName = source.decision_group || legacyGroup?.name || '';
        const groupChoices = [['', 'Wybierz grupę'], ...groups.map(g => [g.name, `${g.label || g.name} (${g.name})`])];
        if (groupName && !groups.some(g => g.name === groupName)) groupChoices.push([groupName, `Niedostępna grupa: ${groupName}`]);
        field(base, 'Grupa *', 'decision_group', groupName, groupChoices);
        const policy = field(base, 'Polityka zakończenia', 'completion_policy', source.completion_policy || 'ALL', [['ALL', 'ALL — wszystkie rekordy']]);
        policy.closest('label').append(node('small', 'Etap zostanie zakończony po zapisaniu decyzji dla wszystkich aktywnych elementów grupy.'));
        const completion = field(base, 'Etap po rozpatrzeniu wszystkich osób *', 'completion_next', '', []);
        completion.dataset.selectedTarget = source.completion_next || legacyGroup?.decision_completion?.next || '';
        const count = node('p', '', 'workflow-help'); count.dataset.decisionCount = '';
        section.append(base, node('h4', 'Opcje decyzji'), count);
        const options = node('div', '', 'workflow-stage-decision-options');
        options.dataset.decisionOptions = '';
        const available = new Map();
        // Snapshot wins by catalog ID, with code fallback for older snapshots without an ID.
        [...assigned, ...(window.workflowDecisionCatalog || [])].forEach(option => {
            if (![...available.values()].some(o => optionIdentity(o) === optionIdentity(option) || o.code === option.code)) available.set(optionIdentity(option), option);
        });
        available.forEach(option => {
            const assignedOption = assigned.find(o => optionIdentity(o) === optionIdentity(option) || o.code === option.code);
            const row = node('div', '', 'workflow-stage-decision-option');
            row.dataset.decisionOption = option.code; row.optionSnapshot = option;
            check(row, option.label, 'selected', Boolean(assignedOption));
            row.append(node('code', option.code));
            const overview = node('p', '', 'workflow-help'); overview.dataset.optionSummary = ''; row.append(overview);
            const details = node('details'); details.dataset.optionDetails = '';
            details.append(node('summary', 'Edytuj'));
            const settings = node('div', '', 'workflow-stage-fields'); settings.dataset.optionSettings = '';
            const target = field(settings, 'Następny etap *', 'target_step', '', []); target.dataset.selectedTarget = assignedOption?.target_step || '';
            const status = node('p', '', 'workflow-help'); status.dataset.optionStatus = ''; settings.append(status);
            check(settings, 'Wymaga uzasadnienia', 'require_reason', Boolean(assignedOption?.reason_required ?? assignedOption?.require_reason));
            details.append(settings); row.append(details); options.append(row);
        });
        if (!available.size) options.append(node('p', 'Katalog jest pusty. Najpierw dodaj typy decyzji w konfiguracji opcji.', 'workflow-help'));
        section.append(options);
        const mail = node('div', '', 'workflow-stage-fields');
        check(mail, 'Wyślij e-mail po wyborze decyzji', 'decision_email_enabled', Boolean(source.decision_email?.enabled));
        field(mail, 'Szablon / zdarzenie wiadomości', 'decision_email_template', source.decision_email?.template_type || '', [['', 'Wybierz szablon / zdarzenie'], ...(window.workflowMailChoices || [])]);
        section.append(mail); editor.append(section);
        const advanced = node('details', '', 'workflow-stage-advanced');
        advanced.append(node('summary', 'Ustawienia zaawansowane'));
        editor.append(advanced);
        check(advanced, 'Zezwól na jawny powrót do tego samego etapu', 'allow_loop', Boolean(source.allow_loop));
        const transitions = field(advanced, 'Przejścia warunkowe (JSON, opcjonalnie)', 'transitions_json', JSON.stringify(source.transitions || []));
        transitions.classList.add('workflow-transition-json');
    }
    function sync(card, steps) {
        const kind = card.querySelector('[data-step-stage-type]').value;
        const decision = kind === 'decision';
        const item = decision && value(card, 'decision_scope') === 'item';
        const composite = kind === 'document' && read(card, 'document_composite').checked;
        card.querySelector('[data-document-editor]').hidden = kind !== 'document';
        card.querySelector('[data-document-settings]').hidden = !composite;
        const enabled = key => read(card, `document_${key}`).checked;
        const visibleSubstates = {
            collect_data: true, verification_error: enabled('signature_verification'), generating: enabled('generate'), failed: enabled('generate'), ready: enabled('download'),
            awaiting_signature: enabled('participant_signature'), participant_signed: enabled('participant_signature'),
            upload: enabled('upload_required'), verifying: enabled('signature_verification'), verification_failed: enabled('signature_verification'),
            awaiting_office_signature: enabled('office_signature'), office_signed: enabled('office_signature'),
            signed: enabled('participant_signature') || enabled('office_signature'), completed: true,
        };
        card.querySelectorAll('[data-document-substate]').forEach(details => {
            details.hidden = !visibleSubstates[details.dataset.documentSubstate];
        });
        read(card, 'action').closest('label').hidden = composite;
        card.querySelector('[data-stage-decision-editor]').hidden = !decision;
        card.querySelector('[data-stage-actions]').hidden = !['document', 'system', 'user_action'].includes(kind);
        card.querySelector('[data-step-next]').closest('label').hidden = decision || kind === 'final';
        card.querySelector('.workflow-stage-options').hidden = true;
        ['decision_group', 'completion_policy', 'completion_next'].forEach(key => read(card, key).closest('label').hidden = !item);
        read(card, 'decision_email_template').closest('label').hidden = !decision || !read(card, 'decision_email_enabled').checked;
        read(card, 'transitions_json').closest('label').hidden = decision || kind === 'final' || composite;
        card.querySelectorAll('[data-stage-field="target_step"], [data-stage-field="completion_next"]').forEach(select => {
            const selected = select.dataset.targetsReady ? select.value : (select.dataset.selectedTarget || '');
            select.dataset.targetsReady = 'true';
            select.replaceChildren(new Option('Wybierz etap', ''), ...steps.map(s => new Option(s.label, s.id)));
            if (selected && !steps.some(s => s.id === selected)) select.add(new Option(`Nieistniejący etap: ${selected}`, selected));
            select.value = selected; select.dataset.selectedTarget = selected;
            const description = select.closest('[data-option-settings]')?.querySelector('[data-option-status]');
            if (description) description.textContent = `Publiczny status po przejściu: ${steps.find(s => s.id === selected)?.user_label || 'wybierz etap docelowy'}`;
        });
        card.querySelectorAll('[data-decision-option]').forEach(row => {
            const selected = read(row, 'selected').checked;
            row.querySelector('[data-option-details]').hidden = !selected;
            read(row, 'target_step').closest('label').hidden = item;
            const target = item ? value(card, 'completion_next') : value(row, 'target_step');
            const targetLabel = steps.find(s => s.id === target)?.label || 'Wybierz etap';
            row.querySelector('[data-option-summary]').textContent = selected ? `→ ${targetLabel}${item ? ' (po zakończeniu ALL)' : ''}` : 'Opcja niewybrana';
            row.querySelector('[data-option-status]').textContent = item
                ? 'Przejście całego zgłoszenia określa etap po zakończeniu ALL. Pojedyncza decyzja osoby nie zmienia etapu.'
                : `Status widoczny dla użytkownika po przejściu: ${steps.find(s => s.id === target)?.user_label || 'wybierz etap docelowy'}`;
        });
        card.querySelector('[data-decision-count]').textContent = `Wybrano: ${[...card.querySelectorAll('[data-decision-option]')].filter(row => read(row, 'selected').checked).length}`;
    }
    function collect(card) {
        const kind = card.querySelector('[data-step-stage-type]').value;
        const composite = kind === 'document' && read(card, 'document_composite').checked;
        let transitions;
        try { transitions = JSON.parse(value(card, 'transitions_json') || '[]'); } catch (_) { transitions = null; }
        return {
            explicit_stage: true, stage_type: kind,
            type: kind === 'decision' ? 'manual_decision' : kind === 'final' ? 'end' : (kind === 'user_action' && JSON.parse(card.querySelector('[data-workflow-step-source]').textContent || '{}').type === 'form_submit' ? 'form_submit' : kind),
            final: kind === 'final', requires_officer_action: ['officer_action', 'decision'].includes(kind), requires_user_action: kind === 'user_action',
            action: composite ? 'none' : ['system', 'document', 'user_action'].includes(kind) ? value(card, 'action') : 'none',
            document_id: composite || ['system', 'document', 'user_action'].includes(kind) && value(card, 'action') !== 'none' ? value(card, 'document_id') : '',
            document_lifecycle: composite ? 'composite' : undefined,
            document_options: composite ? Object.fromEntries(Object.keys(documentCapabilities).map(key => [key, read(card, `document_${key}`).checked])) : undefined,
            document_instructions: composite ? Object.fromEntries(Object.keys(documentSubstates).map(key => [key, {description: value(card, `document_instruction_${key}_description`), next_action: value(card, `document_instruction_${key}_next_action`)}])) : undefined,
            next: ['decision', 'final'].includes(kind) ? '' : card.querySelector('[data-step-next]').value,
            transitions: composite || ['decision', 'final'].includes(kind) ? [] : transitions,
            invalid_transitions: !['decision', 'final'].includes(kind) && !Array.isArray(transitions),
            decisions: {}, decision_name: value(card, 'decision_name'), decision_scope: value(card, 'decision_scope'),
            decision_group: kind === 'decision' && value(card, 'decision_scope') === 'item' ? value(card, 'decision_group') : '', completion_policy: kind === 'decision' && value(card, 'decision_scope') === 'item' ? value(card, 'completion_policy') : '', completion_next: kind === 'decision' && value(card, 'decision_scope') === 'item' ? value(card, 'completion_next') : '',
            decision_email: {...(JSON.parse(card.querySelector('[data-workflow-step-source]').textContent || '{}').decision_email || {}), enabled: kind === 'decision' && (read(card, 'decision_email_enabled')?.checked || false), template_type: value(card, 'decision_email_template')},
            allow_loop: read(card, 'allow_loop')?.checked || false,
        };
    }
    function decisions(cards) {
        return cards.flatMap(card => card.querySelector('[data-step-stage-type]').value !== 'decision' ? [] : [...card.querySelectorAll('[data-decision-option]')].filter(row => read(row, 'selected').checked).map((row, index) => ({
            ...row.optionSnapshot, step_id: card.querySelector('[data-step-id]').value, scope: value(card, 'decision_scope'),
            // Retain existing item snapshot metadata; new options use the ALL target for compatibility.
            target_step: value(card, 'decision_scope') === 'item' ? (value(row, 'target_step') || value(card, 'completion_next')) : value(row, 'target_step'), require_reason: read(row, 'require_reason').checked,
            reason_required: undefined, active: true, sort_order: index,
        })));
    }
    function validate(config) {
        const errors = [], issues = [], warnings = [], steps = config.steps.filter(s => s.active !== false), ids = new Set(steps.map(s => s.id));
        const edges = new Map();
        if (!config.initial_step || !ids.has(config.initial_step)) errors.push('Wybierz aktywny etap początkowy.');
        steps.forEach(step => {
            const label = step.admin_label || step.id || 'Nowy etap';
            const fail = (text, field = '', option = '') => { errors.push(`${label}: ${text}`); issues.push({stepId: step.id, label, text, field, option}); };
            const options = (config.decision_types || []).filter(o => o.step_id === step.id && o.active !== false);
            const item = step.stage_type === 'decision' && step.decision_scope === 'item';
            if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(step.id)) fail('Podaj techniczne ID etapu.', 'id');
            if (!step.id || steps.filter(s => s.id === step.id).length > 1) fail('ID musi być unikalne.', 'id');
            if (!step.admin_label) fail('Podaj nazwę dla administratora.', 'admin-label');
            if (!step.user_label) fail('Podaj status widoczny dla użytkownika.', 'user-label');
            if (!step.status) fail('Wybierz status techniczny.', 'status');
            if (!Array.isArray(step.transitions)) fail('Przejścia warunkowe muszą być tablicą JSON.', 'transitions_json');
            if (step.stage_type === 'decision' && options.length < 2) fail('Wybierz co najmniej 2 opcje decyzji.', 'options');
            if (new Set(options.map(optionIdentity)).size !== options.length || new Set(options.map(o => o.code)).size !== options.length) fail('Ta sama opcja decyzji może wystąpić w etapie tylko raz.', 'options');
            if (step.stage_type === 'decision' && !step.decision_name) fail('Podaj nazwę decyzji.', 'decision_name');
            if (item && !step.decision_group) fail('Wybierz grupę powtarzalną.', 'decision_group');
            else if (item && !repeatableGroups().some(g => g.name === step.decision_group)) fail('Wybrana grupa nie istnieje w aktualnej definicji formularza.', 'decision_group');
            if (item && !step.completion_next) fail('Wybierz etap po zakończeniu decyzji wszystkich osób.', 'completion_next');
            else if (item && !ids.has(step.completion_next)) fail('Wybierz istniejący aktywny etap.', 'completion_next');
            if (step.decision_email?.enabled && !step.decision_email.template_type) fail('Wybierz szablon e-mail.', 'decision_email_template');
            if (['generate_document', 'await_signature'].includes(step.action) && !step.document_id) fail('Wybierz dokument.', 'document_id');
            if (step.document_lifecycle === 'composite') {
                const definition = (window.workflowDocuments || []).find(d => d.id === step.document_id && d.enabled !== false);
                if (!definition) fail('Wybierz definicję dokumentu.', 'document_id');
                else if (step.document_options.generate && !['template', 'template_html', 'builder_document', 'template_metadata'].some(key => {
                    const content = definition[key];
                    return typeof content === 'string' ? Boolean(content.trim()) : content && Object.keys(content).length > 0;
                })) fail('Skonfiguruj szablon generowanego dokumentu.', 'document_id');
                if (!step.next) fail('Wybierz kolejny etap.', 'next');
                if (step.document_options.participant_signature && !step.document_options.upload_required) fail('Podpis użytkownika wymaga wgrania dokumentu.', 'document_upload_required');
                if (!step.document_options.generate && !step.document_options.upload_required && !step.document_options.office_signature) fail('Włącz generowanie lub wgranie dokumentu.', 'document_generate');
            }
            const targets = item ? [step.completion_next].filter(Boolean) : [step.next, ...(Array.isArray(step.transitions) ? step.transitions.map(t => t.next) : []), ...options.map(o => o.target_step)].filter(Boolean);
            if (!item) options.forEach(o => {
                if (!o.target_step || !ids.has(o.target_step)) fail('Wybierz istniejący kolejny etap.', 'target_step', o.code);
            });
            if (!item && targets.some(t => !ids.has(t))) fail('Przejście wskazuje nieistniejący etap.', 'next');
            if (!step.final && !targets.length) fail('Brak ścieżki do kolejnego etapu.');
            if (targets.includes(step.id) && !step.allow_loop) fail('Powrót do tego samego etapu wymaga jawnej zgody.', 'allow_loop');
            if (step.final && targets.length) fail('Etap końcowy nie może mieć wyjścia.');
            edges.set(step.id, targets.filter(t => ids.has(t)));
        });
        const reached = new Set(), path = new Set();
        function visit(id) {
            if (path.has(id)) { warnings.push('Workflow zawiera powrót/pętlę.'); return; }
            if (reached.has(id)) return;
            reached.add(id); path.add(id); (edges.get(id) || []).forEach(visit); path.delete(id);
        }
        if (ids.has(config.initial_step)) visit(config.initial_step);
        const finals = new Set(steps.filter(s => s.final).map(s => s.id));
        const canFinish = new Set(finals);
        let changed = true;
        while (changed) { changed = false; edges.forEach((targets, id) => { if (!canFinish.has(id) && targets.some(t => canFinish.has(t))) {canFinish.add(id); changed = true;} }); }
        steps.forEach(s => {
            const graphError = text => { errors.push(`${s.admin_label}: ${text}`); issues.push({stepId: s.id, label: s.admin_label, text}); };
            if (!reached.has(s.id)) graphError('Etap nieosiągalny.');
            if (!canFinish.has(s.id)) graphError('Brak ścieżki do zakończenia.');
        });
        return {errors, issues, warnings: [...new Set(warnings)]};
    }
    function showValidation(cards, validation, errorBox) {
        cards.forEach((card, index) => {
            card.classList.remove('has-error');
            card.querySelectorAll('[data-stage-error]').forEach(el => el.remove());
            card.querySelectorAll('[aria-invalid]').forEach(el => { el.removeAttribute('aria-invalid'); el.removeAttribute('aria-describedby'); });
            const local = validation.issues.filter(issue => issue.stepId === card.querySelector('[data-step-id]').value);
            local.forEach((issue, issueIndex) => {
                const row = issue.option ? [...card.querySelectorAll('[data-decision-option]')].find(row => row.dataset.decisionOption === issue.option) : card;
                const input = read(row || card, issue.field) || card.querySelector(`[data-step-${issue.field || 'none'}]`);
                const holder = input?.closest('label') || (issue.field === 'options' ? card.querySelector('[data-decision-options]') : card.querySelector('[data-workflow-step-editor]'));
                const message = node('small', issue.text, 'workflow-field-error'); message.dataset.stageError = ''; message.id = `workflow-error-${index}-${issueIndex}`;
                holder.append(message);
                if (input) { input.setAttribute('aria-invalid', 'true'); input.setAttribute('aria-describedby', [input.getAttribute('aria-describedby'), message.id].filter(Boolean).join(' ')); }
            });
            const badges = card.querySelector('[data-step-badges]');
            badges.querySelector('[data-stage-validity]')?.remove();
            const badge = node('span', local.length ? 'Wymaga konfiguracji' : 'Gotowy', 'workflow-status-badge'); badge.dataset.stageValidity = local.length ? 'invalid' : 'valid'; badges.append(badge);
        });
        if (!errorBox) return;
        const wasOpen = errorBox.querySelector('details')?.open;
        errorBox.replaceChildren(); errorBox.hidden = !validation.errors.length && !validation.warnings.length;
        if (validation.errors.length) {
            const count = validation.errors.length;
            const noun = count === 1 ? 'błąd' : count % 10 >= 2 && count % 10 <= 4 && (count % 100 < 12 || count % 100 > 14) ? 'błędy' : 'błędów';
            errorBox.append(node('p', `⚠ Workflow wymaga poprawy — ${count} ${noun}`));
            const details = node('details'); details.open = Boolean(wasOpen); details.append(node('summary', 'Pokaż szczegóły'));
            const groups = new Map();
            validation.issues.forEach(issue => {
                if (!groups.has(issue.stepId)) { const list = node('ul'); details.append(node('strong', issue.label), list); groups.set(issue.stepId, list); }
                groups.get(issue.stepId).append(node('li', issue.text));
            });
            if (validation.errors.length > validation.issues.length) details.append(node('p', 'Wybierz aktywny etap początkowy.'));
            errorBox.append(details);
        }
        validation.warnings.forEach(text => errorBox.append(node('p', text)));
    }
    function summarize(card, steps) {
        const stage = collect(card), item = stage.stage_type === 'decision' && stage.decision_scope === 'item';
        const count = [...card.querySelectorAll('[data-decision-option]')].filter(row => read(row, 'selected').checked).length;
        const label = id => steps.find(s => s.id === id)?.label || 'Wybierz etap';
        const transition = stage.stage_type === 'decision' ? (item ? `Po zakończeniu ALL → ${label(stage.completion_next)}` : 'Według wybranej opcji') : stage.final ? 'Zakończenie procesu' : stage.transitions?.length ? 'Według warunków' : label(stage.next);
        card.querySelector('[data-step-summary]').replaceChildren(...[
            ['Status', card.querySelector('[data-step-user-label]').value || 'Uzupełnij status'],
            [stage.stage_type === 'decision' ? 'Przejście' : 'Kolejny etap', transition],
            [stage.document_lifecycle === 'composite' ? 'Proces dokumentu' : 'Decyzja', stage.document_lifecycle === 'composite' ? Object.entries(documentCapabilities).filter(([key]) => stage.document_options[key]).map(([,label]) => label).join(' · ') : stage.stage_type === 'decision' ? `${count} ${count === 1 ? 'opcja' : count >= 2 && count <= 4 ? 'opcje' : 'opcji'} · ${item ? 'grupa powtarzalna' : 'całe zgłoszenie'}` : 'Brak'],
        ].map(([title, text]) => { const part = node('div'); part.append(node('dt', title), node('dd', text)); return part; }));
    }
    function references(config, id) {
        const fields = [];
        function inspect(items) {
            (items || []).forEach(field => {
                if ((field.availability || []).some(entry => entry.step === id) || field.stage === id) fields.push(`Pole: ${field.label || field.name}`);
                inspect(field.fields);
            });
        }
        inspect(window.workflowFields);
        return [...fields, config.initial_step === id ? 'Etap początkowy' : '', ...config.steps.filter(s => s.id !== id && (s.next === id || s.completion_next === id || (Array.isArray(s.transitions) ? s.transitions : []).some(t => t.next === id))).map(s => s.admin_label), ...config.decision_types.filter(o => o.target_step === id && o.step_id !== id).map(o => o.label)].filter(Boolean);
    }
    return {types, initialize, sync, collect, decisions, validate, references, showValidation, summarize};
})();
