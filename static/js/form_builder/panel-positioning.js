const DESKTOP_LAYOUT = "(min-width: 901px)";
const PANEL_GAP = 16;

export function clampedPanelOffset({
  targetTop,
  workspaceTop,
  workspaceHeight,
  panelHeight,
  gap = PANEL_GAP,
}) {
  const desired = targetTop - workspaceTop - gap;
  const maximum = Math.max(0, workspaceHeight - panelHeight);

  return Math.max(0, Math.min(desired, maximum));
}

export function initializePanelPositioning(builder) {
  const workspace = builder.querySelector(".form-builder__workspace");
  const panels = [
    builder.querySelector("[data-field-palette]"),
    builder.querySelector("[data-properties-panel]"),
  ].filter(Boolean);
  const desktopLayout = window.matchMedia(DESKTOP_LAYOUT);
  let animationFrame = null;

  if (!workspace || !panels.length) {
    return () => {};
  }

  const reset = () => {
    panels.forEach((panel) => {
      panel.style.removeProperty("--builder-panel-offset");
      panel.style.removeProperty("--builder-panel-max-height");
    });
  };

  const position = () => {
    animationFrame = null;

    if (!desktopLayout.matches || builder.classList.contains("is-preview")) {
      reset();
      return;
    }

    const selectedField = workspace.querySelector(
      ".form-builder__field.is-selected",
    );

    if (!selectedField) {
      reset();
      return;
    }

    const workspaceBounds = workspace.getBoundingClientRect();
    const selectedBounds = selectedField.getBoundingClientRect();
    const workspaceHeight = workspaceBounds.height;
    const panelMaxHeight = Math.min(
      workspaceHeight,
      Math.max(320, window.innerHeight - 32),
    );

    panels.forEach((panel) => {
      panel.style.setProperty(
        "--builder-panel-max-height",
        `${Math.max(0, panelMaxHeight)}px`,
      );
      const panelHeight = Math.min(
        panel.scrollHeight,
        panelMaxHeight,
      );
      const offset = clampedPanelOffset({
        targetTop: selectedBounds.top,
        workspaceTop: workspaceBounds.top,
        workspaceHeight,
        panelHeight,
      });

      panel.style.setProperty("--builder-panel-offset", `${offset}px`);
    });
  };

  const schedule = () => {
    if (animationFrame !== null) {
      window.cancelAnimationFrame(animationFrame);
    }
    animationFrame = window.requestAnimationFrame(position);
  };

  window.addEventListener("resize", schedule);
  desktopLayout.addEventListener?.("change", schedule);

  const resizeObserver = window.ResizeObserver
    ? new ResizeObserver(schedule)
    : null;
  resizeObserver?.observe(workspace);
  panels.forEach((panel) => resizeObserver?.observe(panel));

  schedule();
  return schedule;
}
