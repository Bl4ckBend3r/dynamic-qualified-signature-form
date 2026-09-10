export function initializeDragAndDrop(
  canvas,
  {
    moveField,
    addField,
    addNestedField,
    moveNestedField,
  },
) {
  let activePayload = null;
  let marker = null;

  const clearMarker = () => {
    marker?.element?.classList.remove(
      "is-drop-before",
      "is-drop-after",
      "is-drop-target",
    );

    canvas
      .querySelectorAll(".is-drop-target")
      .forEach((element) =>
        element.classList.remove(
          "is-drop-target",
        ),
      );

    marker = null;
  };

  /*
   * Zapamiętujemy przeciągany element.
   */
  document.addEventListener(
    "dragstart",
    (event) => {
      const paletteButton =
        event.target.closest?.(
          "[data-add-field]",
        );

      if (paletteButton) {
        activePayload =
          `new:${paletteButton.dataset.addField}`;

        return;
      }

      const item =
        event.target.closest?.(
          "[data-field-key]",
        );

      if (!item) {
        return;
      }

      const parentGroupKey =
        item.dataset.parentGroupKey;

      if (parentGroupKey) {
        activePayload =
          `nested:${parentGroupKey}:${item.dataset.fieldKey}`;
      } else {
        activePayload =
          `field:${item.dataset.fieldKey}`;
      }

      item.classList.add(
        "is-dragging",
      );
    },
  );

  document.addEventListener(
    "dragend",
    () => {
      activePayload = null;

      canvas
        .querySelectorAll(".is-dragging")
        .forEach((element) =>
          element.classList.remove(
            "is-dragging",
          ),
        );

      clearMarker();
    },
  );

  canvas.addEventListener(
    "dragover",
    (event) => {
      if (!activePayload) {
        return;
      }

      /*
       * Szukamy CAŁEGO kafelka
       * repeatable_group.
       */
      const groupItem =
        event.target.closest(
          '[data-field-type="repeatable_group"][data-field-key]',
        );

      /*
       * PALETA -> GRUPA
       */
      if (
        groupItem &&
        activePayload.startsWith("new:")
      ) {
        const type =
          activePayload.slice(4);

        /*
         * Bez grupy wewnątrz grupy.
         */
        if (type === "repeatable_group") {
          return;
        }

        event.preventDefault();
        event.stopPropagation();

        event.dataTransfer.dropEffect =
          "copy";

        clearMarker();

        const groupKey =
          groupItem.dataset.fieldKey;

        groupItem.classList.add(
          "is-drop-target",
        );

        const nestedItem =
          event.target.closest(
            '[data-nested-field="true"]',
          );

        /*
         * Jeżeli kursor jest nad
         * konkretnym dzieckiem,
         * wyliczamy pozycję.
         */
        if (
          nestedItem &&
          nestedItem.dataset.parentGroupKey ===
            groupKey
        ) {
          const rect =
            nestedItem.getBoundingClientRect();

          const after =
            event.clientY >
            rect.top +
              rect.height / 2;

          nestedItem.classList.add(
            after
              ? "is-drop-after"
              : "is-drop-before",
          );

          marker = {
            type: "nested",
            groupKey,
            index:
              Number(
                nestedItem.dataset
                  .fieldIndex,
              ) +
              (after ? 1 : 0),
            element: nestedItem,
          };

          return;
        }

        /*
         * Upuszczenie na dowolnym
         * innym miejscu grupy =
         * dodanie na końcu.
         */
        const nestedFields =
          groupItem.querySelectorAll(
            '[data-nested-field="true"]',
          );

        marker = {
          type: "nested",
          groupKey,
          index: nestedFields.length,
          element: groupItem,
        };

        return;
      }

      /*
       * ZMIANA KOLEJNOŚCI
       * WEWNĄTRZ GRUPY.
       */
      if (
        groupItem &&
        activePayload.startsWith(
          "nested:",
        )
      ) {
        const [
          ,
          sourceGroupKey,
        ] = activePayload.split(":");

        const groupKey =
          groupItem.dataset.fieldKey;

        /*
         * Na tym etapie tylko
         * w tej samej grupie.
         */
        if (
          sourceGroupKey !== groupKey
        ) {
          return;
        }

        event.preventDefault();
        event.stopPropagation();

        event.dataTransfer.dropEffect =
          "move";

        clearMarker();

        groupItem.classList.add(
          "is-drop-target",
        );

        const nestedItem =
          event.target.closest(
            '[data-nested-field="true"]',
          );

        if (
          nestedItem &&
          nestedItem.dataset.parentGroupKey ===
            groupKey
        ) {
          const rect =
            nestedItem.getBoundingClientRect();

          const after =
            event.clientY >
            rect.top +
              rect.height / 2;

          nestedItem.classList.add(
            after
              ? "is-drop-after"
              : "is-drop-before",
          );

          marker = {
            type: "nested",
            groupKey,
            index:
              Number(
                nestedItem.dataset
                  .fieldIndex,
              ) +
              (after ? 1 : 0),
            element: nestedItem,
          };

          return;
        }

        marker = {
          type: "nested",
          groupKey,
          index:
            groupItem.querySelectorAll(
              '[data-nested-field="true"]',
            ).length,
          element: groupItem,
        };

        return;
      }

      /*
       * Pola nested nie wychodzą
       * poza grupę.
       */
      if (
        activePayload.startsWith(
          "nested:",
        )
      ) {
        return;
      }

      /*
       * FORMULARZ GŁÓWNY.
       */
      event.preventDefault();

      event.dataTransfer.dropEffect =
        activePayload.startsWith("new:")
          ? "copy"
          : "move";

      clearMarker();

      let item =
        event.target.closest(
          "[data-field-key]",
        );

      /*
       * Jeżeli trafiliśmy w dziecko
       * grupy, przechodzimy do
       * nadrzędnego repeatable_group.
       */
      if (
        item?.dataset.parentGroupKey
      ) {
        item =
          item.closest(
            '[data-field-type="repeatable_group"]',
          );
      }

      if (!item) {
        marker = {
          type: "top",
          index:
            canvas.querySelectorAll(
              ':scope > [data-field-key]',
            ).length,
          element: null,
        };

        return;
      }

      const rect =
        item.getBoundingClientRect();

      const after =
        event.clientY >
        rect.top +
          rect.height / 2;

      item.classList.add(
        after
          ? "is-drop-after"
          : "is-drop-before",
      );

      marker = {
        type: "top",
        index:
          Number(
            item.dataset.fieldIndex,
          ) +
          (after ? 1 : 0),
        element: item,
      };
    },
  );

  canvas.addEventListener(
    "drop",
    (event) => {
      if (!activePayload) {
        return;
      }

      const currentMarker =
        marker;

      if (!currentMarker) {
        return;
      }

      event.preventDefault();
      event.stopPropagation();

      const payload =
        activePayload;

      clearMarker();

      /*
       * DROP DO GRUPY.
       */
      if (
        currentMarker.type ===
        "nested"
      ) {
        if (
          payload.startsWith("new:")
        ) {
          const type =
            payload.slice(4);

          if (
            type === "repeatable_group"
          ) {
            return;
          }

          addNestedField(
            currentMarker.groupKey,
            type,
            currentMarker.index,
          );

          return;
        }

        if (
          payload.startsWith(
            "nested:",
          )
        ) {
          const [
            ,
            sourceGroupKey,
            fieldKey,
          ] = payload.split(":");

          if (
            sourceGroupKey !==
            currentMarker.groupKey
          ) {
            return;
          }

          moveNestedField(
            currentMarker.groupKey,
            fieldKey,
            currentMarker.index,
          );

          return;
        }
      }

      /*
       * DROP NA FORMULARZ GŁÓWNY.
       */
      if (
        currentMarker.type === "top"
      ) {
        if (
          payload.startsWith("new:")
        ) {
          addField(
            payload.slice(4),
            currentMarker.index,
          );

          return;
        }

        if (
          payload.startsWith("field:")
        ) {
          moveField(
            payload.slice(6),
            currentMarker.index,
          );
        }
      }
    },
  );
}