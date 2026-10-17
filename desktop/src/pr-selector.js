import {searchField, iconButton, emptyState, skeleton} from "./ui/components.js";
// PRs come only from the authenticated central's configured GitHub repository.
export function prSelector({api, h, t, guard, repoInput, numInput, onSelect}) {
  let page = 1, serial = 0, cached = [], scope = "", nextPage = null, busy = false, listStale = false, error = "";
  let pressedKey = null;
  const alive = () => {try {guard(); return true;} catch {return false;}};
  const filter = h("select", {"aria-label": t("state"), onchange: () => loadList()},
    ...["open", "closed", "all"].map(value => h("option", {value}, t("pr_state_" + value))));
  const search = h("input", {type: "search", "aria-label": t("delivery_search_prs"), placeholder: t("delivery_search_prs"), oninput: () => render()});
  const refresh = iconButton({icon:"refresh-cw", label:t("delivery_refresh_prs"), onClick:() => activate({kind: "refresh"}, 1)});
  const list = h("div", {class: "pr-list", "aria-live": "polite"});
  const container = h("div", {class: "pr-selector"}, h("div", {class: "filters"}, searchField(search), filter, refresh), list);
  const shownEnabled = node => node && !node.disabled && !node.hidden && node.getClientRects().length > 0;
  const keyOf = node => {
    if (!node || node.nodeType !== 1) return null;
    const pageKey = node.getAttribute("data-pr-page");
    if (pageKey) return {attr: "data-pr-page", value: pageKey};
    const number = node.getAttribute("data-pr-number");
    if (number) return {attr: "data-pr-number", value: number};
    return null;
  };
  const sameKey = (node, key) => Boolean(key) && (key.kind === "refresh" ? node === refresh : node.getAttribute(key.attr) === key.value);
  const isPressed = node => pressedKey ? sameKey(node, pressedKey) : false;
  function arm(button, blocked) {
    if (busy && (isPressed(button) || !blocked)) {
      button.disabled = false;
      button.setAttribute("aria-disabled", "true");
    } else {
      button.removeAttribute("aria-disabled");
      button.disabled = Boolean(blocked);
    }
  }
  function fallback(from) {
    const controls = [...container.querySelectorAll("button, input, select")];
    const index = from ? controls.indexOf(from) : -1;
    const nextControl = controls.slice(index + 1).find(shownEnabled);
    if (nextControl) {nextControl.focus({preventScroll: true}); return;}
    list.tabIndex = -1;
    list.focus({preventScroll: true});
  }
  function successor(key) {
    const buttons = [...list.querySelectorAll("button")];
    const previous = buttons.find(node => node.getAttribute("data-pr-page") === "previous");
    const next = buttons.find(node => node.getAttribute("data-pr-page") === "next");
    if (key?.attr === "data-pr-page" && key.value === "next") {
      if (shownEnabled(previous)) {previous.focus({preventScroll: true}); return;}
      fallback(next || previous);
      return;
    }
    if (key?.attr === "data-pr-page" && key.value === "previous") {
      if (shownEnabled(next)) {next.focus({preventScroll: true}); return;}
      const first = buttons.find(node => node.hasAttribute("data-pr-number") && shownEnabled(node));
      if (first) {first.focus({preventScroll: true}); return;}
      if (shownEnabled(refresh)) {refresh.focus({preventScroll: true}); return;}
      fallback(previous || next);
      return;
    }
    fallback(refresh);
  }
  function restoreFocus(key) {
    if (!key) return;
    const match = [...list.querySelectorAll("button")].find(node => sameKey(node, key));
    if (match && shownEnabled(match)) {match.focus({preventScroll: true}); return;}
    const active = document.activeElement;
    // Replacing the list detaches the focused button. That loss belongs to this
    // render; a connected control outside the list means the person moved away.
    if (active && active !== document.body && active.isConnected) return;
    successor(key);
  }
  function render() {
    if (!alive()) return;
    const active = document.activeElement;
    const key = list.contains(active) ? keyOf(active) : null;
    list.replaceChildren();
    arm(refresh, false);
    if (busy) list.append(skeleton(2));
    if (error) list.append(h("p", {class: "note warn"}, error));
    if (scope) list.append(h("p", {class: "muted"}, `${scope} · ${t("delivery_pr_page", {page})}`));
    if (listStale && cached.length) list.append(h("p", {class: "note warn"}, t("delivery_pr_stale")));
    const query = search.value.trim().toLocaleLowerCase();
    const rows = cached.filter(pr => pr.title.toLocaleLowerCase().includes(query) || String(pr.number).includes(query));
    if (!rows.length && !busy) list.append(emptyState({icon:"git-pull-request", title:t("delivery_no_prs")}));
    for (const pr of rows) {
      const loaded = scope, revision = serial;
      const select = h("button", {type: "button", class: "secondary", "data-pr-number": String(pr.number), onclick: () => {
        if (!alive() || busy || listStale || revision !== serial || loaded !== scope) return;
        repoInput.value = loaded; numInput.value = String(pr.number); onSelect();
      }}, `#${pr.number} ${pr.title}`);
      arm(select, listStale);
      list.append(h("div", {class: "row"}, select, h("span", {class: "muted"},
        t("pr_state_" + pr.state), pr.draft ? ` · ${t("pr_draft")}` : "")));
    }
    if (scope) {
      const previous = h("button", {type: "button", class: "secondary", "data-pr-page": "previous", onclick: () => activate({attr: "data-pr-page", value: "previous"}, page - 1)}, t("pagination_prev"));
      const next = h("button", {type: "button", class: "secondary", "data-pr-page": "next", onclick: () => activate({attr: "data-pr-page", value: "next"}, nextPage)}, t("pagination_next"));
      arm(previous, listStale || page <= 1);
      arm(next, listStale || !nextPage);
      list.append(h("div", {class: "actions"}, previous, next));
    }
    restoreFocus(key);
    if ([refresh, ...list.querySelectorAll("button")].some(button => button.getAttribute("aria-disabled") === "true")) list.setAttribute("aria-busy", "true");
    else list.removeAttribute("aria-busy");
  }
  function activate(press, wanted) {
    if (busy) return;
    loadList(wanted, press);
  }
  async function loadList(wanted = 1, press = null) {
    if (!alive()) return;
    const mine = ++serial, repository = repoInput.value.trim(), selectedState = filter.value;
    pressedKey = press;
    error = "";
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository)) {
      busy = false; listStale = true; pressedKey = null; error = t("delivery_choose_repository"); render(); return;
    }
    busy = true; render();
    try {
      const result = await api("GET", `/repositories/${repository}/pulls?state=${selectedState}&page=${wanted}`);
      if (!alive() || mine !== serial) return;
      if (repoInput.value.trim() !== repository || filter.value !== selectedState) {busy = false; render(); return;}
      if (typeof result.loaded_scope !== "string" || result.loaded_scope.toLowerCase() !== repository.toLowerCase()
          || !Array.isArray(result.pulls) || result.pulls.length > 100 || result.pulls.some(pr =>
            !Number.isInteger(pr.number) || pr.number < 1 || pr.number > 999999999 || typeof pr.title !== "string"
            || !["open", "closed"].includes(pr.state))) throw Error(t("delivery_pr_invalid"));
      page = wanted; scope = result.loaded_scope; cached = result.pulls;
      nextPage = result.has_more && wanted < 1000 && result.next_page === wanted + 1 ? result.next_page : null;
      listStale = false;
    } catch (failure) {
      if (!alive() || mine !== serial) return;
      error = failure.message || String(failure);
      listStale = true;
    } finally {if (alive() && mine === serial) {busy = false; pressedKey = null; render();}}
  }
  repoInput.addEventListener("input", () => {serial++; busy = false; listStale = true; pressedKey = null; render();});
  return {container, loadList};
}
