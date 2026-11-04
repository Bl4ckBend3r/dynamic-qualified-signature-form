export function readBuilderConfig(root) {
    const readJson = (selector, fallback) => {
        try { return JSON.parse(root.querySelector(selector)?.textContent || JSON.stringify(fallback)); }
        catch (_error) { return fallback; }
    };
    return {
        fields: readJson("[data-builder-initial-state]", []).map(withKey),
        widths: readJson("[data-builder-widths]", {full: 12}),
        types: readJson("[data-builder-types]", ["text"]),
        workflowSteps: readJson("[data-builder-workflow-steps]", []),
    };
}

export function withKey(field) {
    const generated =
        globalThis.crypto?.randomUUID?.() ||
        `${Date.now()}-${Math.random().toString(16).slice(2)}`;

    const result = {
        ...field,
        _key:
            field._key ||
            `field-${field.id || generated}`,
    };

    if (
        result.type === "repeatable_group" &&
        Array.isArray(result.fields)
    ) {
        result.fields =
            result.fields.map(withKey);
    }

    return result;
}

export function serializeFields(fields) {
    return fields.map(serializeField);
}

function serializeField(field) {
    const {
        _key,
        ...serialized
    } = field;

    if (
        serialized.type === "repeatable_group" &&
        Array.isArray(serialized.fields)
    ) {
        serialized.fields =
            serialized.fields.map(
                serializeField
            );
    }

    return serialized;
}

export function uniqueFieldName(fields, type, preferred = "") {
    const base = slugify(preferred || type || "pole") || "pole";
    const used = new Set(fields.map((field) => field.name));
    if (!used.has(base)) return base;
    let suffix = 2;
    while (used.has(`${base}_${suffix}`)) suffix += 1;
    return `${base}_${suffix}`;
}

export function slugify(value) {
    return String(value || "")
        .normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
        .toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}
