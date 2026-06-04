"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = { settings: null, currentId: null, currentItem: null };

async function api(method, url, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(url, opts);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try { const j = await r.json(); if (j.detail) msg = j.detail; } catch {}
    throw new Error(msg);
  }
  if (r.status === 204) return null;
  return r.json();
}

function toast(msg, kind = "ok", ms = 2400) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast ${kind}`;
  setTimeout(() => t.classList.add("hidden"), ms);
}

function badge(text, cls) {
  const span = document.createElement("span");
  span.className = `badge ${cls || ""}`;
  span.textContent = text;
  return span;
}

function renderCard(item, target) {
  const li = document.createElement("li");
  li.className = "card";
  li.dataset.id = item.id;

  const row1 = document.createElement("div");
  row1.className = "row1";
  const title = document.createElement("div");
  title.className = "title";
  title.textContent = item.title;
  const badges = document.createElement("div");
  badges.className = "badges";
  badges.appendChild(badge(item.type, "type"));
  badges.appendChild(badge(item.status, `status-${item.status}`));
  if (item.importance === "high") badges.appendChild(badge("high", "imp-high"));
  row1.appendChild(title);
  row1.appendChild(badges);
  li.appendChild(row1);

  if (item.summary || item.body) {
    const p = document.createElement("div");
    p.className = "preview";
    p.textContent = item.summary || item.body;
    li.appendChild(p);
  }

  const meta = document.createElement("div");
  meta.className = "meta";
  if (item.project_name) {
    const s = document.createElement("span");
    s.textContent = `📁 ${item.project_name}`;
    meta.appendChild(s);
  }
  if (item.tags && item.tags.length) {
    const s = document.createElement("span");
    s.textContent = `🏷 ${item.tags.join(", ")}`;
    meta.appendChild(s);
  }
  const t = document.createElement("span");
  t.textContent = `🕒 ${item.created_at.replace("T", " ").slice(0, 19)}`;
  meta.appendChild(t);
  li.appendChild(meta);

  li.addEventListener("click", () => openDetail(item.id));
  target.appendChild(li);
}

function renderCards(items, container) {
  container.innerHTML = "";
  if (!items.length) {
    container.innerHTML = `<li class="card"><div class="muted">No items yet. Save one from ChatGPT or use “+ New”.</div></li>`;
    return;
  }
  items.forEach((i) => renderCard(i, container));
}

async function loadInbox() {
  const status = $("#filter-status").value;
  const url = new URL("/api/memory", location.origin);
  url.searchParams.set("limit", "100");
  if (status) url.searchParams.set("status", status);
  const data = await api("GET", url.pathname + url.search);
  renderCards(data.items, $("#inbox-list"));
}

async function doSearch() {
  const q = $("#search-input").value.trim();
  if (!q) { $("#search-results").innerHTML = ""; return; }
  const url = new URL("/api/search", location.origin);
  url.searchParams.set("q", q);
  const type = $("#search-type").value;
  const project = $("#search-project").value.trim();
  if (type) url.searchParams.set("type", type);
  if (project) url.searchParams.set("project", project);
  const data = await api("GET", url.pathname + url.search);
  renderCards(data.results, $("#search-results"));
}

async function loadDecisions() {
  const data = await api("GET", "/api/decisions");
  const ul = $("#decisions-list");
  ul.innerHTML = "";
  if (!data.decisions.length) {
    ul.innerHTML = `<li class="card"><div class="muted">No decisions recorded yet.</div></li>`; return;
  }
  data.decisions.forEach((d) => {
    const li = document.createElement("li");
    li.className = "card";
    li.dataset.id = d.memory_id;
    li.innerHTML = `
      <div class="row1">
        <div class="title">${escapeHtml(d.decision)}</div>
        <div class="badges">${badgeHtml(d.status, "type")}${d.project_name ? badgeHtml(d.project_name) : ""}</div>
      </div>
      ${d.rationale ? `<div class="preview">${escapeHtml(d.rationale)}</div>` : ""}
      <div class="meta"><span>🕒 ${d.created_at.replace("T", " ").slice(0, 19)}</span></div>
    `;
    li.addEventListener("click", () => openDetail(d.memory_id));
    ul.appendChild(li);
  });
}

async function loadTasks() {
  const data = await api("GET", "/api/tasks");
  const ul = $("#tasks-list");
  ul.innerHTML = "";
  if (!data.tasks.length) {
    ul.innerHTML = `<li class="card"><div class="muted">No tasks yet.</div></li>`; return;
  }
  data.tasks.forEach((t) => {
    const li = document.createElement("li");
    li.className = "card";
    li.dataset.id = t.memory_id;
    li.innerHTML = `
      <div class="row1">
        <div class="title">${escapeHtml(t.title)}</div>
        <div class="badges">${badgeHtml(t.priority, "type")}${badgeHtml(t.status)}${t.project_name ? badgeHtml(t.project_name) : ""}</div>
      </div>
      ${t.next_action ? `<div class="preview">${escapeHtml(t.next_action)}</div>` : ""}
      <div class="meta"><span>🕒 ${t.created_at.replace("T", " ").slice(0, 19)}</span></div>
    `;
    li.addEventListener("click", () => openDetail(t.memory_id));
    ul.appendChild(li);
  });
}

async function loadEvents() {
  const data = await api("GET", "/api/events?limit=200");
  const ul = $("#events-list");
  ul.innerHTML = "";
  data.events.forEach((e) => {
    const li = document.createElement("li");
    li.innerHTML = `
      <span>${e.created_at.replace("T", " ").slice(0, 23)}</span>
      <span>${escapeHtml(e.action)}</span>
      <span class="muted">${escapeHtml(e.actor)}</span>
      <span class="muted">${escapeHtml(JSON.stringify(e.payload || {}))}</span>
    `;
    ul.appendChild(li);
  });
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}
function badgeHtml(text, cls = "") {
  return `<span class="badge ${cls}">${escapeHtml(text)}</span>`;
}

function fillSelect(sel, values, withEmpty) {
  sel.innerHTML = "";
  if (withEmpty) {
    const o = document.createElement("option"); o.value = ""; o.textContent = withEmpty; sel.appendChild(o);
  }
  values.forEach((v) => {
    const o = document.createElement("option");
    o.value = v; o.textContent = v;
    sel.appendChild(o);
  });
}

async function openDetail(id) {
  state.currentId = id;
  state.currentItem = id ? await api("GET", `/api/memory/${id}`) : null;
  const item = state.currentItem || { title: "", body: "", summary: "", type: "note", status: "inbox", importance: "medium", project_name: "", tags: [] };
  $("#d-title").value = item.title || "";
  $("#d-summary").value = item.summary || "";
  $("#d-body").value = item.body || "";
  $("#d-type").value = item.type || "note";
  $("#d-status").value = item.status || "inbox";
  $("#d-importance").value = item.importance || "medium";
  $("#d-project").value = item.project_slug || item.project_name || "";
  $("#d-tags").value = (item.tags || []).join(", ");
  $("#d-created").textContent = id ? `#${id} · created ${item.created_at}` : "new memory";
  $("#d-append").value = "";
  $("#d-archive").disabled = !id;
  $("#detail-overlay").classList.remove("hidden");
}

function closeDetail() {
  $("#detail-overlay").classList.add("hidden");
  state.currentId = null;
}

async function saveDetail() {
  const payload = {
    title: $("#d-title").value.trim(),
    body: $("#d-body").value,
    summary: $("#d-summary").value.trim() || null,
    type: $("#d-type").value,
    status: $("#d-status").value,
    importance: $("#d-importance").value,
    project: $("#d-project").value.trim() || null,
    tags: $("#d-tags").value.split(",").map((s) => s.trim()).filter(Boolean),
  };
  if (!payload.title) { toast("title required", "bad"); return; }
  try {
    let item;
    if (state.currentId) {
      item = await api("PATCH", `/api/memory/${state.currentId}`, payload);
    } else {
      item = await api("POST", "/api/memory", payload);
    }
    toast(state.currentId ? "saved" : "created");
    state.currentId = item.id;
    state.currentItem = item;
    closeDetail();
    await loadInbox();
  } catch (e) { toast(e.message, "bad"); }
}

async function archiveDetail() {
  if (!state.currentId) return;
  if (!confirm("Archive this memory?")) return;
  try {
    await api("POST", `/api/memory/${state.currentId}/archive`);
    toast("archived");
    closeDetail();
    await loadInbox();
  } catch (e) { toast(e.message, "bad"); }
}

async function appendDetail() {
  if (!state.currentId) { toast("save first", "bad"); return; }
  const text = $("#d-append").value.trim();
  if (!text) return;
  try {
    const item = await api("POST", `/api/memory/${state.currentId}/append`, { text });
    state.currentItem = item;
    $("#d-body").value = item.body;
    $("#d-append").value = "";
    toast("appended");
  } catch (e) { toast(e.message, "bad"); }
}

async function loadStatus() {
  const s = await api("GET", "/api/status");
  state.settings = s;
  $("#status-dot").classList.add("ok");
  $("#server-version").textContent = `v${s.version}`;
  $("#mcp-url").textContent = s.mcp_url_origin;
  $("#set-name").textContent = s.server_name;
  $("#set-host").textContent = s.host;
  $("#set-port").textContent = s.port;
  $("#set-data").textContent = s.data_dir;
  $("#set-db").textContent = s.db_path;
  $("#set-mcp-local").textContent = s.mcp_url_local;
  $("#set-mcp-origin").textContent = s.mcp_url_origin;
  fillSelect($("#filter-status"), s.enums.status, "All statuses");
  fillSelect($("#search-type"), s.enums.type, "Any type");
  fillSelect($("#d-type"), s.enums.type, null);
  fillSelect($("#d-status"), s.enums.status, null);
  fillSelect($("#d-importance"), s.enums.importance, null);
}

function setTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  if (name === "inbox") loadInbox();
  else if (name === "decisions") loadDecisions();
  else if (name === "tasks") loadTasks();
  else if (name === "events") loadEvents();
}

function wire() {
  $$(".tab").forEach((t) => t.addEventListener("click", () => setTab(t.dataset.tab)));
  $("#refresh-inbox").addEventListener("click", loadInbox);
  $("#filter-status").addEventListener("change", loadInbox);
  $("#search-btn").addEventListener("click", doSearch);
  $("#search-input").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
  $("#refresh-decisions").addEventListener("click", loadDecisions);
  $("#refresh-tasks").addEventListener("click", loadTasks);
  $("#refresh-events").addEventListener("click", loadEvents);
  $("#copy-endpoint").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("#mcp-url").textContent);
      toast("copied MCP URL");
    } catch { toast("copy failed", "bad"); }
  });
  $("#new-memory").addEventListener("click", () => openDetail(null));
  $("#d-close").addEventListener("click", closeDetail);
  $("#d-save").addEventListener("click", saveDetail);
  $("#d-archive").addEventListener("click", archiveDetail);
  $("#d-append-btn").addEventListener("click", appendDetail);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#detail-overlay").classList.contains("hidden")) closeDetail();
  });
}

async function boot() {
  wire();
  try {
    await loadStatus();
    await loadInbox();
  } catch (e) {
    $("#status-dot").classList.add("bad");
    toast(`status error: ${e.message}`, "bad", 6000);
  }
}

boot();
