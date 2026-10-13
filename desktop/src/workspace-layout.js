import {icon} from "./ui/icons.js";

// Shared workspace layout controls: adjustable project sidebar and mobile conversation layout.
// Upstream references: Project Hub PR #2 (sidebar-resize.js) and mobile.js.
// Strictly maintains central authority, DOM structure, local drafts, focus, and bounded layout constraints.

export const DEFAULT_SIDEBAR_WIDTH = 248;
export const MIN_SIDEBAR_WIDTH = 200;
export const MAX_SIDEBAR_WIDTH = 500;
export const MIN_MAIN_WIDTH = 360;

export const KEY_SIDEBAR_WIDTH = "batc.sidebar.width";
export const KEY_MOBILE_INFO_COLLAPSED = "batc.mobile.info_collapsed";
export const KEY_MOBILE_COMPOSER_COLLAPSED = "batc.mobile.composer_collapsed";

function isMobileLayout() {
  if (typeof window === "undefined") return false;
  return (window.innerWidth || document.documentElement?.clientWidth || 0) < 1000;
}

export function calcSidebarBounds(windowWidth = window.innerWidth) {
  const winW = windowWidth || 1024;
  const maxAllowed = Math.min(MAX_SIDEBAR_WIDTH, Math.max(MIN_SIDEBAR_WIDTH, winW - MIN_MAIN_WIDTH));
  return { minAllowed: MIN_SIDEBAR_WIDTH, maxAllowed };
}

export function setupSidebarResizer({ aside, workspace, t }) {
  if (!aside || !workspace) return { dispose() {} };

  let savedWidth = window.innerWidth < 1200 ? 232 : DEFAULT_SIDEBAR_WIDTH;
  try {
    const raw = localStorage.getItem(KEY_SIDEBAR_WIDTH);
    if (raw !== null) {
      const parsed = parseInt(raw, 10);
      if (!Number.isNaN(parsed) && parsed >= MIN_SIDEBAR_WIDTH) {
        savedWidth = parsed;
      }
    }
  } catch {
    /* localStorage unavailable */
  }

  const resizer = document.createElement("div");
  resizer.className = "workspace-resizer";
  resizer.setAttribute("role", "separator");
  resizer.setAttribute("aria-orientation", "vertical");
  resizer.setAttribute("tabindex", "0");
  resizer.setAttribute("aria-label", t("workspace_resizer"));
  resizer.title = t("workspace_resizer_help");

  const applyWidth = (requestedWidth, persist = true) => {
    if (isMobileLayout()) {
      resizer.removeAttribute("aria-valuenow");
      return requestedWidth;
    }
    const { minAllowed, maxAllowed } = calcSidebarBounds();
    const clamped = Math.max(minAllowed, Math.min(maxAllowed, requestedWidth));
    workspace.style.setProperty("--workspace-sidebar-width", `${clamped}px`);
    resizer.setAttribute("aria-valuenow", String(clamped));
    resizer.setAttribute("aria-valuemin", String(minAllowed));
    resizer.setAttribute("aria-valuemax", String(maxAllowed));
    if (persist) {
      try {
        localStorage.setItem(KEY_SIDEBAR_WIDTH, String(clamped));
      } catch {
        /* storage unavailable */
      }
    }
    return clamped;
  };

  let currentWidth = applyWidth(savedWidth, false);

  let isDragging = false;
  let startX = 0;
  let startWidth = currentWidth;
  let activePointerId = null;

  const onGlobalKeyDown = event => {
    if (event.key === "Escape" && isDragging) {
      cancelDrag();
      event.preventDefault();
    }
  };

  const cancelDrag = () => {
    if (!isDragging) return;
    window.removeEventListener("keydown", onGlobalKeyDown);
    isDragging = false;
    currentWidth = applyWidth(startWidth, false);
    resizer.classList.remove("is-resizing");
    document.body.classList.remove("workspace-resizing");
    document.body.style.removeProperty("user-select");
    if (activePointerId !== null) {
      try {
        resizer.releasePointerCapture(activePointerId);
      } catch {
        /* pointer capture released */
      }
      activePointerId = null;
    }
  };

  const onPointerDown = event => {
    if (event.button !== 0 || isMobileLayout()) return;
    isDragging = true;
    activePointerId = event.pointerId;
    startX = event.clientX;
    startWidth = currentWidth;
    resizer.classList.add("is-resizing");
    document.body.classList.add("workspace-resizing");
    document.body.style.userSelect = "none";
    window.addEventListener("keydown", onGlobalKeyDown);
    try {
      resizer.setPointerCapture(event.pointerId);
    } catch {
      /* pointer capture unsupported */
    }
    event.preventDefault();
  };

  const onPointerMove = event => {
    if (!isDragging || event.pointerId !== activePointerId) return;
    const delta = event.clientX - startX;
    currentWidth = applyWidth(startWidth + delta, false);
  };

  const onPointerUp = event => {
    if (!isDragging || event.pointerId !== activePointerId) return;
    window.removeEventListener("keydown", onGlobalKeyDown);
    const delta = event.clientX - startX;
    currentWidth = applyWidth(startWidth + delta, true);
    isDragging = false;
    resizer.classList.remove("is-resizing");
    document.body.classList.remove("workspace-resizing");
    document.body.style.removeProperty("user-select");
    if (activePointerId !== null) {
      try {
        resizer.releasePointerCapture(activePointerId);
      } catch {
        /* pointer capture released */
      }
      activePointerId = null;
    }
  };

  const onPointerCancel = () => {
    cancelDrag();
  };

  const onKeyDown = event => {
    if (event.key === "Escape") {
      if (isDragging) {
        cancelDrag();
        event.preventDefault();
      }
      return;
    }
    if (isMobileLayout()) return;
    const { minAllowed, maxAllowed } = calcSidebarBounds();
    let handled = false;
    if (event.key === "ArrowLeft") {
      currentWidth = applyWidth(currentWidth - (event.shiftKey ? 48 : 16), true);
      handled = true;
    } else if (event.key === "ArrowRight") {
      currentWidth = applyWidth(currentWidth + (event.shiftKey ? 48 : 16), true);
      handled = true;
    } else if (event.key === "PageDown") {
      currentWidth = applyWidth(currentWidth - 48, true);
      handled = true;
    } else if (event.key === "PageUp") {
      currentWidth = applyWidth(currentWidth + 48, true);
      handled = true;
    } else if (event.key === "Home") {
      currentWidth = applyWidth(minAllowed, true);
      handled = true;
    } else if (event.key === "End") {
      currentWidth = applyWidth(maxAllowed, true);
      handled = true;
    }
    if (handled) {
      event.preventDefault();
      event.stopPropagation();
    }
  };

  const onDblClick = () => {
    if (!isMobileLayout()) {
      currentWidth = applyWidth(DEFAULT_SIDEBAR_WIDTH, true);
    }
  };

  const onWindowResize = () => {
    if (isMobileLayout()) cancelDrag();
    if (!isMobileLayout()) {
      currentWidth = applyWidth(currentWidth, false);
    }
  };

  resizer.addEventListener("pointerdown", onPointerDown);
  resizer.addEventListener("pointermove", onPointerMove);
  resizer.addEventListener("pointerup", onPointerUp);
  resizer.addEventListener("pointercancel", onPointerCancel);
  resizer.addEventListener("lostpointercapture", onPointerCancel);
  resizer.addEventListener("keydown", onKeyDown);
  resizer.addEventListener("dblclick", onDblClick);
  window.addEventListener("resize", onWindowResize);

  aside.appendChild(resizer);

  return {
    resizer,
    getWidth: () => currentWidth,
    setWidth: w => {
      currentWidth = applyWidth(w, true);
      return currentWidth;
    },
    dispose() {
      cancelDrag();
      resizer.removeEventListener("pointerdown", onPointerDown);
      resizer.removeEventListener("pointermove", onPointerMove);
      resizer.removeEventListener("pointerup", onPointerUp);
      resizer.removeEventListener("pointercancel", onPointerCancel);
      resizer.removeEventListener("lostpointercapture", onPointerCancel);
      resizer.removeEventListener("keydown", onKeyDown);
      resizer.removeEventListener("dblclick", onDblClick);
      window.removeEventListener("resize", onWindowResize);
      resizer.remove();
    }
  };
}

export function setupMobileSessionLayout({ head, composer, textarea, t, guard, infoRow = null, composerRow = null }) {
  if (!head || !composer) return { updateInfo() {}, updateDraft() {}, dispose() {} };

  let isDisposed = false;
  const alive = () => {
    if (isDisposed) return false;
    try {
      guard?.();
      return true;
    } catch {
      return false;
    }
  };

  // 1. Mobile conversation info collapse
  let isInfoCollapsed = false;
  try {
    isInfoCollapsed = localStorage.getItem(KEY_MOBILE_INFO_COLLAPSED) === "true";
  } catch {
    /* localStorage unavailable */
  }

  const infoToggle = document.createElement("button");
  infoToggle.type = "button";
  infoToggle.className = "mini workspace-info-toggle";

  const renderInfoToggle = () => {
    if (!alive()) return;
    head.classList.toggle("workspace-info-collapsed", isInfoCollapsed);
    infoToggle.setAttribute("aria-expanded", String(!isInfoCollapsed));
    const label = t(isInfoCollapsed ? "mobile_info_expand" : "mobile_info_collapse");
    infoToggle.setAttribute("aria-label", label);
    infoToggle.replaceChildren(icon(isInfoCollapsed ? "chevron-down" : "chevron-up"));
  };

  infoToggle.addEventListener("click", () => {
    if (!alive()) return;
    isInfoCollapsed = !isInfoCollapsed;
    try {
      localStorage.setItem(KEY_MOBILE_INFO_COLLAPSED, String(isInfoCollapsed));
    } catch {
      /* localStorage unavailable */
    }
    renderInfoToggle();
  });

  const updateInfo = () => {
    if (!alive()) return;
    if (infoRow) {
      if (!infoRow.contains(infoToggle)) infoRow.append(infoToggle);
    } else if (!head.contains(infoToggle)) head.append(infoToggle);
    renderInfoToggle();
  };

  // 2. Mobile composer collapse & draft indicator
  let isComposerCollapsed = false;
  try {
    isComposerCollapsed = localStorage.getItem(KEY_MOBILE_COMPOSER_COLLAPSED) === "true";
  } catch {
    /* localStorage unavailable */
  }

  const composerToggle = document.createElement("button");
  composerToggle.type = "button";
  composerToggle.className = "mini workspace-composer-toggle";
  composerToggle.setAttribute("aria-controls", "workspace-message-input");

  const renderComposerToggle = () => {
    if (!alive()) return;
    const text = textarea?.value || "";
    const hasDraft = text.trim().length > 0;
    composer.classList.toggle("workspace-composer-collapsed", isComposerCollapsed);
    composerToggle.setAttribute("aria-expanded", String(!isComposerCollapsed));

    const baseText = t(isComposerCollapsed ? "mobile_compose_open" : "mobile_compose_close");
    const draftSuffix = hasDraft ? ` · ${t("mobile_draft_indicator")}` : "";
    composerToggle.replaceChildren(icon(isComposerCollapsed ? "chevron-down" : "chevron-up"));
    composerToggle.setAttribute("aria-label", baseText + draftSuffix);
    composerToggle.classList.toggle("has-draft", hasDraft);
  };

  composerToggle.addEventListener("click", () => {
    if (!alive()) return;
    isComposerCollapsed = !isComposerCollapsed;
    try {
      localStorage.setItem(KEY_MOBILE_COMPOSER_COLLAPSED, String(isComposerCollapsed));
    } catch {
      /* localStorage unavailable */
    }
    renderComposerToggle();
    if (!isComposerCollapsed) {
      textarea?.focus({ preventScroll: true });
    } else {
      composerToggle.focus({preventScroll: true});
    }
  });

  const onTextareaInput = () => {
    renderComposerToggle();
  };

  textarea?.addEventListener("input", onTextareaInput);
  textarea?.addEventListener("change", onTextareaInput);

  if (composerRow) {
    if (!composerRow.contains(composerToggle)) composerRow.append(composerToggle);
  } else if (!composer.contains(composerToggle)) composer.prepend(composerToggle);

  // 3. visualViewport keyboard adaptation (ignoring pinch-zoom)
  const updateViewport = () => {
    if (!alive()) return;
    const isMob = isMobileLayout();
    const vv = window.visualViewport;
    if (!isMob || !vv) {
      document.body.classList.remove("mobile-keyboard");
      document.documentElement.style.removeProperty("--workspace-visible-height");
      return;
    }

    // Pinch-zoom protection: when pinch zooming, scale is not 1.
    // We must NOT treat pinch zoom as a virtual keyboard.
    const isPinchZoom = Math.abs(vv.scale - 1) > 0.05;
    if (isPinchZoom) {
      document.body.classList.remove("mobile-keyboard");
      document.documentElement.style.removeProperty("--workspace-visible-height");
      return;
    }

    // When virtual keyboard opens on mobile, scale is ~1 and visualViewport height drops
    const isKeyboard = vv.height < (window.innerHeight - 80);
    document.body.classList.toggle("mobile-keyboard", isKeyboard);
    if (isKeyboard) {
      document.documentElement.style.setProperty("--workspace-visible-height", `${vv.height}px`);
      if (window.scrollY || vv.offsetTop) {
        window.scrollTo(0, 0);
      }
    } else {
      document.documentElement.style.removeProperty("--workspace-visible-height");
    }
  };

  const onFocusIn = () => requestAnimationFrame(updateViewport);
  const onFocusOut = () => requestAnimationFrame(updateViewport);

  window.visualViewport?.addEventListener("resize", updateViewport);
  window.visualViewport?.addEventListener("scroll", updateViewport);
  window.addEventListener("resize", updateViewport);
  textarea?.addEventListener("focusin", onFocusIn);
  textarea?.addEventListener("focusout", onFocusOut);

  // Initial renders
  updateInfo();
  renderComposerToggle();
  updateViewport();

  return {
    infoToggle,
    composerToggle,
    updateInfo,
    updateDraft: renderComposerToggle,
    dispose() {
      isDisposed = true;
      window.visualViewport?.removeEventListener("resize", updateViewport);
      window.visualViewport?.removeEventListener("scroll", updateViewport);
      window.removeEventListener("resize", updateViewport);
      textarea?.removeEventListener("input", onTextareaInput);
      textarea?.removeEventListener("change", onTextareaInput);
      textarea?.removeEventListener("focusin", onFocusIn);
      textarea?.removeEventListener("focusout", onFocusOut);
      infoToggle.remove();
      composerToggle.remove();
      head.classList.remove("workspace-info-collapsed");
      composer.classList.remove("workspace-composer-collapsed");
      document.body.classList.remove("mobile-keyboard");
      document.documentElement.style.removeProperty("--workspace-visible-height");
    }
  };
}
