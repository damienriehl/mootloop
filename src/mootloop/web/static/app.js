/* MootLoop demo viewer. Read-only: every byte comes from the /api endpoints. */
"use strict";

const PERSONAS = [
  ["associate", "Associate"],
  ["partner", "Partner"],
  ["oc_associate", "OC Associate"],
  ["oc_partner", "OC Partner"],
  ["judge", "Judge Panel"],
  ["rubric_judge", "Rubric Judge"],
];

const $ = (id) => document.getElementById(id);

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]),
  );

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

/* --- theme ----------------------------------------------------------------- */

function initTheme() {
  const stored = localStorage.getItem("mootloop-theme");
  if (stored) document.documentElement.dataset.theme = stored;
  $("theme-toggle").addEventListener("click", () => {
    const root = document.documentElement;
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    const current = root.dataset.theme || (dark ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    localStorage.setItem("mootloop-theme", next);
  });
}

/* --- caption ----------------------------------------------------------------- */

function renderCaption(matter) {
  const cap = matter.caption || {};
  const jur = matter.jurisdiction || {};
  $("cap-state").textContent = `STATE OF ${(jur.state || "—").toUpperCase()}`;
  $("cap-court").textContent = (cap.court_name || "DISTRICT COURT").toUpperCase();
  $("cap-county").textContent = `COUNTY OF ${(cap.county || "—").toUpperCase()}`;
  $("cap-judge").textContent = cap.judge_name ? `JUDGE: ${cap.judge_name.toUpperCase()}` : "";
  $("cap-case").textContent = cap.case_number || "—";
  const byRole = (role) =>
    (matter.parties || [])
      .filter((p) => p.role === role)
      .map((p) => p.name)
      .join(", ") || "—";
  $("cap-plaintiff").textContent = `${byRole("plaintiff")},`;
  $("cap-defendant").textContent = `${byRole("defendant")},`;
  $("cap-ourside").textContent = matter.our_side ? `appearing for the ${matter.our_side}` : "";
}

/* --- run summary + persona strip ------------------------------------------------ */

function renderRun(run) {
  $("run-status").textContent = run.status || "—";
  if (run.status === "finished") $("run-status").classList.add("ok");
  $("run-requests").textContent = run.requests ?? "—";
  $("run-turns").textContent = run.completed_turns ?? "—";
  $("run-spend").textContent =
    run.spend_usd != null ? `$${Number(run.spend_usd).toFixed(2)}` : "—";
  $("run-rubric").textContent = run.rubric_version || "—";
  const exp = $("run-export");
  exp.textContent = run.export_ready ? "ready" : (run.blockers || []).join(", ") || "blocked";
  if (run.export_ready) exp.classList.add("ok");

  const strip = $("persona-strip");
  strip.innerHTML = "";
  const counts = run.persona_turns || {};
  for (const [key, label] of PERSONAS) {
    const li = document.createElement("li");
    li.className = "persona";
    li.innerHTML =
      `<div class="persona-role">${esc(label)}</div>` +
      `<div class="persona-turns">${counts[key] ?? 0}<small> turns</small></div>`;
    strip.appendChild(li);
  }
}

/* --- tabs ------------------------------------------------------------------------ */

function initTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const activate = (tab) => {
    for (const t of tabs) {
      const selected = t === tab;
      t.classList.toggle("is-active", selected);
      t.setAttribute("aria-selected", String(selected));
      $(t.getAttribute("aria-controls")).hidden = !selected;
    }
    tab.focus();
  };
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => activate(tab));
    tab.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight") activate(tabs[(i + 1) % tabs.length]);
      if (e.key === "ArrowLeft") activate(tabs[(i + tabs.length - 1) % tabs.length]);
    });
  });
}

/* --- requests table --------------------------------------------------------------- */

const GATE_ORDER = ["degeneracy", "completeness", "fabrication", "rubric", "citations", "decisions", "attestation"];

function chipsFor(gates) {
  return GATE_ORDER.filter((g) => g in gates)
    .map((g) => `<span class="chip chip-${esc(gates[g])}">${esc(g)} · ${esc(gates[g])}</span>`)
    .join("");
}

function renderRequests(requests) {
  const tbody = $("request-rows");
  tbody.innerHTML = "";
  for (const req of requests) {
    const tr = document.createElement("tr");
    tr.className = "request-row";
    tr.tabIndex = 0;
    tr.setAttribute("role", "button");
    tr.setAttribute("aria-expanded", "false");
    tr.dataset.requestId = req.request_id;
    const posture = [
      req.objections ? `${req.objections} objection${req.objections > 1 ? "s" : ""}` : "no objection",
      req.rfa_disposition ? `RFA: ${req.rfa_disposition}` : "",
      req.restructured ? '<span class="restructured">restructured</span>' : "",
    ]
      .filter(Boolean)
      .join(" · ");
    tr.innerHTML =
      `<td><span class="request-id">${esc(req.request_id)}</span></td>` +
      `<td><div class="request-text">${esc(req.text)}</div></td>` +
      `<td class="num">${req.turns}</td>` +
      `<td><div class="chips">${chipsFor(req.gates || {})}</div></td>` +
      `<td><span class="posture">${posture}</span></td>`;
    const open = () => openDetail(req);
    tr.addEventListener("click", open);
    tr.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        open();
      }
    });
    tbody.appendChild(tr);
  }
}

/* --- request detail ------------------------------------------------------------------ */

const STAGE_LABEL = {
  associate_draft: "draft",
  partner_loop: "partner loop",
  oc_attack: "OC attack",
  bolster: "bolster",
  judge_panel: "judge panel",
  restructure: "restructure",
  rubric_gate: "rubric gate",
};

function markRow(requestId) {
  for (const row of document.querySelectorAll(".request-row")) {
    const isOpen = row.dataset.requestId === requestId;
    row.classList.toggle("is-open", isOpen);
    row.setAttribute("aria-expanded", String(isOpen));
  }
}

async function openDetail(req) {
  const detail = $("request-detail");
  markRow(req.request_id);
  $("detail-id").textContent = req.request_id;
  $("detail-text").textContent = req.text;
  detail.hidden = false;

  const [turns, panel, response] = await Promise.all([
    getJSON(`/api/requests/${encodeURIComponent(req.request_id)}/turns`),
    getJSON(`/api/requests/${encodeURIComponent(req.request_id)}/panel`),
    getJSON(`/api/requests/${encodeURIComponent(req.request_id)}/response`).catch(() => null),
  ]);

  renderTimeline(turns);
  renderSurvival(panel);
  renderFinalResponse(response, req);
  detail.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function renderTimeline(turns) {
  const ol = $("detail-timeline");
  ol.innerHTML = "";
  for (const turn of turns) {
    const li = document.createElement("li");
    li.className = "timeline-turn";
    const details = document.createElement("details");
    details.innerHTML =
      `<summary class="turn-summary">` +
      `<span class="turn-stage">${esc(STAGE_LABEL[turn.stage] || turn.stage)}</span>` +
      `<span class="turn-persona">${esc(turn.persona.replace("_", " "))}</span>` +
      `<span class="turn-meta">${esc(turn.turn_id)} · attempt ${turn.attempt}</span>` +
      `<span class="turn-hint">output ▾</span>` +
      `</summary>` +
      `<pre class="turn-output">${esc(JSON.stringify(turn.output, null, 2))}</pre>`;
    li.appendChild(details);
    ol.appendChild(li);
  }
}

function renderSurvival(panel) {
  const wrap = $("detail-panel");
  wrap.innerHTML = "";
  if (!panel.length) {
    wrap.innerHTML = '<p class="survival-empty">No objections reached the judge panel.</p>';
    return;
  }
  for (const row of panel) {
    const pct = Math.round(row.survival_rate * 100);
    const weak = row.survival_rate < 0.5;
    const div = document.createElement("div");
    div.className = "survival-row";
    div.innerHTML =
      `<span class="survival-basis">obj[${row.objection_index}] ${esc(row.objection_basis)}</span>` +
      `<div class="bar" role="img" aria-label="${pct}% of judges say this objection survives">` +
      `<div class="bar-fill${weak ? " weak" : ""}" style="width:${pct}%"></div></div>` +
      `<span class="survival-count">${row.survive_votes}/${row.total_votes} survive (${pct}%)</span>`;
    wrap.appendChild(div);
  }
}

function renderFinalResponse(response, req) {
  const box = $("detail-response");
  if (!response) {
    box.innerHTML = '<p class="survival-empty">No operative draft.</p>';
    return;
  }
  let html = "";
  if (response.rfa_disposition) {
    html += `<p class="rfa-disposition">Rule 36 disposition: ${esc(response.rfa_disposition)}</p>`;
  }
  for (const obj of response.objections || []) {
    html += `<p class="objection"><strong>objection (${esc(obj.basis)})</strong> — ${esc(obj.text)}</p>`;
  }
  html += `<p class="response-text">${esc(response.response_text)}</p>`;
  box.innerHTML = html;
}

/* --- decisions ------------------------------------------------------------------------ */

function renderDecisions(decisions) {
  $("decisions-count").textContent = `(${decisions.length})`;
  const list = $("decision-list");
  list.innerHTML = "";
  for (const d of decisions) {
    const li = document.createElement("li");
    const resolved = d.status !== "open";
    li.className = `decision${resolved ? " is-resolved" : ""}`;
    const options = (d.proposal.options || [])
      .map((o) => {
        const chosen = d.resolution && d.resolution.chosen_key === o.key;
        return `<li class="${chosen ? "chosen" : ""}">${esc(o.label)} — <em>${esc(o.consequence)}</em></li>`;
      })
      .join("");
    const resolution = d.resolution
      ? `<p class="decision-resolution">${esc(d.status)} by ${esc(d.resolution.decided_by)} ` +
        `(${esc(d.resolution.source)}) · ${esc(d.resolution.decided_at)}</p>`
      : "";
    li.innerHTML =
      `<div class="decision-head">` +
      `<span class="decision-kind">${esc(d.kind)}</span>` +
      `<span class="decision-id">${esc(d.decision_id)}${d.request_id ? ` · ${esc(d.request_id)}` : ""}</span>` +
      `<span class="decision-status">${esc(d.status)}</span>` +
      `</div>` +
      `<p class="decision-summary">${esc(d.proposal.summary)}</p>` +
      `<p class="decision-reasoning">${esc(d.proposal.reasoning)}</p>` +
      `<ul class="decision-options">${options}</ul>` +
      resolution;
    list.appendChild(li);
  }
}

/* --- deliverables ---------------------------------------------------------------------- */

/* A deliberately small markdown renderer: headings, emphasis, lists, tables,
   blockquotes, fenced code, and the ::: anchor fences the masters use. */
function renderMarkdown(src) {
  const lines = src.split("\n");
  const out = [];
  let inCode = false;
  let listType = null;
  let inTable = false;

  const closeList = () => {
    if (listType) {
      out.push(`</${listType}>`);
      listType = null;
    }
  };
  const closeTable = () => {
    if (inTable) {
      out.push("</table>");
      inTable = false;
    }
  };
  const inline = (s) =>
    esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/_([^_]+)_/g, "<em>$1</em>");

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (line.startsWith("```")) {
      closeList();
      closeTable();
      out.push(inCode ? "</code></pre>" : "<pre><code>");
      inCode = !inCode;
      continue;
    }
    if (inCode) {
      out.push(esc(raw));
      continue;
    }
    if (line.startsWith(":::")) continue; // pandoc div anchors — invisible in the viewer
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      closeList();
      closeTable();
      out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`);
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      closeTable();
      if (listType !== "ul") {
        closeList();
        out.push("<ul>");
        listType = "ul";
      }
      out.push(`<li>${inline(line.replace(/^\s*[-*]\s+/, ""))}</li>`);
      continue;
    }
    if (line.startsWith("|")) {
      closeList();
      if (/^\|[\s:-]+\|/.test(line.replace(/[^|\s:-]/g, ""))
          && /^[|\s:-]+$/.test(line)) continue; // separator row
      const cells = line.split("|").slice(1, -1).map((c) => inline(c.trim()));
      if (!inTable) {
        out.push("<table>");
        inTable = true;
        out.push(`<tr>${cells.map((c) => `<th>${c}</th>`).join("")}</tr>`);
      } else {
        out.push(`<tr>${cells.map((c) => `<td>${c}</td>`).join("")}</tr>`);
      }
      continue;
    }
    closeTable();
    if (line.startsWith(">")) {
      closeList();
      out.push(`<blockquote>${inline(line.replace(/^>\s?/, ""))}</blockquote>`);
      continue;
    }
    if (/^[-_]{3,}$/.test(line)) {
      closeList();
      out.push("<hr>");
      continue;
    }
    if (!line.trim()) {
      closeList();
      continue;
    }
    closeList();
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList();
  closeTable();
  if (inCode) out.push("</code></pre>");
  return out.join("\n");
}

function renderDeliverables(items) {
  const list = $("deliverable-list");
  list.innerHTML = "";
  for (const item of items) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "deliverable-link";
    btn.textContent = item.name;
    btn.addEventListener("click", async () => {
      for (const b of list.querySelectorAll(".deliverable-link")) b.classList.remove("is-active");
      btn.classList.add("is-active");
      const view = $("deliverable-view");
      const res = await fetch(`/api/deliverables/${item.name}`);
      const text = await res.text();
      if (item.media_type === "application/json") {
        view.innerHTML = `<pre class="turn-output">${esc(JSON.stringify(JSON.parse(text), null, 2))}</pre>`;
      } else {
        view.innerHTML = `<div class="md">${renderMarkdown(text)}</div>`;
      }
    });
    li.appendChild(btn);
    list.appendChild(li);
  }
}

/* --- boot -------------------------------------------------------------------------------- */

async function boot() {
  initTheme();
  initTabs();
  $("detail-close").addEventListener("click", () => {
    $("request-detail").hidden = true;
    markRow(null);
  });
  try {
    const [matter, run, requests, decisions, deliverables] = await Promise.all([
      getJSON("/api/matter"),
      getJSON("/api/run"),
      getJSON("/api/requests"),
      getJSON("/api/decisions"),
      getJSON("/api/deliverables"),
    ]);
    renderCaption(matter);
    renderRun(run);
    renderRequests(requests);
    renderDecisions(decisions);
    renderDeliverables(deliverables);
  } catch (err) {
    document.querySelector("main").insertAdjacentHTML(
      "afterbegin",
      `<p role="alert" style="color:var(--fail)">Could not load the demo run: ${esc(err.message)}</p>`,
    );
  }
}

boot();
