// Project Browser front end. Plain JavaScript, no build step.
// Everything from the server is inserted with textContent (never innerHTML):
// notes come from other people's synced files, so treat them as untrusted.

const POLL_MS = 4000;
const OFFLINE = "Can't reach the project browser. Is the app still running? Changes can't be saved until it is.";
const $ = (id) => document.getElementById(id);

const state = {
  project: null,
  overview: null,
  version: null,
  entity: null,        // detail JSON of the open shot/asset
  task: null,          // selected task name
  statusFilter: new Set(),
  formDirty: false,
};

// Helpers
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "style") Object.assign(node.style, value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined && child !== false) {
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
  }
  return node;
}

// Like replaceChildren, but skips null/false so optional parts can be inline
function setChildren(node, ...children) {
  node.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
}

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error(OFFLINE);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

const dateFormat = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const formatIso = (iso) => (iso ? dateFormat.format(new Date(iso)) : "");
const formatMtime = (seconds) => dateFormat.format(new Date(seconds * 1000));

function statusInfo(code) {
  return state.project.statuses.find((s) => s.code === code)
    || { code, name: `${code} (not in config)`, background: "#555555", text: "#ffffff" };
}

function chip(code, extra = {}) {
  const info = statusInfo(code);
  const { tag = "span", who, ...props } = extra;
  // The default status ("nothing started") is drawn quietly so real progress stands out
  const node = el(tag, { class: info.default ? "chip quiet" : "chip", title: info.name, ...props },
    info.code, who ? el("span", { class: "who", text: `· ${who}` }) : null);
  node.style.setProperty("--chip-bg", info.background);
  node.style.setProperty("--chip-fg", info.text);
  return node;
}

let toastTimer;
function toast(message, bad = false) {
  const node = $("toast");
  node.textContent = message;
  node.classList.toggle("bad", bad);
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, bad ? 6000 : 2500);
}

function isDone(code) { return statusInfo(code).done; }

// Loading
async function loadAll({ rescan = false } = {}) {
  try {
    if (rescan) await api("/api/rescan", {});
    const [project, overview, version] = await Promise.all([
      api("/api/project"), api("/api/overview"), api("/api/version"),
    ]);
    state.project = project;
    state.overview = overview;
    state.version = version.version;
    if (!$("user").value) $("user").value = localStorage.getItem("pb-user") || project.user || "";
    document.title = `${project.name} · Project Browser`;
    $("project-name").textContent = project.name;
    renderLegend();
    renderSummary();
    renderGrids();
    fillPeople();
    if (state.entity) await openEntity(state.entity.name, state.task, { keepForm: true });
  } catch (error) {
    setChildren($("main"), el("div", { class: "error", text: error.message }));
  }
}

function setOffline(message) {
  const banner = $("offline");
  banner.textContent = message || "";
  banner.hidden = !message;
}

async function poll() {
  try {
    const { version } = await api("/api/version");
    setOffline(null);
    if (version !== state.version) await loadAll();
  } catch (error) {
    // App closed or restarted: say so, and keep checking in case it comes back
    setOffline(error.message);
  }
  setTimeout(poll, POLL_MS);
}

// Summary & legend
function allTasks() {
  return state.overview.entities.flatMap((e) => e.tasks.map((t) => ({ entity: e, task: t })));
}

function renderSummary() {
  const tasks = allTasks();
  const done = tasks.filter(({ task }) => isDone(task.status)).length;
  const waiting = tasks.filter(({ task }) => statusInfo(task.status).feedback_request).length;
  const retakes = tasks.filter(({ task }) => statusInfo(task.status).retake).length;
  const pct = tasks.length ? Math.round((100 * done) / tasks.length) : 0;
  const parts = [
    el("span", {}, el("b", { text: `${done}/${tasks.length}` }), ` tasks done (${pct}%)`),
    el("span", {}, el("b", { text: waiting }), " waiting for review"),
    el("span", {}, el("b", { text: retakes }), retakes === 1 ? " retake" : " retakes"),
  ];
  if (state.overview.unsorted) {
    parts.push(el("span", {}, el("b", { text: state.overview.unsorted }), " files not sorted yet"));
  }
  setChildren($("summary"), ...parts);
}

function renderLegend() {
  const counts = {};
  for (const { task } of allTasks()) counts[task.status] = (counts[task.status] || 0) + 1;
  const legend = $("legend");
  legend.classList.toggle("filtering", state.statusFilter.size > 0);
  setChildren(legend,
    el("span", { class: "hint", text: "Show:" }),
    ...state.project.statuses.map((s) => {
      const node = chip(s.code, {
        tag: "button",
        type: "button",
        "aria-pressed": String(state.statusFilter.has(s.code)),
        onclick: () => {
          if (state.statusFilter.has(s.code)) state.statusFilter.delete(s.code);
          else state.statusFilter.add(s.code);
          renderLegend();
          renderGrids();
        },
      });
      node.title = `${s.name}: show only shots and assets with this status`;
      node.append(el("span", { class: "count", text: counts[s.code] || 0 }));
      return node;
    }),
    state.statusFilter.size
      ? el("button", { class: "link-button", type: "button", text: "Show all", onclick: () => { state.statusFilter.clear(); renderLegend(); renderGrids(); } })
      : null,
  );
}

// Grids
function visibleEntities() {
  const text = $("filter").value.trim().toLowerCase();
  return state.overview.entities.filter((entity) => {
    if (text && !entity.name.toLowerCase().includes(text)) return false;
    if (state.statusFilter.size && !entity.tasks.some((t) => state.statusFilter.has(t.status))) return false;
    return true;
  });
}

function entityCell(entity) {
  const done = entity.tasks.filter((t) => isDone(t.status)).length;
  const total = entity.tasks.length;
  const thumb = entity.thumbnail
    ? el("img", { class: "thumb", src: entity.thumbnail, alt: "", loading: "lazy" })
    : el("span", { class: "thumb", "aria-hidden": "true" });
  return el("button", { class: "entity", type: "button", onclick: () => openEntity(entity.name) },
    thumb,
    el("span", {},
      el("span", { class: "entity-name", text: entity.name }),
      el("span", { class: "progress", title: `${done} of ${total} tasks done` },
        el("span", { class: "bar" }, el("i", { style: { width: `${total ? (100 * done) / total : 0}%` } })),
        `${done}/${total}`)));
}

function renderGrids() {
  const main = $("main");
  const entities = visibleEntities();
  if (!entities.length) {
    setChildren(main, el("p", { class: "empty", text: "Nothing matches. Clear the search or status filter." }));
    return;
  }

  const types = [...Object.keys(state.project.task_types)];
  for (const e of entities) if (!types.includes(e.type)) types.push(e.type);

  const groups = types.map((type) => {
    const members = entities.filter((e) => e.type === type);
    if (!members.length) return null;
    const columns = [...(state.project.task_types[type] || [])];
    for (const e of members) for (const t of e.tasks) if (!columns.includes(t.task)) columns.push(t.task);

    const rows = [];
    let lastGroup = null;
    for (const entity of members) {
      const groupName = type === "shot" ? (entity.act ? `${entity.act} · ${entity.sequence}` : "Not in a sequence") : null;
      if (groupName && groupName !== lastGroup) {
        rows.push(el("tr", { class: "subhead" }, el("th", { colspan: columns.length + 1, scope: "rowgroup", text: groupName })));
        lastGroup = groupName;
      }
      const byTask = Object.fromEntries(entity.tasks.map((t) => [t.task, t]));
      const selected = state.entity && state.entity.name === entity.name;
      rows.push(el("tr", { class: selected ? "selected" : null },
        el("th", { scope: "row" }, entityCell(entity)),
        columns.map((column) => {
          const task = byTask[column];
          if (!task) return el("td", {});
          const info = statusInfo(task.status);
          const label = `${entity.name} ${column}: ${info.name}`
            + (task.assignee ? `, assigned to ${task.assignee}` : "")
            + (task.latest ? `, latest ${task.latest}` : "");
          return el("td", {}, chip(task.status, {
            tag: "button", type: "button", title: label, "aria-label": label,
            onclick: () => openEntity(entity.name, column),
          }));
        })));
    }

    const title = `${type ? type[0].toUpperCase() + type.slice(1) : "Other"}s`;
    return el("section", { class: "group", "aria-label": title },
      el("h2", { text: `${title} (${members.length})` }),
      el("div", { class: "table-wrap" },
        el("table", {},
          el("thead", {}, el("tr", {}, el("th", { scope: "col", text: type === "shot" ? "Shot" : "Asset" }),
            columns.map((c) => el("th", { scope: "col", text: c })))),
          el("tbody", {}, rows))));
  });

  setChildren(main, ...groups.filter(Boolean),
    ...state.overview.warnings.map((w) => el("p", { class: "warning", text: w })));
}

// Detail panel
async function openEntity(name, task = null, { keepForm = false } = {}) {
  let data;
  try {
    data = await api(`/api/entity?name=${encodeURIComponent(name)}`);
  } catch (error) {
    toast(error.message, true);
    return;
  }
  const sameTask = state.entity && state.entity.name === data.name && state.task === (task || state.task);
  state.entity = data;
  state.task = task && data.tasks.some((t) => t.task === task) ? task : (data.tasks[0] || {}).task || null;
  renderPanel({ keepForm: keepForm && sameTask && state.formDirty });
  history.replaceState(null, "", `#${encodeURIComponent(data.name)}/${encodeURIComponent(state.task || "")}`);
}

function closePanel() {
  state.entity = null;
  state.task = null;
  $("panel").hidden = true;
  document.body.classList.remove("panel-open");
  history.replaceState(null, "", location.pathname);
  renderGrids();
}

function renderPanel({ keepForm = false } = {}) {
  const entity = state.entity;
  const panel = $("panel");
  panel.hidden = false;
  document.body.classList.add("panel-open");
  $("panel-title").textContent = entity.name;
  $("panel-context").textContent = [entity.type, entity.context].filter(Boolean).join(" · ");
  const thumb = $("panel-thumb");
  thumb.hidden = !entity.thumbnail;
  if (entity.thumbnail) thumb.src = entity.thumbnail;

  setChildren($("task-tabs"), ...entity.tasks.map((t) => el("button", {
    class: "task-tab", type: "button", role: "tab", "aria-selected": String(t.task === state.task),
    onclick: () => { state.task = t.task; state.formDirty = false; renderPanel(); },
  }, el("span", { class: "name", text: t.task }), chip(t.status))));

  const task = entity.tasks.find((t) => t.task === state.task);
  renderForm(task, keepForm);
  renderVersions(entity, task);
  renderNotes(task);
  renderGrids();
}

function renderForm(task, keepForm) {
  const form = $("task-form");
  form.hidden = !task;
  if (!task) return;
  const select = $("form-status");
  if (!keepForm) {
    setChildren(select, ...state.project.statuses.map((s) => el("option", { value: s.code, text: `${s.code} — ${s.name}` })));
    select.value = task.status;
    $("form-assignee").value = task.assignee || "";
    $("form-comment").value = "";
    state.formDirty = false;
  }
}

function renderVersions(entity, task) {
  const products = entity.products.filter((p) => (task ? p.task === task.task : true));
  const box = $("versions");
  if (!products.length) {
    setChildren(box, el("p", { class: "muted", text: "No files for this task yet." }));
    return;
  }
  setChildren(box, ...products.map((product) => el("div", { class: "product" },
    el("h4", { text: product.name }),
    ...product.versions.map((v) => {
      let preview;
      if (v.media && v.media.kind === "image") {
        preview = el("button", { class: "preview", type: "button", "aria-label": `View ${v.label}`, onclick: () => openViewer(product.name, v) },
          el("img", { class: "preview", src: v.media.url, alt: "", loading: "lazy" }));
      } else if (v.media && v.media.kind === "video") {
        preview = el("button", { class: "preview", type: "button", "aria-label": `Play ${v.label}`, text: "▶", onclick: () => openViewer(product.name, v) });
      } else {
        preview = el("span", { class: "preview none", text: "no preview", title: "Browsers can't display this file type" });
      }
      const meta = [formatMtime(v.modified), v.sequence ? `${v.files} frames` : null].filter(Boolean).join(" · ");
      return el("div", { class: "version-row" },
        preview,
        el("span", { class: "label", text: v.label }),
        el("span", { class: "meta", text: meta }),
        el("button", { class: "link-button", type: "button", text: "Show in folder", onclick: () => reveal(v.path) }));
    }))));
}

function renderNotes(task) {
  const list = $("notes");
  if (!task || !task.notes.length) {
    setChildren(list, el("li", { class: "muted", text: "No notes yet." }));
    return;
  }
  setChildren(list, ...[...task.notes].reverse().map((note) => el("li", {},
    el("div", { class: "meta" }, el("b", { text: note.user }), formatIso(note.time), note.status ? chip(note.status) : null),
    el("div", { class: "text", text: note.text }))));
}

function fillPeople() {
  const people = new Set();
  for (const { task } of allTasks()) if (task.assignee) people.add(task.assignee);
  const me = $("user").value.trim();
  if (me) people.add(me);
  setChildren($("people"), ...[...people].sort().map((p) => el("option", { value: p })));
}

// Actions
async function saveTask(event) {
  event.preventDefault();
  const user = $("user").value.trim();
  if (!user) {
    toast("Enter your name in the “You” box first.", true);
    $("user").focus();
    return;
  }
  const button = $("form-save");
  button.disabled = true;
  try {
    const result = await api("/api/task", {
      entity: state.entity.name,
      task: state.task,
      user,
      status: $("form-status").value,
      assignee: $("form-assignee").value,
      comment: $("form-comment").value,
    });
    state.formDirty = false;
    toast(result.saved ? `Saved ${state.entity.name} ${state.task}` : "Nothing changed");
    state.version = null; // refresh the grid on the next poll
    await loadAll();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function reveal(path) {
  try {
    await api("/api/reveal", { path });
  } catch (error) {
    toast(error.message, true);
  }
}

function openViewer(name, version) {
  const body = $("viewer-body");
  const media = version.media.kind === "video"
    ? el("video", { src: version.media.url, controls: true, autoplay: true, preload: "metadata" })
    : el("img", { src: version.media.url, alt: `${name} ${version.label}` });
  if (version.media.kind === "video") {
    media.addEventListener("error", () => {
      setChildren(body, el("p", { class: "error", text: "This browser can't play this video. H.264 .mp4 plays everywhere; use “Show in folder” to open it in a player." }));
    });
  }
  setChildren(body, media);
  $("viewer-title").textContent = `${name} ${version.label}${version.sequence ? " (middle frame)" : ""}`;
  $("viewer").showModal();
}

function closeViewer() {
  $("viewer").close();
}

// Wiring
$("refresh").addEventListener("click", async () => {
  $("refresh").disabled = true;
  await loadAll({ rescan: true });
  $("refresh").disabled = false;
  toast("Rescanned the project folder");
});
$("filter").addEventListener("input", renderGrids);
$("user").addEventListener("change", () => { localStorage.setItem("pb-user", $("user").value.trim()); fillPeople(); });
$("panel-close").addEventListener("click", closePanel);
$("task-form").addEventListener("submit", saveTask);
$("task-form").addEventListener("input", () => { state.formDirty = true; });
$("form-comment").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) $("task-form").requestSubmit();
});
$("viewer-close").addEventListener("click", closeViewer);
$("viewer").addEventListener("close", () => setChildren($("viewer-body")));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("viewer").open && !$("panel").hidden) closePanel();
  if (event.key === "/" && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") {
    event.preventDefault();
    $("filter").focus();
  }
});

// Start
(async () => {
  await loadAll();
  const [name, task] = location.hash.slice(1).split("/").map(decodeURIComponent);
  if (name && state.overview) await openEntity(name, task || null);
  setTimeout(poll, POLL_MS);
})();
