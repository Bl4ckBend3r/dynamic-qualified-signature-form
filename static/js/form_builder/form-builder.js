import { initializeFieldPalette } from "./field-palette.js";
import { renderCanvas } from "./form-canvas.js";
import { initializePropertiesPanel } from "./properties-panel.js";
import { initializeDragAndDrop } from "./drag-and-drop.js";
import { initializePanelPositioning } from "./panel-positioning.js";
import {
  readBuilderConfig,
  serializeFields,
  uniqueFieldName,
  withKey,
} from "./serialization.js";

const builder = document.querySelector("[data-form-builder]");

if (builder) {
  const config = readBuilderConfig(document);

  let fields = config.fields;

  let selectedPath = fields[0]
    ? {
        groupKey: null,
        fieldKey: fields[0]._key,
      }
    : null;

  const canvas = builder.querySelector("[data-form-canvas]");
  const panel = builder.querySelector("[data-properties-panel]");
  const stateInput = builder.querySelector("[data-builder-state]");
  const status = builder.querySelector("[data-builder-status]");
  const positionPanels = initializePanelPositioning(builder);

  const FIELD_LABELS = {
    text: "Tekst",
    textarea: "Dłuższy tekst",
    email: "E-mail",
    tel: "Telefon",
    number: "Liczba",
    date: "Data",
    time: "Czas",
    select: "Wybierz opcję",
    radio: "Radio",
    checkbox: "Checkbox",
    pesel: "PESEL",
    file: "Załącz plik",
    repeatable_group: "Grupa powtarzalna",
  };

  const OPTION_FIELD_TYPES = new Set(["select", "radio", "checkbox"]);

  const selected = () => {
    if (!selectedPath) {
      return null;
    }

    if (!selectedPath.groupKey) {
      return (
        fields.find((field) => field._key === selectedPath.fieldKey) || null
      );
    }

    const group = fields.find((field) => field._key === selectedPath.groupKey);

    if (!group) {
      return null;
    }

    return (
      (group.fields || []).find(
        (field) => field._key === selectedPath.fieldKey,
      ) || null
    );
  };

  const markChanged = (message = "Niezapisane zmiany") => {
    status.textContent = message;
  };

  const selectTopLevel = (key) => {
    selectedPath = {
      groupKey: null,
      fieldKey: key,
    };

    render();
  };

  const selectNested = (groupKey, fieldKey) => {
    selectedPath = {
      groupKey,
      fieldKey,
    };

    render();
  };

  const updateSelected = (updates) => {
    if (!selectedPath) {
      return;
    }

    if (!selectedPath.groupKey) {
      fields = fields.map((field) =>
        field._key === selectedPath.fieldKey
          ? {
              ...field,
              ...updates,
            }
          : field,
      );
    } else {
      fields = fields.map((group) => {
        if (group._key !== selectedPath.groupKey) {
          return group;
        }

        return {
          ...group,

          fields: (group.fields || []).map((field) =>
            field._key === selectedPath.fieldKey
              ? {
                  ...field,
                  ...updates,
                }
              : field,
          ),
        };
      });
    }

    render();
    markChanged();
  };

  const removeSelected = () => {
    if (!selectedPath) {
      return;
    }

    /*
     * Usuwanie zwykłego pola.
     */
    if (!selectedPath.groupKey) {
      const index = fields.findIndex(
        (field) => field._key === selectedPath.fieldKey,
      );

      if (index < 0) {
        return;
      }

      fields = fields.filter((field) => field._key !== selectedPath.fieldKey);

      const next = fields[Math.min(index, fields.length - 1)] || null;

      selectedPath = next
        ? {
            groupKey: null,
            fieldKey: next._key,
          }
        : null;

      render();

      markChanged("Pole zostanie usunięte po zapisaniu.");

      return;
    }

    /*
     * Usuwanie pola wewnątrz grupy.
     */
    const groupKey = selectedPath.groupKey;

    const fieldKey = selectedPath.fieldKey;

    fields = fields.map((group) => {
      if (group._key !== groupKey) {
        return group;
      }

      return {
        ...group,

        fields: (group.fields || []).filter((field) => field._key !== fieldKey),
      };
    });

    /*
     * Po usunięciu dziecka zaznaczamy
     * samą grupę.
     */
    selectedPath = {
      groupKey: null,
      fieldKey: groupKey,
    };

    render();

    markChanged("Pole grupy zostanie usunięte po zapisaniu.");
  };

  const createFieldData = (type, existingFields) => {
    const name = uniqueFieldName(existingFields, type);

    const fieldData = {
      id: null,
      name,
      label: FIELD_LABELS[type] || `Nowe pole ${name}`,
      type,
      required: false,
      width: "full",
      width_span: 12,
      placeholder: "",
      section: "",
      document_label: "",
      availability: config.workflowSteps.map((step, index) => ({
        step: step.id,
        visible: index === 0,
        editable: index === 0,
        required: false,
      })),
      document_usage: { declaration: false },
      options: OPTION_FIELD_TYPES.has(type) ? ["Opcja 1", "Opcja 2"] : [],
    };

    if (type === "repeatable_group") {
      fieldData.fields = [];
      fieldData.min_items = 1;
      fieldData.max_items = 20;
      fieldData.add_label = "Dodaj";
      fieldData.item_label = "Element";
      fieldData.options = [];
    }

    return fieldData;
  };

  /*
   * Dodawanie zwykłego pola.
   */
  const addField = (type, index = fields.length) => {
    const field = withKey(createFieldData(type, fields));

    fields.splice(Math.max(0, Math.min(index, fields.length)), 0, field);

    fields = [...fields];

    selectedPath = {
      groupKey: null,
      fieldKey: field._key,
    };

    render();

    markChanged("Dodano nowe pole. Zapisz formularz, aby utrwalić zmianę.");
  };

  /*
   * Dodawanie pola do repeatable_group.
   */
  const addNestedField = (groupKey, type, index = null) => {
    /*
     * P1: bez zagnieżdżania grup.
     */
    if (type === "repeatable_group") {
      return;
    }

    fields = fields.map((group) => {
      if (group._key !== groupKey) {
        return group;
      }

      const nestedFields = [...(group.fields || [])];

      const field = withKey(createFieldData(type, nestedFields));

      const targetIndex =
        index === null
          ? nestedFields.length
          : Math.max(0, Math.min(index, nestedFields.length));

      nestedFields.splice(targetIndex, 0, field);

      selectedPath = {
        groupKey,
        fieldKey: field._key,
      };

      return {
        ...group,
        fields: nestedFields,
      };
    });

    render();

    markChanged("Dodano pole do grupy.");
  };

  /*
   * Duplikowanie zwykłego pola.
   */
  const duplicate = (key) => {
    const effectiveKey =
      key ||
      (selectedPath && !selectedPath.groupKey ? selectedPath.fieldKey : null);

    if (!effectiveKey) {
      return;
    }

    const index = fields.findIndex((field) => field._key === effectiveKey);

    if (index < 0) {
      return;
    }

    const source = fields[index];

    /*
     * serializeFields usuwa techniczne _key,
     * dzięki czemu kopia dostanie nowe klucze,
     * również dla dzieci repeatable_group.
     */
    const serializedSource = serializeFields([source])[0];

    const copy = withKey({
      ...structuredClone(serializedSource),

      id: null,

      name: uniqueFieldName(fields, source.type, `${source.name}_kopia`),

      label: `${source.label} (kopia)`,
    });

    fields.splice(index + 1, 0, copy);

    fields = [...fields];

    selectedPath = {
      groupKey: null,
      fieldKey: copy._key,
    };

    render();
    markChanged("Pole zostało zduplikowane.");
  };

  /*
   * Duplikowanie pola wewnątrz grupy.
   */
  const duplicateNested = (groupKey, fieldKey) => {
    fields = fields.map((group) => {
      if (group._key !== groupKey) {
        return group;
      }

      const nestedFields = [...(group.fields || [])];

      const index = nestedFields.findIndex((field) => field._key === fieldKey);

      if (index < 0) {
        return group;
      }

      const source = nestedFields[index];

      const serializedSource = serializeFields([source])[0];

      const copy = withKey({
        ...structuredClone(serializedSource),

        id: null,

        name: uniqueFieldName(
          nestedFields,
          source.type,
          `${source.name}_kopia`,
        ),

        label: `${source.label} (kopia)`,
      });

      nestedFields.splice(index + 1, 0, copy);

      selectedPath = {
        groupKey,
        fieldKey: copy._key,
      };

      return {
        ...group,
        fields: nestedFields,
      };
    });

    render();

    markChanged("Pole grupy zostało zduplikowane.");
  };

  /*
   * Ruch zwykłego pola przyciskami.
   */
  const move = (key, delta) => {
    const from = fields.findIndex((field) => field._key === key);

    if (from < 0) {
      return;
    }

    const to = Math.max(0, Math.min(fields.length - 1, from + delta));

    if (from === to) {
      return;
    }

    const [field] = fields.splice(from, 1);

    fields.splice(to, 0, field);

    fields = [...fields];

    render();
    markChanged();
  };

  /*
   * Ruch pola wewnątrz grupy.
   */
  const moveNested = (groupKey, fieldKey, delta) => {
    fields = fields.map((group) => {
      if (group._key !== groupKey) {
        return group;
      }

      const nestedFields = [...(group.fields || [])];

      const from = nestedFields.findIndex((field) => field._key === fieldKey);

      if (from < 0) {
        return group;
      }

      const to = Math.max(0, Math.min(nestedFields.length - 1, from + delta));

      if (from === to) {
        return group;
      }

      const [field] = nestedFields.splice(from, 1);

      nestedFields.splice(to, 0, field);

      return {
        ...group,
        fields: nestedFields,
      };
    });

    render();
    markChanged();
  };

  /*
   * Drag & drop zwykłego pola.
   */
  const moveTo = (key, targetIndex) => {
    const from = fields.findIndex((field) => field._key === key);

    if (from < 0) {
      return;
    }

    const [field] = fields.splice(from, 1);

    const adjusted = from < targetIndex ? targetIndex - 1 : targetIndex;

    fields.splice(Math.max(0, Math.min(adjusted, fields.length)), 0, field);

    fields = [...fields];

    selectedPath = {
      groupKey: null,
      fieldKey: key,
    };

    render();
    markChanged();
  };

  /*
   * Docelowy handler D&D wewnątrz grupy.
   * Podepniemy go w drag-and-drop.js.
   */
  const moveNestedTo = (groupKey, fieldKey, targetIndex) => {
    fields = fields.map((group) => {
      if (group._key !== groupKey) {
        return group;
      }

      const nestedFields = [...(group.fields || [])];

      const from = nestedFields.findIndex((field) => field._key === fieldKey);

      if (from < 0) {
        return group;
      }

      const [field] = nestedFields.splice(from, 1);

      const adjusted = from < targetIndex ? targetIndex - 1 : targetIndex;

      nestedFields.splice(
        Math.max(0, Math.min(adjusted, nestedFields.length)),
        0,
        field,
      );

      return {
        ...group,
        fields: nestedFields,
      };
    });

    selectedPath = {
      groupKey,
      fieldKey,
    };

    render();
    markChanged();
  };

  const renderProperties = initializePropertiesPanel(panel, config, {
    update: updateSelected,

    remove: removeSelected,

    duplicate: () => {
      if (!selectedPath) {
        return;
      }

      if (selectedPath.groupKey) {
        duplicateNested(selectedPath.groupKey, selectedPath.fieldKey);
      } else {
        duplicate(selectedPath.fieldKey);
      }
    },

    close: () => {
      selectedPath = null;
      render();
    },
  });

  const render = () => {
    renderCanvas(canvas, fields, selectedPath, {
      /*
       * Pola główne.
       */
      select: selectTopLevel,

      update: (key, updates) => {
        selectedPath = {
          groupKey: null,
          fieldKey: key,
        };

        updateSelected(updates);
      },

      duplicate,

      remove: (key) => {
        selectedPath = {
          groupKey: null,
          fieldKey: key,
        };

        removeSelected();
      },

      move,

      /*
       * Pola repeatable_group.
       */
      selectNested,

      updateNested: (groupKey, fieldKey, updates) => {
        selectedPath = {
          groupKey,
          fieldKey,
        };

        updateSelected(updates);
      },

      addNested: addNestedField,

      duplicateNested,

      removeNested: (groupKey, fieldKey) => {
        selectedPath = {
          groupKey,
          fieldKey,
        };

        removeSelected();
      },

      moveNested,

      moveNestedTo,

      isNestedSelected: (groupKey, fieldKey) =>
        selectedPath?.groupKey === groupKey &&
        selectedPath?.fieldKey === fieldKey,
    });

    renderProperties(selected());

    builder
      .querySelector("[data-builder-empty]")
      .classList.toggle("is-visible", fields.length === 0);

    builder.querySelector("[data-field-count]").textContent =
      `${fields.length} ${fields.length === 1 ? "pole" : "pól"}`;

    stateInput.value = JSON.stringify(serializeFields(fields));
    positionPanels();
  };

  const addFieldFromPalette = (type) => {
    /*
     * Jeżeli zaznaczone jest pole
     * wewnątrz grupy, dodajemy kolejne
     * pole do tej grupy.
     */
    if (selectedPath?.groupKey && type !== "repeatable_group") {
      addNestedField(selectedPath.groupKey, type);

      return;
    }

    /*
     * Jeżeli zaznaczony jest sam
     * repeatable_group.
     */
    if (selectedPath && !selectedPath.groupKey) {
      const selectedField = fields.find(
        (field) => field._key === selectedPath.fieldKey,
      );

      if (
        selectedField?.type === "repeatable_group" &&
        type !== "repeatable_group"
      ) {
        addNestedField(selectedField._key, type);

        return;
      }
    }

    addField(type);
  };

  initializeFieldPalette(builder.querySelector("[data-field-palette]"), {
    addField: addFieldFromPalette,
  });

  /*
   * Na razie zachowujemy istniejące D&D
   * pól głównych.
   *
   * W następnym kroku rozszerzymy
   * drag-and-drop.js o pola nested.
   */
  initializeDragAndDrop(canvas, {
    moveField: moveTo,
    addField,
    addNestedField,
    moveNestedField: moveNestedTo,
  });

  builder.querySelectorAll("[data-builder-mode]").forEach((button) =>
    button.addEventListener("click", () => {
      const preview = button.dataset.builderMode === "preview";

      builder.classList.toggle("is-preview", preview);

      builder
        .querySelectorAll("[data-builder-mode]")
        .forEach((item) => item.classList.toggle("is-active", item === button));
      positionPanels();
    }),
  );

  builder.addEventListener("submit", () => {
    stateInput.value = JSON.stringify(serializeFields(fields));

    status.textContent = "Zapisywanie…";
  });

  render();
}
