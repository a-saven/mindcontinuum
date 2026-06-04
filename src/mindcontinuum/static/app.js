"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const NS_KEY = "mindcontinuum.namespace";
const state = { settings: null, currentId: null, currentItem: null, ns: localStorage.getItem(NS_KEY) || "work" };

function ns() { return state.ns; }
function withNs(url) {
  const u = new URL(url, location.origin);
  if (!u.searchParams.has("namespace")) u.searchParams.set("namespace", ns());
  return u.pathname + u.search;
}

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

function toast(msg, kind = "ok", ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast ${kind}`;
  setTimeout(() => t.classList.add("hidden"), ms);
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
}
function badgeHtml(text, cls = "") {
  return `<span class="badge ${cls}">${escapeHtml(text)}</span>`;
}

function renderCard(item, target, extraActions) {
  const li = document.createElement("li");
  li.className = "card";
  li.dataset.id = item.id;

  const badges = [
    badgeHtml(item.type, "type"),
    badgeHtml(item.status, `status-${item.status}`),
    item.importance === "high" ? badgeHtml("high", "imp-high") : "",
  ].join("");
  const meta = [
    item.project_name ? `<span>📁 ${escapeHtml(item.project_name)}</span>` : "",
    item.tags && item.tags.length ? `<span>🏷 ${escapeHtml(item.tags.join(", "))}</span>` : "",
    `<span>🕒 ${item.created_at.replace("T", " ").slice(0, 19)}</span>`,
  ].join("");
  const preview = (item.summary || item.body || "").slice(0, 240);

  li.innerHTML = `
    <div class="row1">
      <div class="title">${escapeHtml(item.title)}</div>
      <div class="badges">${badges}</div>
    </div>
    ${preview ? `<div class="preview">${escapeHtml(preview)}</div>` : ""}
    <div class="meta">${meta}</div>
    ${extraActions ? `<div class="row-actions">${extraActions}</div>` : ""}
  `;
  li.addEventListener("click", (e) => {
    if (e.target.closest("button[data-action]")) return;
    openDetail(item.id);
  });
  if (extraActions) {
    li.querySelectorAll("button[data-action]").forEach((b) => {
      b.addEventListener("click", async (e) => {
        e.stopPropagation();
        try {
          const action = b.dataset.action;
          if (action === "promote") await api("POST", `/api/memory/${item.id}/promote_stable`);
          else if (action === "reject") await api("POST", `/api/memory/${item.id}/reject_proposal`);
          toast(`${action}d #${item.id}`);
          loadProposed();
        } catch (err) { toast(err.message, "bad"); }
      });
    });
  }
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
  const url = new URL("/api/memory", location.origin);
  url.searchParams.set("limit", "100");
  url.searchParams.set("namespace", ns());
  const status = $("#filter-status").value;
  if (status) url.searchParams.set("status", status);
  const data = await api("GET", url.pathname + url.search);
  renderCards(data.items, $("#inbox-list"));
}

async function loadProposed() {
  const url = new URL("/api/memory", location.origin);
  url.searchParams.set("status", "proposed_stable");
  url.searchParams.set("namespace", ns());
  const data = await api("GET", url.pathname + url.search);
  const container = $("#proposed-list");
  container.innerHTML = "";
  if (!data.items.length) {
    container.innerHTML = `<li class="card"><div class="muted">Nothing awaiting promotion right now.</div></li>`;
    return;
  }
  const actions = `
    <button class="ok" data-action="promote">Promote to stable</button>
    <button class="warn" data-action="reject">Reject</button>
  `;
  data.items.forEach((i) => renderCard(i, container, actions));
}

async function doSearch() {
  const q = $("#search-input").value.trim();
  if (!q) { $("#search-results").innerHTML = ""; return; }
  const url = new URL("/api/search", location.origin);
  url.searchParams.set("q", q);
  url.searchParams.set("mode", $("#search-mode").value);
  url.searchParams.set("namespace", ns());
  const type = $("#search-type").value;
  const project = $("#search-project").value.trim();
  if (type) url.searchParams.set("type", type);
  if (project) url.searchParams.set("project", project);
  const data = await api("GET", url.pathname + url.search);
  renderCards(data.results, $("#search-results"));
}

async function loadDecisions() {
  const data = await api("GET", withNs("/api/decisions"));
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
  const data = await api("GET", withNs("/api/tasks"));
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

async function loadProjects() {
  const data = await api("GET", "/api/projects");
  const ul = $("#projects-list");
  ul.innerHTML = "";
  if (!data.projects.length) {
    ul.innerHTML = `<li class="card"><div class="muted">No projects yet.</div></li>`; return;
  }
  data.projects.forEach((p) => {
    const li = document.createElement("li");
    li.className = "card";
    li.innerHTML = `
      <div class="row1">
        <div class="title">${escapeHtml(p.name)}</div>
        <div class="badges">${badgeHtml(p.slug, "type")}</div>
      </div>
      ${p.description ? `<div class="preview">${escapeHtml(p.description)}</div>` : ""}
      <div class="row-actions">
        <a class="btn" href="/api/projects/${encodeURIComponent(p.slug)}/pack.md?namespace=${ns()}">Download pack.md</a>
        <button data-pack="${escapeHtml(p.slug)}">Copy pack to clipboard</button>
      </div>
    `;
    li.querySelector("[data-pack]").addEventListener("click", async (e) => {
      e.stopPropagation();
      try {
        const r = await fetch(`/api/projects/${encodeURIComponent(p.slug)}/pack.md?namespace=${ns()}`);
        const md = await r.text();
        await navigator.clipboard.writeText(md);
        toast(`copied pack for ${p.slug}`);
      } catch (err) { toast(err.message, "bad"); }
    });
    ul.appendChild(li);
  });
}

async function loadEvents() {
  const url = new URL("/api/events", location.origin);
  url.searchParams.set("limit", "200");
  url.searchParams.set("namespace", ns());
  const data = await api("GET", url.pathname + url.search);
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
  const item = state.currentItem || { title: "", body: "", summary: "", type: "note", status: "inbox", importance: "medium", project_name: "", tags: [], links: [] };
  $("#d-title").value = item.title || "";
  $("#d-summary").value = item.summary || "";
  $("#d-body").value = item.body || "";
  $("#d-type").value = item.type || "note";
  $("#d-status").value = item.status || "inbox";
  $("#d-importance").value = item.importance || "medium";
  $("#d-project").value = item.project_slug || item.project_name || "";
  $("#d-tags").value = (item.tags || []).join(", ");
  $("#d-created").textContent = id ? `#${id} · ${item.namespace || "work"} · created ${item.created_at}` : "new memory";
  $("#d-append").value = "";
  $("#d-archive").disabled = !id;

  // Stable workflow buttons
  $("#d-propose").classList.toggle("hidden", !id || ["stable","proposed_stable","archived","rejected"].includes(item.status));
  $("#d-promote").classList.toggle("hidden", !id || item.status !== "proposed_stable");
  $("#d-reject").classList.toggle("hidden",  !id || item.status !== "proposed_stable");

  // Links + contradictions
  const links = (item.links || []);
  const rel = $("#d-related");
  if (links.length) {
    rel.innerHTML = `<h4>Related (${links.length})</h4>` + links.map((l) => `
      <div class="link-row">
        <span class="link-type ${l.link_type}">${l.link_type}${l.direction === "in" ? " ←" : " →"}</span>
        <a href="#" data-open="${l.other_id}">#${l.other_id} · ${escapeHtml(l.other_title)}</a>
        <span class="muted">${escapeHtml(l.other_status)}</span>
      </div>
    `).join("");
    rel.querySelectorAll("[data-open]").forEach((a) => {
      a.addEventListener("click", (e) => { e.preventDefault(); openDetail(parseInt(a.dataset.open, 10)); });
    });
    rel.classList.remove("hidden");
  } else {
    rel.innerHTML = ""; rel.classList.add("hidden");
  }
  $("#d-duplicates").innerHTML = "";
  $("#d-duplicates").classList.add("hidden");

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
  if (state.currentItem && state.currentItem.status === "stable") {
    if (!confirm("This is STABLE memory. Save changes anyway?")) return;
  }
  try {
    let item;
    if (state.currentId) {
      item = await api("PATCH", `/api/memory/${state.currentId}`, payload);
    } else {
      payload.namespace = ns();
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

async function proposeDetail() {
  if (!state.currentId) return;
  try {
    const item = await api("POST", `/api/memory/${state.currentId}/propose_stable`, { reason: null });
    state.currentItem = item;
    toast("proposed for stable");
    closeDetail();
    await loadInbox();
  } catch (e) { toast(e.message, "bad"); }
}

async function promoteDetail() {
  if (!state.currentId) return;
  try {
    const item = await api("POST", `/api/memory/${state.currentId}/promote_stable`);
    toast("promoted to stable");
    state.currentItem = item;
    closeDetail();
    await loadInbox();
  } catch (e) { toast(e.message, "bad"); }
}

async function rejectDetail() {
  if (!state.currentId) return;
  try {
    await api("POST", `/api/memory/${state.currentId}/reject_proposal`);
    toast("proposal rejected");
    closeDetail();
    await loadProposed();
  } catch (e) { toast(e.message, "bad"); }
}

async function findDuplicates() {
  if (!state.currentId) { toast("save first", "bad"); return; }
  try {
    const data = await api("GET", `/api/memory/${state.currentId}/duplicates`);
    const dups = $("#d-duplicates");
    if (!data.candidates.length) {
      dups.innerHTML = `<h4>No likely duplicates found</h4>`;
      dups.classList.remove("hidden");
      return;
    }
    dups.innerHTML = `<h4>Possible duplicates (${data.count})</h4>` + data.candidates.map((c) => `
      <div class="dup-row">
        <span class="reason">${c.score}</span>
        <a href="#" data-open="${c.id}">#${c.id} · ${escapeHtml(c.title)}</a>
        <span class="muted">${c.reasons.join(", ")}</span>
        <button data-merge-into="${c.id}">Merge into</button>
      </div>
    `).join("");
    dups.classList.remove("hidden");
    dups.querySelectorAll("[data-open]").forEach((a) => {
      a.addEventListener("click", (e) => { e.preventDefault(); openDetail(parseInt(a.dataset.open, 10)); });
    });
    dups.querySelectorAll("[data-merge-into]").forEach((b) => {
      b.addEventListener("click", async (e) => {
        e.stopPropagation();
        const target = b.dataset.mergeInto;
        if (!confirm(`Merge #${state.currentId} into #${target}? This archives the current item.`)) return;
        try {
          await api("POST", `/api/memory/${state.currentId}/merge_into/${target}`);
          toast(`merged into #${target}`);
          closeDetail();
          await loadInbox();
        } catch (err) { toast(err.message, "bad"); }
      });
    });
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
  $("#set-personal").textContent = s.personal_mcp_enabled ? "enabled" : "disabled (UI-only writes)";
  const e = s.embeddings;
  $("#set-embed").textContent = e.available
    ? `${e.model} · ${e.dim}d · indexed ${e.count_indexed}/${e.count_total}`
    : "not available (keyword search only)";
  fillSelect($("#filter-status"), s.enums.status, "All statuses");
  fillSelect($("#search-type"), s.enums.type, "Any type");
  fillSelect($("#d-type"), s.enums.type, null);
  fillSelect($("#d-status"), s.enums.status, null);
  fillSelect($("#d-importance"), s.enums.importance, null);
  $("#ns-badge").textContent = ns();
  $("#ns-select").value = ns();
  $("#export-json").href = `/api/export/json?namespace=${ns()}`;
  $("#export-md").href = `/api/export/markdown?namespace=${ns()}`;
}

function applyNamespace(v) {
  if (v !== "work" && v !== "personal") v = "work";
  state.ns = v;
  document.body.dataset.namespace = v;
  localStorage.setItem(NS_KEY, v);
  $("#ns-badge").textContent = v;
  $("#export-json").href = `/api/export/json?namespace=${v}`;
  $("#export-md").href = `/api/export/markdown?namespace=${v}`;
}

function setTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  if (name === "inbox") loadInbox();
  else if (name === "proposed") loadProposed();
  else if (name === "decisions") loadDecisions();
  else if (name === "tasks") loadTasks();
  else if (name === "projects") loadProjects();
  else if (name === "events") loadEvents();
}

async function reindexEmbeddings() {
  try {
    toast("reindexing…");
    const r = await api("POST", "/api/embeddings/reindex");
    if (!r.available) toast("embeddings unavailable on this server", "bad");
    else { toast(`indexed ${r.indexed}/${r.total}`); await loadStatus(); }
  } catch (e) { toast(e.message, "bad"); }
}

async function importJson(file) {
  try {
    const fd = new FormData(); fd.append("file", file);
    const r = await fetch("/api/import/json", { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const data = await r.json();
    toast(`JSON imported · ${data.imported} new, ${data.skipped} skipped, ${data.errors} errors`);
    await loadInbox(); await loadStatus();
  } catch (e) { toast(e.message, "bad"); }
}

async function importMarkdown(file) {
  try {
    const fd = new FormData(); fd.append("file", file);
    const r = await fetch("/api/import/markdown", { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const data = await r.json();
    toast(`Markdown imported · ${data.imported} new, ${data.skipped} skipped, ${data.errors} errors`);
    await loadInbox(); await loadStatus();
  } catch (e) { toast(e.message, "bad"); }
}

function wire() {
  $$(".tab").forEach((t) => t.addEventListener("click", () => setTab(t.dataset.tab)));
  $("#refresh-inbox").addEventListener("click", loadInbox);
  $("#filter-status").addEventListener("change", loadInbox);
  $("#refresh-proposed").addEventListener("click", loadProposed);
  $("#search-btn").addEventListener("click", doSearch);
  $("#search-input").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
  $("#refresh-decisions").addEventListener("click", loadDecisions);
  $("#refresh-tasks").addEventListener("click", loadTasks);
  $("#refresh-projects").addEventListener("click", loadProjects);
  $("#refresh-events").addEventListener("click", loadEvents);
  $("#copy-endpoint").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("#mcp-url").textContent); toast("copied MCP URL"); }
    catch { toast("copy failed", "bad"); }
  });
  $("#new-memory").addEventListener("click", () => openDetail(null));
  $("#d-close").addEventListener("click", closeDetail);
  $("#d-save").addEventListener("click", saveDetail);
  $("#d-archive").addEventListener("click", archiveDetail);
  $("#d-append-btn").addEventListener("click", appendDetail);
  $("#d-suggest-dups").addEventListener("click", findDuplicates);
  $("#d-propose").addEventListener("click", proposeDetail);
  $("#d-promote").addEventListener("click", promoteDetail);
  $("#d-reject").addEventListener("click", rejectDetail);
  $("#ns-select").addEventListener("change", (e) => {
    applyNamespace(e.target.value);
    loadInbox();
    if ($("#view-proposed").classList.contains("active")) loadProposed();
  });
  $("#embed-reindex").addEventListener("click", reindexEmbeddings);
  $("#import-json-btn").addEventListener("click", () => $("#import-json-file").click());
  $("#import-md-btn").addEventListener("click", () => $("#import-md-file").click());
  $("#import-json-file").addEventListener("change", (e) => e.target.files[0] && importJson(e.target.files[0]));
  $("#import-md-file").addEventListener("change", (e) => e.target.files[0] && importMarkdown(e.target.files[0]));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#detail-overlay").classList.contains("hidden")) closeDetail();
  });
}

async function boot() {
  applyNamespace(state.ns);
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
