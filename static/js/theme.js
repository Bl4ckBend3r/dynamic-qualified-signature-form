(() => {
    "use strict";

    const STORAGE_KEY = "ui-theme";
    const THEMES = new Set(["light", "dark"]);
    const root = document.documentElement;

    const preferredTheme = () => (
        window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light"
    );

    const storedTheme = () => {
        try {
            const value = window.localStorage.getItem(STORAGE_KEY);
            return THEMES.has(value) ? value : null;
        } catch (_error) {
            return null;
        }
    };

    const updateToggle = (toggle, theme) => {
        const nextTheme = theme === "dark" ? "light" : "dark";
        const nextLabel = nextTheme === "dark" ? "Włącz ciemny motyw" : "Włącz jasny motyw";
        toggle.setAttribute("aria-label", nextLabel);
        toggle.setAttribute("title", nextLabel);
        toggle.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
        const icon = toggle.querySelector("[data-theme-icon]");
        if (icon) icon.textContent = theme === "dark" ? "☀" : "☾";
        const label = toggle.querySelector("[data-theme-label]");
        if (label) label.textContent = theme === "dark" ? "Jasny" : "Ciemny";
    };

    const applyTheme = (theme) => {
        const safeTheme = THEMES.has(theme) ? theme : preferredTheme();
        root.dataset.theme = safeTheme;
        root.style.colorScheme = safeTheme;
        document.querySelectorAll("[data-theme-toggle]").forEach((toggle) => {
            updateToggle(toggle, safeTheme);
        });
        return safeTheme;
    };

    const saveTheme = (theme) => {
        try {
            window.localStorage.setItem(STORAGE_KEY, theme);
        } catch (_error) {
            // The applied theme still works for this page when storage is unavailable.
        }
    };

    const initialTheme = applyTheme(storedTheme() || preferredTheme());

    const bindToggles = () => {
        applyTheme(root.dataset.theme || initialTheme);
        document.querySelectorAll("[data-theme-toggle]").forEach((toggle) => {
            if (toggle.dataset.themeBound === "true") return;
            toggle.dataset.themeBound = "true";
            toggle.addEventListener("click", () => {
                const nextTheme = root.dataset.theme === "dark" ? "light" : "dark";
                applyTheme(nextTheme);
                saveTheme(nextTheme);
            });
        });
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bindToggles, {once: true});
    } else {
        bindToggles();
    }
})();
