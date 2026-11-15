// Project Browser front end. Plain JavaScript, no build step.
// Everything from the server is inserted with textContent (never innerHTML):
// notes come from other people's synced files, so treat them as untrusted.

const POLL_MS = 4000;
const OFFLINE = "Can't reach the project browser. Is the app still running? Changes can't be saved until it is.";
const $ = (id) => document.getElementById(id);

const state = {
  project: null,
  overview: null,
  activity: [],
  version: null,
  view: "grid",        // grid | activity
  entity: null,        // detail JSON of the open shot/asset
  task: null,          // selected task name
  statusFilter: new Set(),
  mine: false,
  attention: false,
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
const timeFormat = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const dayFormat = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long" });
const formatIso = (iso) => (iso ? dateFormat.format(new Date(iso)) : "");
const formatMtime = (seconds) => dateFormat.format(new Date(seconds * 1000));
const me = () => $("user").value.trim();

function statusInfo(code) {
  return state.project.statuses.find((s) => s.code === code)
    || { code, name: `${code} (not in config)`, background: "#555555", text: "#ffffff" };
}

// Problems cause wrong work (clashes, changes after approval); reminders are just stalled tasks
const isProblem = (flag) => flag.code !== "stalled";

function chip(code, extra = {}) {
  const info = statusInfo(code);
  const { tag = "span", flags = [], ...props } = extra;
  const problem = flags.some(isProblem);
  const reminder = !problem && flags.length > 0;
  // The default status ("nothing started") is drawn quietly so real progress stands out
  const classes = ["chip", info.default ? "quiet" : null, problem ? "flagged" : null, reminder ? "reminder" : null]
    .filter(Boolean).join(" ");
  const mark = problem ? "!" : reminder ? "…" : null;
  const node = el(tag, { class: classes, title: info.name, ...props },
    info.code, mark ? el("span", { class: "mark", "aria-hidden": "true", text: mark }) : null);
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

const isDone = (code) => statusInfo(code).done;
const problemTasks = () => allTasks().filter(({ task }) => task.flags.some(isProblem));
const stalledTasks = () => allTasks().filter(({ task }) => task.flags.length && !task.flags.some(isProblem));

// "New for you": changes by other people on tasks assigned to you since you last looked
const seenKey = () => `pb-seen:${me().toLowerCase()}`;
const lastSeen = () => localStorage.getItem(seenKey()) || "";

function myTaskKeys() {
  const user = me().toLowerCase();
  const keys = new Set();
  if (!user || !state.overview) return keys;
  for (const { entity, task } of allTasks()) {
    if ((task.assignee || "").toLowerCase() === user) keys.add(`${entity.name}/${task.task}`);
  }
  return keys;
}

function forMe(item) {
  // Changes by others on my tasks. Status moves caused by files are skipped (most
  // likely my own saves); ones caused by someone's decision count ("3d is ready").
  return myTaskKeys().has(`${item.entity}/${item.task}`) && item.kind !== "file" && item.user !== "files"
    && (item.user || "").toLowerCase() !== me().toLowerCase();
}

function unreadForMe() {
  const seen = lastSeen();
  return state.activity.filter((item) => forMe(item) && item.time > seen);
}

function markSeen() {
  if (me() && state.activity.length) localStorage.setItem(seenKey(), state.activity[0].time);
}

// Loading
async function loadAll({ rescan = false } = {}) {
  try {
    if (rescan) await api("/api/rescan", {});
    const [project, overview, activity, version] = await Promise.all([
      api("/api/project"), api("/api/overview"), api("/api/activity"), api("/api/version"),
    ]);
    state.project = project;
    state.overview = overview;
    state.activity = activity.items;
    state.version = version.version;
    if (!$("user").value) $("user").value = localStorage.getItem("pb-user") || project.user || "";
    document.title = `${project.name} · Project Browser`;
    $("project-name").textContent = project.name;
    renderAll();
    if (state.entity) await openEntity(state.entity.name, state.task, { keepForm: true });
  } catch (error) {
    setChildren($("main"), el("div", { class: "error", text: error.message }));
  }
}

function renderAll() {
  renderToolbar();
  renderSummary();
  renderMain();
  fillPeople();
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

// Summary & toolbar
function allTasks() {
  return state.overview.entities.flatMap((e) => e.tasks.map((t) => ({ entity: e, task: t })));
}

function renderSummary() {
  const tasks = allTasks();
  const done = tasks.filter(({ task }) => isDone(task.status)).length;
  const waiting = tasks.filter(({ task }) => statusInfo(task.status).feedback_request).length;
  const retakes = tasks.filter(({ task }) => statusInfo(task.status).retake).length;
  const attention = problemTasks().length;
  const stalled = stalledTasks().length;
  const unread = unreadForMe().length;
  const pct = tasks.length ? Math.round((100 * done) / tasks.length) : 0;
  const parts = [
    el("span", {}, el("b", { text: `${done}/${tasks.length}` }), ` tasks done (${pct}%)`),
    el("span", {}, el("b", { text: waiting }), " waiting for review"),
    el("span", {}, el("b", { text: retakes }), retakes === 1 ? " retake" : " retakes"),
  ];
  if (attention) {
    parts.push(el("span", { class: "attention" }, el("button", {
      class: "linkish", type: "button", title: "Show only tasks with a warning",
      onclick: () => { state.attention = true; setView("grid"); },
    }, el("b", { text: attention }), attention === 1 ? " needs attention" : " need attention")));
  }
  if (stalled) {
    parts.push(el("span", { class: "stalled" }, el("button", {
      class: "linkish", type: "button", title: "Active tasks with no new file or update for a while",
      onclick: () => { state.attention = true; setView("grid"); },
    }, el("b", { text: stalled }), " stalled")));
  }
  if (unread) {
    parts.push(el("span", { class: "unread" }, el("button", {
      class: "linkish", type: "button", title: "Changes by others on tasks assigned to you",
      onclick: () => { state.mine = true; setView("activity"); },
    }, el("b", { text: unread }), " new for you")));
  }
  if (state.overview.unsorted) {
    parts.push(el("span", {}, el("b", { text: state.overview.unsorted }), " files not sorted yet"));
  }
  setChildren($("summary"), ...parts);
}

function renderToolbar() {
  $("view-grid").setAttribute("aria-selected", String(state.view === "grid"));
  $("view-activity").setAttribute("aria-selected", String(state.view === "activity"));
  $("toggle-mine").setAttribute("aria-pressed", String(state.mine));
  $("toggle-attention").setAttribute("aria-pressed", String(state.attention));
  $("toggle-attention").hidden = state.view !== "grid";

  const counts = {};
  for (const { task } of allTasks()) counts[task.status] = (counts[task.status] || 0) + 1;
  const legend = $("legend");
  legend.hidden = state.view !== "grid";
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
          renderToolbar();
          renderMain();
        },
      });
      node.title = `${s.name}: show only shots and assets with this status`;
      node.append(el("span", { class: "count", text: counts[s.code] || 0 }));
      return node;
    }),
    state.statusFilter.size
      ? el("button", { class: "link-button", type: "button", text: "Show all", onclick: () => { state.statusFilter.clear(); renderToolbar(); renderMain(); } })
      : null,
  );
}

function setView(view) {
  state.view = view;
  renderToolbar();
  renderSummary();
  renderMain();
}

function renderMain() {
  if (state.view === "activity") renderActivity();
  else renderGrids();
}

// Grids
function visibleEntities() {
  const text = $("filter").value.trim().toLowerCase();
  const user = me().toLowerCase();
  return state.overview.entities.filter((entity) => {
    if (text && !entity.name.toLowerCase().includes(text)) return false;
    if (state.statusFilter.size && !entity.tasks.some((t) => state.statusFilter.has(t.status))) return false;
    if (state.mine && !entity.tasks.some((t) => (t.assignee || "").toLowerCase() === user)) return false;
    if (state.attention && !entity.tasks.some((t) => t.flags.length)) return false;
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
    const why = state.mine && !me() ? "Enter your name in the “You” box to see your tasks." : "Nothing matches. Clear the search or filters.";
    setChildren(main, el("p", { class: "empty", text: why }));
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
          const label = [`${entity.name} ${column}: ${info.name}`,
            task.assignee ? `assigned to ${task.assignee}` : null,
            task.latest ? `latest ${task.latest}` : null,
            ...task.flags.map((f) => `⚠ ${f.message}`)].filter(Boolean).join("\n");
          return el("td", {}, chip(task.status, {
            tag: "button", type: "button", title: label, "aria-label": label.replace(/\n/g, ", "),
            flags: task.flags, onclick: () => openEntity(entity.name, column),
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

// Activity feed
const ICONS = { file: "⬆", status: "●", note: "✎", assign: "→" };

function activitySentence(item) {
  const target = el("button", {
    class: "target", type: "button", text: `${item.entity} ${item.task}`,
    onclick: () => openEntity(item.entity, item.task),
  });
  if (item.kind === "file") {
    const frames = item.sequence && item.files > 1 ? ` (${item.files} frames)` : "";
    const parts = [el("span", {}, target, ` · ${item.label} of ${item.product} saved${frames}`)];
    if (item.copies) {
      parts.push(el("div", { class: "alert inline", text: `⚠ ${item.copies + 1} files claim ${item.label}: two people may have saved the same version` }));
    }
    return parts;
  }
  if (item.kind === "status") {
    const who = item.auto ? (item.user === "files" ? "New file moved " : "Automation moved ") : `${item.user} set `;
    const parts = [el("span", {}, `${who}`, target, " to ", chip(item.status))];
    if (item.note) parts.push(el("blockquote", { text: item.note }));
    return parts;
  }
  if (item.kind === "note") {
    return [el("span", {}, `${item.user} commented on `, target), el("blockquote", { text: item.note })];
  }
  if (item.kind === "assign") {
    return [el("span", {}, `${item.user} ${item.assignee ? `assigned ${item.assignee} to ` : "unassigned "}`, target)];
  }
  return [el("span", {}, target)];
}

function renderActivity() {
  const main = $("main");
  const text = $("filter").value.trim().toLowerCase();
  const seen = lastSeen();
  const items = state.activity.filter((item) => {
    if (text && !`${item.entity} ${item.task}`.toLowerCase().includes(text)) return false;
    if (state.mine && !forMe(item)) return false;
    return true;
  });
  if (!items.length) {
    const why = state.mine
      ? (me() ? "Nothing yet from others on tasks assigned to you." : "Enter your name in the “You” box to see changes on your tasks.")
      : "No activity yet. Files saved into the project and status changes appear here.";
    setChildren(main, el("p", { class: "empty", text: why }));
    return;
  }

  const days = [];
  for (const item of items) {
    const day = dayFormat.format(new Date(item.time));
    if (!days.length || days[days.length - 1].day !== day) days.push({ day, items: [] });
    days[days.length - 1].items.push(item);
  }
  setChildren(main, el("div", { class: "feed" }, ...days.map(({ day, items: dayItems }) => [
    el("h2", { text: day }),
    el("ol", {}, ...dayItems.map((item) => el("li", { class: forMe(item) && item.time > seen ? "unread" : null },
      el("span", { class: "when", text: timeFormat.format(new Date(item.time)) }),
      el("span", { class: "icon", "aria-hidden": "true", text: ICONS[item.kind] || "·" }),
      el("div", { class: "what" }, ...activitySentence(item))))),
  ])));
  if (state.mine) {
    markSeen();
    renderSummary();
  }
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
  renderMain();
}

function renderPanel({ keepForm = false } = {}) {
  const entity = state.entity;
  $("panel").hidden = false;
  document.body.classList.add("panel-open");
  $("panel-title").textContent = entity.name;
  $("panel-context").textContent = [entity.type, entity.context].filter(Boolean).join(" · ");
  const thumb = $("panel-thumb");
  thumb.hidden = !entity.thumbnail;
  if (entity.thumbnail) thumb.src = entity.thumbnail;

  setChildren($("task-tabs"), ...entity.tasks.map((t) => el("button", {
    class: "task-tab", type: "button", role: "tab", "aria-selected": String(t.task === state.task),
    onclick: () => { state.task = t.task; state.formDirty = false; renderPanel(); },
  }, el("span", { class: "name", text: t.task }), chip(t.status, { flags: t.flags }))));

  const task = entity.tasks.find((t) => t.task === state.task);
  setChildren($("task-alerts"), ...(task ? task.flags : []).map((f) => el("div", { class: isProblem(f) ? "alert" : "alert reminder" },
    el("b", { text: isProblem(f) ? "⚠ " : "… " }), f.message)));
  renderForm(task, keepForm);
  renderVersions(entity, task);
  renderNotes(task);
  if (state.view === "grid") renderGrids();
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

function decisionWord(task) {
  const info = statusInfo(task.status);
  if (info.done) return "approved";
  if (info.feedback_request) return "sent for review";
  if (info.retake) return "retake on this";
  return "last decision";
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
    ...product.versions.map((v, index) => {
      let preview;
      if (v.media && v.media.kind === "image") {
        preview = el("button", { class: "preview", type: "button", "aria-label": `View ${v.label}`, onclick: () => openViewer(product.name, v) },
          el("img", { class: "preview", src: v.media.url, alt: "", loading: "lazy" }));
      } else if (v.media && v.media.kind === "video") {
        preview = el("button", { class: "preview", type: "button", "aria-label": `Play ${v.label}`, text: "▶", onclick: () => openViewer(product.name, v) });
      } else {
        preview = el("span", { class: "preview none", text: "no preview", title: "Browsers can't display this file type" });
      }
      const decided = task && task.decided_on && v.number !== null && task.decided_on[product.name] === v.number;
      const meta = [formatMtime(v.modified), v.sequence ? `${v.files} frames` : null].filter(Boolean).join(" · ");
      return el("div", { class: "version-row" },
        preview,
        el("span", { class: "label" }, v.label),
        el("span", { class: "meta" }, meta,
          index === 0 ? el("span", { class: "badge latest", text: "latest" }) : null,
          decided ? el("span", { class: "badge", text: decisionWord(task) }) : null),
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
  if (me()) people.add(me());
  setChildren($("people"), ...[...people].sort().map((p) => el("option", { value: p })));
}

// Actions
async function saveTask(event) {
  event.preventDefault();
  if (!me()) {
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
      user: me(),
      status: $("form-status").value,
      assignee: $("form-assignee").value,
      comment: $("form-comment").value,
    });
    state.formDirty = false;
    toast(result.saved ? `Saved ${state.entity.name} ${state.task}` : "Nothing changed");
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

// Wiring
$("refresh").addEventListener("click", async () => {
  $("refresh").disabled = true;
  await loadAll({ rescan: true });
  $("refresh").disabled = false;
  toast("Rescanned the project folder");
});
$("filter").addEventListener("input", renderMain);
$("user").addEventListener("change", () => {
  localStorage.setItem("pb-user", me());
  renderAll();
});
$("view-grid").addEventListener("click", () => setView("grid"));
$("view-activity").addEventListener("click", () => setView("activity"));
$("toggle-mine").addEventListener("click", () => { state.mine = !state.mine; renderToolbar(); renderMain(); });
$("toggle-attention").addEventListener("click", () => { state.attention = !state.attention; renderToolbar(); renderMain(); });
$("panel-close").addEventListener("click", closePanel);
$("task-form").addEventListener("submit", saveTask);
$("task-form").addEventListener("input", () => { state.formDirty = true; });
$("form-comment").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) $("task-form").requestSubmit();
});
$("viewer-close").addEventListener("click", () => $("viewer").close());
$("viewer").addEventListener("close", () => setChildren($("viewer-body")));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("viewer").open && !$("panel").hidden) closePanel();
  if (event.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
    event.preventDefault();
    $("filter").focus();
  }
});

// Start
(async () => {
  await loadAll();
  if (location.hash === "#activity") {
    setView("activity");
  } else {
    const [name, task] = location.hash.slice(1).split("/").map(decodeURIComponent);
    if (name && state.overview) await openEntity(name, task || null);
  }
  setTimeout(poll, POLL_MS);
})();
