import {fleetStatus} from "./fleet-status.js";
import {fleetControl, fleetRequest} from "./transport/index.ts";

export async function mountFleetDesktop(main, {h, t}) {
  const panel = h("section", {class: "panel fleet-desktop", "aria-label": t("fleet_desktop_title")});
  const content = h("div"), message = h("p", {class: "muted", role: "status"});
  panel.append(h("h2", {}, t("fleet_desktop_title")), h("p", {class: "muted"}, t("fleet_independent")), content, message);
  if (!main.isConnected) return () => {};
  main.append(panel);
  let disposed = false, busy = false, readable = false, snapshot, draft, binding, choicePreview, launch, migration, timer, pressed = null;
  let backend = "rust", autostart = false;
  const alive = () => !disposed && panel.isConnected;
  const key = () => `batc.desktop.fleet.controls.${binding}`;
  const choices = value => ({connections: [...value.connections], profiles: [...value.profiles], dashboard: value.dashboard});
  const equivalent = (a, b) => a && b && a.dashboard === b.dashboard && ["connections", "profiles"].every(k => Array.isArray(a[k]) && Array.isArray(b[k]) && [...a[k]].sort().join("\0") === [...b[k]].sort().join("\0"));
  const validId = id => typeof id === "string" && /^[0-9a-f]{32}$/.test(id);
  const validSummary = (s,id) => s?.launch_id === id && Array.isArray(s.profiles) && s.profiles.every(v=>typeof v === "string") && typeof s.dashboard === "boolean" && typeof s.opens_bat === "boolean";
  const knownLaunch = () => ["started","not_started","no_bat","already_running"].includes(launch?.receipt?.state);
  const acceptLaunch = value => {
    if (!launch || !value) return;
    const receipt = value.summary || value;
    if (receipt.launch_id !== launch.preview_id || !Array.isArray(receipt.profiles) || typeof receipt.dashboard !== "boolean"
      || launch.summary && (JSON.stringify(receipt.profiles) !== JSON.stringify(launch.summary.profiles) || receipt.dashboard !== launch.summary.dashboard)) throw new Error(t("fleet_unknown"));
    if (!["prepared","uncertain","started","not_started","no_bat","already_running"].includes(value.state)) throw new Error(t("fleet_unknown"));
    launch.receipt = value;
  };
  const acceptMigration = value => {if (!migration || value?.id !== migration.preview_id || typeof value.phase !== "string") throw new Error(t("fleet_unknown")); migration.receipt=value;};
  const save = () => {try {sessionStorage.setItem(key(), JSON.stringify({draft, launch, migration}));} catch {/* Mounted state stays available. */}};
  const canEnable = () => readable && snapshot?.configuration.valid && !snapshot.pending_migration
    && (snapshot.monitor.state === "stopped" || snapshot.monitor.controllable);
  const stale = () => draft && (draft.revision !== snapshot.selection.revision || draft.epoch !== snapshot.monitor.epoch);
  const discardPreview = () => {if (choicePreview) fleetControl({action:"discard", preview_id:choicePreview.preview_id}).catch(() => {}); choicePreview = null;};
  const edit = (field, value) => {
    if (busy || !canEnable() || stale()) return;
    discardPreview(); draft ||= {...choices(snapshot.selection), revision:snapshot.selection.revision, epoch:snapshot.monitor.epoch};
    draft[field] = value; if (equivalent(draft, snapshot.selection)) draft = null;
    save(); render();
  };
  const accept = value => {
    if (value?.control_version !== 1 || !value.configuration?.binding || !Array.isArray(value.profiles)
      || !Array.isArray(value.selection?.profiles) || typeof value.selection.dashboard !== "boolean"
      || !value.monitor || !value.login || !value.readiness) throw new Error(t("fleet_unavailable"));
    if (binding !== value.configuration.binding) {
      binding = value.configuration.binding; backend = value.backend; draft = choicePreview = launch = migration = null;
      try {
        const old = JSON.parse(sessionStorage.getItem(key()) || "null");
        if (old?.draft && Array.isArray(old.draft.connections) && Array.isArray(old.draft.profiles)
          && [...old.draft.connections, ...old.draft.profiles].every(v => typeof v === "string") && typeof old.draft.dashboard === "boolean") draft = old.draft;
        if (validId(old?.launch?.preview_id)) launch = {preview_id:old.launch.preview_id,attempted:old.launch.attempted === true,
          summary:validSummary(old.launch.summary,old.launch.preview_id)?old.launch.summary:null,receipt:null};
        if (launch?.summary && ["no_bat","already_running"].includes(old?.launch?.receipt?.state)) acceptLaunch(old.launch.receipt);
        if (validId(old?.migration?.preview_id) && (old.migration.accepted === true || /^[0-9a-f]{64}$/.test(old.migration.fingerprint))) migration = {...old.migration,receipt:null};
      } catch {/* Invalid drafts never authorize a request. */}
    }
    snapshot = value; readable = true;
    const automatic=value.login_launch;
    if (!launch && automatic?.configuration_binding === binding && validId(automatic.preview_id) && validSummary(automatic.summary,automatic.preview_id)) {
      launch={preview_id:automatic.preview_id,summary:automatic.summary,attempted:true};
      if (automatic.receipt) acceptLaunch(automatic.receipt);
      save();
    }
    if (draft && equivalent(draft, value.selection)) {draft = null; discardPreview(); save();}
    if (validId(value.pending_migration) && migration?.preview_id !== value.pending_migration) {
      migration = {preview_id:value.pending_migration, accepted:true}; save();
    }
  };
  const run = async (fn, quiet = false, action = null) => {
    if (!alive() || busy) return;
    if (action) pressed = action;
    busy = true;
    if (!quiet) message.textContent = t("fleet_working");
    render();
    try {await fn(); if (alive() && !quiet) message.textContent = "";}
    catch (error) {if (alive()) message.textContent = `${t("fleet_unknown")} ${String(error)}`;}
    finally {busy = false; pressed = null; if (alive()) {save(); render();}}
  };
  const read = async () => {
    try {
      const value = await fleetControl({action:"overview"}); if (!alive()) return; accept(value);
      if (launch?.attempted) {
        const receipt = await fleetControl({action:"launch_status",launch_id:launch.preview_id});
        if (alive() && receipt) {
          acceptLaunch(receipt);
        }
      }
      if (migration?.accepted) {
        const receipt = await fleetControl({action:"migration_status",migration_id:migration.preview_id});
        if (alive()) acceptMigration(receipt);
      }
    } catch (error) {readable = false; throw error;}
  };
  const reviewChoices = () => run(async () => {
    const value = await fleetControl({action:"preview_choices", choices:choices(draft), configuration_binding:binding,
      selection_revision:draft.revision, monitor_epoch:draft.epoch});
    if (alive()) {if (!validId(value.preview_id) || !equivalent(value.summary?.choices, draft)) throw new Error(t("fleet_unknown")); choicePreview = value;}
  }, false, "choices");
  const launchApply = () => run(async () => {
    launch.attempted = true; save();
    const value = await fleetControl({action:"launch",preview_id:launch.preview_id});
    if (alive()) acceptLaunch(value);
  }, false, "launch");
  const label = id => id === "default" ? t("fleet_local_bat") : snapshot.profiles.find(row => row.id === id)?.label || snapshot.configuration.connections.find(row => row.name === id)?.label || id;
  const gate = (blocked, action) => busy && action && pressed === action ? {"aria-disabled": "true"} : blocked ? {disabled: true} : busy ? {"aria-disabled": "true"} : {};
  const focusKeys = ["data-fleet-action", "data-fleet-choice", "data-fleet-login", "data-fleet-autostart", "data-fleet-backend"];
  const focusKey = node => {
    for (let current = node; current && current !== panel; current = current.parentElement) {
      for (const attr of focusKeys) if (current.hasAttribute?.(attr)) return [attr, current.getAttribute(attr)];
    }
    return null;
  };
  const controlByKey = key => {
    if (!key) return null;
    for (const node of content.querySelectorAll("button, input, select")) if (node.getAttribute(key[0]) === key[1]) return node;
    return null;
  };
  const groups = [
    ["start", "quit"],
    ["choices", "use-current", "refresh"],
    ["review-launch", "launch", "refresh-launch", "new-launch", "cancel-launch"],
    ["review-migration", "apply-migration", "continue-migration", "refresh-migration", "retry-migration", "new-migration", "cancel-migration", "restore-migration"],
  ];
  const successor = key => {
    if (key?.[0] === "data-fleet-action") {
      const group = groups.find(items => items.includes(key[1]));
      if (group) for (const candidate of group.slice(group.indexOf(key[1]) + 1)) {
        const node = controlByKey(["data-fleet-action", candidate]);
        if (node && !node.disabled && node.getAttribute("aria-disabled") !== "true") return node;
      }
    }
    message.tabIndex = -1;
    return message;
  };
  const render = () => {
    if (!alive() || !snapshot) return;
    const previous = panel.contains(document.activeElement) ? focusKey(document.activeElement) : null;
    const selected = draft || snapshot.selection, ready = new Map([...snapshot.readiness.hosts, snapshot.readiness.connector].filter(Boolean).map(row => [row.name,row]));
    const checkbox = (field, id, text) => {
      const checked = field === "dashboard" ? selected.dashboard : selected[field].includes(id);
      const blocked = !canEnable() || !!stale();
      return h("label", {class: "fleet-choice"}, h("input", {type: "checkbox", checked, ...gate(blocked), "data-fleet-choice": field + ":" + id,
        onkeydown: event => { if (busy && event.key === " ") event.preventDefault(); },
        onclick: event => { if (busy) event.preventDefault(); },
        onchange: event => {
          if (busy || blocked) { event.target.checked = checked; return; }
          edit(field, field === "dashboard" ? event.target.checked : event.target.checked ? [...selected[field], id] : selected[field].filter(value => value !== id));
        }}), " ", text);
    };
    const button = (action, text, fn, blocked = false, primary = false) => h("button", {class: primary ? "primary" : "secondary", ...gate(blocked, action),
      "data-fleet-action": action, onclick: () => { if (busy || blocked) return; fn(); }}, t(text));
    const fields = [
      h("p", {"data-fleet-current-backend": true}, t("delivery_fleet_current_backend", {backend: t(["rust", "powershell"].includes(snapshot.backend) ? "fleet_backend_" + snapshot.backend : "obs_unknown")})),
      snapshot.backend === "powershell" ? h("p", {class: "note"}, t("delivery_fleet_legacy_default")) : null,
      h("p", {}, t("fleet_monitor_"+snapshot.monitor.state), " · ", t("fleet_"+snapshot.readiness.state)),
      snapshot.monitor.state === "running" && !snapshot.monitor.controllable ? h("p",{class:"note"},t("fleet_other_owner")):null,
      snapshot.login_launch?h("p",{class:"note"},t("fleet_login_"+snapshot.login_launch.state),snapshot.login_launch.code?` (${snapshot.login_launch.code})`:null):null,
      h("div",{class:"actions"},button("start","fleet_start",()=>run(async()=>{await fleetRequest({action:"ensure_monitor",expected_configuration_binding:binding});await read();},false,"start"),!canEnable()||!!draft||snapshot.monitor.state!=="stopped"),
        button("quit","fleet_quit",()=>run(async()=>{await fleetRequest({action:"quit_owned",expected_configuration_binding:binding,expected_monitor_epoch:snapshot.monitor.epoch});await read();},false,"quit"),!canEnable()||!!draft||!snapshot.monitor.controllable)),
      h("h3",{},t("fleet_connections")),
      h("div",{class:"fleet-options"},...snapshot.configuration.connections.map(row=>h("div",{class:"row"},h("div",{class:"grow"},checkbox("connections",row.name,row.label),fleetStatus(ready.get(row.name), {h, t})),h("span",{class:"chip"},t("fleet_"+(ready.get(row.name)?.level||"unavailable")))))) ,
      h("h3",{},t("fleet_windows_title")),
      h("div",{class:"fleet-options"},...snapshot.profiles.map(row=>h("div",{class:"row"},checkbox("profiles",row.id,label(row.id)))),
        h("div",{class:"row"},checkbox("dashboard","dashboard",t("fleet_dashboard_choice")))),
      h("p",{class:"muted"},t("fleet_selection_help")),
      stale()?h("p",{class:"error"},t("fleet_changed")):null,
      choicePreview?h("p",{class:"note"},t("fleet_prerequisites",{names:choicePreview.summary.added_connections.map(label).join(", ")||t("fleet_none")})):null,
      h("div",{class:"actions"},button("choices",choicePreview?"fleet_save_choices":"fleet_review_choices",()=>choicePreview?run(async()=>{await fleetControl({action:"apply_choices",preview_id:choicePreview.preview_id});await read();},false,"choices"):reviewChoices(),!canEnable()||!draft||!!stale(),true),
        button("use-current","fleet_use_current",()=>{if(busy)return;draft=null;discardPreview();save();render();},!draft),button("refresh","fleet_refresh",()=>run(read,false,"refresh"))),
      h("h3",{},t("fleet_launch_title")),h("p",{class:"muted"},t("fleet_launch_help")),
      launch?h("p",{class:"note"},launch.receipt?t("fleet_launch_"+(launch.receipt.state==="prepared"?"uncertain":launch.receipt.state||"uncertain")):t(launch.attempted?"fleet_launch_uncertain":"fleet_launch_review")):null,
      launch?.summary?.bat_may_open_local_window?h("p",{class:"muted"},t("fleet_local_anchor")):null,
      h("div",{class:"actions"},button("review-launch","fleet_review_launch",()=>run(async()=>{const value=await fleetControl({action:"preview_launch"});if(alive()){if(!validId(value.preview_id)||!validSummary(value.summary,value.preview_id))throw new Error(t("fleet_unknown"));launch=value;}},false,"review-launch"),!canEnable()||!!draft||!!launch?.attempted),
        launch&&!launch.receipt&&launch.summary?button("launch",launch.attempted?"fleet_retry_launch":"fleet_launch_apply",launchApply,!canEnable()||!!draft||snapshot.login_launch?.state==="waiting"):null,
        launch?button("refresh-launch","fleet_refresh",()=>run(read,false,"refresh-launch")):null,
        knownLaunch()?button("new-launch","fleet_new_launch",()=>{if(busy)return;fleetControl({action:"discard",preview_id:launch.preview_id}).catch(()=>{});launch=null;save();render();}):null,
        launch&&!launch.attempted?button("cancel-launch","cancel",()=>{if(busy)return;fleetControl({action:"discard",preview_id:launch.preview_id}).catch(()=>{});launch=null;save();render();}):null),
      h("h3",{},t("fleet_login_title")),h("label",{class:"fleet-choice"},h("input",{type:"checkbox",checked:snapshot.login.show_picker,...gate(!canEnable()),"data-fleet-login":"picker",
        onkeydown:event=>{if(busy&&event.key===" ")event.preventDefault();},onclick:event=>{if(busy)event.preventDefault();},
        onchange:event=>{if(busy||!canEnable()){event.target.checked=snapshot.login.show_picker;return;}const show=event.target.checked;run(async()=>{const value=await fleetControl({action:"save_login",expected_revision:snapshot.login.revision,show_picker:show});if(alive())snapshot.login=value;});}})," ",t("fleet_login_picker")),
      h("h3",{},t("fleet_backend_title")),h("p",{class:"muted"},t("fleet_backend_help")),
      h("div",{class:"actions"},h("select",{value:backend,"data-fleet-backend":"choice","aria-label":t("fleet_backend_title"),...gate(!!migration),onchange:event=>{if(busy||migration){event.target.value=backend;return;}backend=event.target.value;}},h("option",{value:"rust",selected:backend==="rust"},t("fleet_backend_rust")),h("option",{value:"powershell",selected:backend==="powershell"},t("fleet_backend_powershell"))),
        h("label",{class:"fleet-choice"},h("input",{type:"checkbox",checked:autostart,...gate(!!migration),"data-fleet-autostart":"enabled",
          onkeydown:event=>{if(busy&&event.key===" ")event.preventDefault();},onclick:event=>{if(busy)event.preventDefault();},
          onchange:event=>{if(busy||migration){event.target.checked=autostart;return;}autostart=event.target.checked;}})," ",t("fleet_autostart"))),
      migration?h("p",{class:"note"},migration.receipt?t("fleet_migration_"+migration.receipt.phase):t(migration.accepted?"fleet_migration_unknown":"fleet_migration_review",{from:t("fleet_backend_"+migration.from),to:t("fleet_backend_"+migration.to),before:t(migration.autostart_before?"fleet_enabled":"fleet_disabled"),after:t(migration.autostart?"fleet_enabled":"fleet_disabled")})):null,
      h("div",{class:"actions"},button("review-migration","fleet_review_migration",()=>run(async()=>{const value=await fleetControl({action:"preview_migration",backend,autostart});if(alive()){if(!validId(value.preview_id)||typeof value.fingerprint!=="string")throw new Error(t("fleet_unknown"));migration=value;}},false,"review-migration"),!canEnable()||!!migration),
        migration&&!migration.accepted?button("apply-migration","fleet_apply_migration",()=>run(async()=>{migration.accepted=true;save();const value=await fleetControl({action:"apply_migration",preview_id:migration.preview_id,fingerprint:migration.fingerprint});if(alive())acceptMigration(value);await read();},false,"apply-migration"),!readable):null,
        migration?.accepted&&migration.receipt&&migration.receipt.phase!=="complete"?button("continue-migration","fleet_continue_migration",()=>run(async()=>{const value=await fleetControl({action:"advance_migration",migration_id:migration.preview_id});if(alive())acceptMigration(value);await read();},false,"continue-migration"),!readable):null,
        migration?.accepted?button("refresh-migration","fleet_refresh",()=>run(read,false,"refresh-migration")):null,
        migration?.accepted&&!migration.receipt?button("retry-migration","fleet_retry_migration",()=>run(async()=>{const value=await fleetControl(migration.restore_source?{action:"restore_migration",source_id:migration.restore_source,restore_id:migration.preview_id}:{action:"apply_migration",preview_id:migration.preview_id,fingerprint:migration.fingerprint});if(alive())acceptMigration(value);await read();},false,"retry-migration"),!migration.restore_source&&!migration.fingerprint):null,
        migration?.receipt?.phase==="complete"?button("new-migration","fleet_new_migration",()=>{if(busy)return;fleetControl({action:"discard",preview_id:migration.preview_id}).catch(()=>{});migration=null;save();render();}):null,
        migration&&!migration.accepted?button("cancel-migration","cancel",()=>{if(busy)return;fleetControl({action:"discard",preview_id:migration.preview_id}).catch(()=>{});migration=null;save();render();}):null,
        migration?.receipt?.phase==="complete"?button("restore-migration","fleet_restore_migration",()=>run(async()=>{const source=migration.preview_id;const restoreId=crypto.randomUUID().replaceAll("-","");migration={preview_id:restoreId,accepted:true,restore_source:source};save();const value=await fleetControl({action:"restore_migration",source_id:source,restore_id:restoreId});if(alive())acceptMigration(value);await read();},false,"restore-migration"),!readable):null),
    ];
    content.replaceChildren(...fields.filter(Boolean));
    if ([...content.querySelectorAll("button")].some(node => node.getAttribute("aria-disabled") === "true")) message.setAttribute("aria-busy", "true");
    else message.removeAttribute("aria-busy");
    const again = controlByKey(previous);
    if (again && !again.disabled) again.focus();
    else if (previous) successor(previous).focus();
  };
  await run(read);
  const poll=async()=>{if(!alive())return;if(!busy)await run(read,true);if(alive())timer=setTimeout(poll,5000);};
  timer=setTimeout(poll,5000);
  return ()=>{disposed=true;clearTimeout(timer);panel.remove();};
}
