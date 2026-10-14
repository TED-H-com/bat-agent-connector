import {searchField, iconButton, emptyState, skeleton} from './ui/components.js';
// PRs come only from the authenticated central's configured GitHub repository.
export function prSelector({api, h, t, guard, repoInput, numInput, onSelect}) {
  let page = 1, serial = 0, cached = [], scope = "", nextPage = null, busy = false, stale = false, error = "";
  const alive = () => {try {guard(); return true;} catch {return false;}};
  const filter = h("select", {"aria-label": t("state"), onchange: () => loadList()},
    ...["open", "closed", "all"].map(value => h("option", {value}, t("pr_state_" + value))));
  const search = h("input", {type: "search", "aria-label": t("delivery_search_prs"), placeholder: t("delivery_search_prs"), oninput: () => render()});
  const refresh = iconButton({icon:"refresh-cw", label:t("delivery_refresh_prs"), onClick:() => loadList()});
  const list = h("div", {class: "pr-list", "aria-live": "polite"});
  const container = h("div", {class: "pr-selector"}, h("div", {class: "filters"}, searchField(search), filter, refresh), list);
  function render() {
    if (!alive()) return;
    list.replaceChildren(); refresh.disabled = busy;
    if (busy) list.append(skeleton(2));
    if (error) list.append(h("p", {class: "note warn"}, error));
    if (scope) list.append(h("p", {class: "muted"}, `${scope} · ${t("delivery_pr_page", {page})}`));
    if (stale && cached.length) list.append(h("p", {class: "note warn"}, t("delivery_pr_stale")));
    const query = search.value.trim().toLocaleLowerCase();
    const rows = cached.filter(pr => pr.title.toLocaleLowerCase().includes(query) || String(pr.number).includes(query));
    if (!rows.length && !busy) list.append(emptyState({icon:"git-pull-request", title:t("delivery_no_prs")}));
    for (const pr of rows) {
      const loaded = scope, revision = serial;
      const select = h("button", {type: "button", class: "secondary", disabled: busy || stale,
        onclick: () => {
          if (!alive() || busy || stale || revision !== serial || loaded !== scope) return;
          repoInput.value = loaded; numInput.value = String(pr.number); onSelect();
        }}, `#${pr.number} ${pr.title}`);
      list.append(h("div", {class: "row"}, select, h("span", {class: "muted"},
        t("pr_state_" + pr.state), pr.draft ? ` · ${t("pr_draft")}` : "")));
    }
    if (scope) list.append(h("div", {class: "actions"},
      h("button", {type: "button", class: "secondary", disabled: busy || stale || page <= 1, onclick: () => loadList(page - 1)}, t("pagination_prev")),
      h("button", {type: "button", class: "secondary", disabled: busy || stale || !nextPage, onclick: () => loadList(nextPage)}, t("pagination_next"))));
  }
  async function loadList(wanted = 1) {
    if (!alive()) return;
    const mine = ++serial, repository = repoInput.value.trim(), selectedState = filter.value;
    stale = true; error = "";
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository)) {
      busy = false; error = t("delivery_choose_repository"); render(); return;
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
      stale = false;
    } catch (failure) {
      if (!alive() || mine !== serial) return;
      error = failure.message || String(failure);
    } finally {if (alive() && mine === serial) {busy = false; render();}}
  }
  repoInput.addEventListener("input", () => {serial++; busy = false; stale = true; render();});
  return {container, loadList};
}
