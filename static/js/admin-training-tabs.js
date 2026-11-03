
(() => {
    "use strict";

    const page = document.querySelector(
        ".training-detail-page"
    );

    const nav = page?.querySelector(
        "[data-training-tabs]"
    );

    if (!page || !nav) return;

    const tabs = [
        ...nav.querySelectorAll("a[data-training-tab]")
    ].filter((tab) => {
        const id = tab.getAttribute("href")?.slice(1);

        return id && page.querySelector(
            `.training-section#${CSS.escape(id)}`
        );
    });

    if (!tabs.length) return;

    const entries = tabs.map((tab) => {
        const id = tab.getAttribute("href").slice(1);

        const panel = page.querySelector(
            `.training-section#${CSS.escape(id)}`
        );

        tab.id = `training-tab-${id}`;

        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-controls", id);

        panel.setAttribute("role", "tabpanel");
        panel.setAttribute("aria-labelledby", tab.id);
        panel.setAttribute("tabindex", "0");

        return { tab, panel, id };
    });

    nav.setAttribute("role", "tablist");

    function activate(
        id,
        { focus = false, updateUrl = false } = {}
    ) {
        const active =
            entries.find((item) => item.id === id) ||
            entries[0];

        entries.forEach(({ tab, panel }) => {
            const selected = tab === active.tab;

            tab.setAttribute(
                "aria-selected",
                String(selected)
            );

            tab.tabIndex = selected ? 0 : -1;

            panel.hidden = !selected;
        });

        if (
            updateUrl &&
            window.location.hash !== `#${active.id}`
        ) {
            window.history.pushState(
                null,
                "",
                `#${active.id}`
            );
        }

        if (focus) {
            active.tab.focus();
        }
    }

    /* Początkowa zakładka */

    const initialId = decodeURIComponent(
        window.location.hash.slice(1)
    );

    activate(initialId);

    page.setAttribute(
        "data-training-tabs-ready",
        "true"
    );

    /* Kliknięcie zakładki */

    nav.addEventListener("click", (event) => {
        const tab = event.target.closest(
            "a[data-training-tab]"
        );

        if (
            !tab ||
            !nav.contains(tab) ||
            event.defaultPrevented ||
            event.button !== 0 ||
            event.metaKey ||
            event.ctrlKey ||
            event.shiftKey ||
            event.altKey
        ) {
            return;
        }

        event.preventDefault();

        activate(
            tab.getAttribute("href").slice(1),
            { updateUrl: true }
        );
    });

    /* Nawigacja klawiaturą */

    nav.addEventListener("keydown", (event) => {
        const index = entries.findIndex(
            ({ tab }) => tab === document.activeElement
        );

        if (index === -1) return;

        if (event.key === " ") {
            event.preventDefault();
            entries[index].tab.click();
            return;
        }

        let next = index;

        if (event.key === "ArrowRight") {
            next = (index + 1) % entries.length;
        } else if (event.key === "ArrowLeft") {
            next = (index - 1 + entries.length) %
                   entries.length;
        } else if (event.key === "Home") {
            next = 0;
        } else if (event.key === "End") {
            next = entries.length - 1;
        } else {
            return;
        }

        event.preventDefault();

        activate(entries[next].id, {
            focus: true,
            updateUrl: true
        });
    });

    /* Obsługa cofania i linków do zakładek */

    const syncWithUrl = () => {
        activate(
            decodeURIComponent(
                window.location.hash.slice(1)
            )
        );
    };

    window.addEventListener(
        "popstate",
        syncWithUrl
    );

    window.addEventListener(
        "hashchange",
        syncWithUrl
    );
})();
