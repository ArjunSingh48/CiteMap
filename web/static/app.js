/* CiteMap web app — plain JS, no build step.
   Live mode: talks to the FastAPI server (/api/...), any date can be queried.
   Static mode (fail-open): if the API is unreachable, it reads data/*.json exported by the
   pipeline and shows the default-date answers. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const app = $("#app");
  const S = { live: null, lang: "en", user: null, rules: null, detail: null, changes: null, tests: null };
  const DEFAULT_DATE = "2026-10-01";
  const CAT = {
    rent_increase_limits: ["Rent increase limits", "Límites de aumento de renta"],
    just_cause_eviction: ["Just-cause eviction", "Desalojo con causa justa"],
    security_deposits: ["Security deposits", "Depósitos de garantía"],
    application_screening_fees: ["Application & screening fees", "Tarifas de solicitud"],
    screening_restrictions: ["Screening restrictions", "Restricciones de evaluación"],
    algorithmic_rent_setting: ["Algorithmic rent-setting", "Fijación algorítmica de rentas"],
  };
  const RES = {
    applies: ["Applies", "Aplica"], unknown: ["Unknown", "Desconocido"], superseded: ["Superseded", "Desplazada"],
    not_yet_effective: ["Not yet effective", "Aún no vigente"], pending: ["Pending bill", "Proyecto de ley"],
  };
  const ORDER = ["applies", "unknown", "superseded", "not_yet_effective", "pending"];
  const T = (en, es) => (S.lang === "es" ? es : en);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const chip = (r) => `<span class="chip ${r}">${esc(RES[r] ? RES[r][S.lang === "es" ? 1 : 0] : r)}</span>`;
  const safeUrl = (u) => (/^https?:\/\//i.test(String(u || "")) ? String(u) : "#");   // never a javascript: link
  const STATUS = { in_force: ["In force", "Vigente"], not_yet_effective: ["Not yet in effect", "Aún no vigente"],
    pending: ["Proposed bill (not law)", "Proyecto (no es ley)"], failed: ["Failed / struck", "Rechazada"] };
  const statusName = (s) => (STATUS[s] ? STATUS[s][S.lang === "es" ? 1 : 0] : s);
  const catName = (c) => (CAT[c] ? CAT[c][S.lang === "es" ? 1 : 0] : c);
  const toast = (m) => { const t = document.createElement("div"); t.className = "toast"; t.textContent = m; document.body.append(t); setTimeout(() => t.remove(), 2600); };

  async function api(path, opts = {}) {
    const r = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...opts });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(j.detail || j.error || "Request failed"), { status: r.status });
    return j;
  }
  async function detectMode() {
    try { await api("api/health"); S.live = true; } catch { S.live = false; }
    $("#modeNote").textContent = S.live ? "Live mode: answers computed on the server for any date."
      : "Offline mode: showing saved answers for " + DEFAULT_DATE + " (the server is not reachable).";
  }
  async function staticData() {
    if (!S.rules) {
      const [r, d, c, t] = await Promise.all(["rules.json", "lookups_detail.json", "changes.json", "change_tests.json"]
        .map((f) => fetch("data/" + f).then((x) => x.json()).catch(() => null)));
      S.rules = r ? r.rules : []; S.detail = d ? d.lookups : {}; S.changes = c || {}; S.tests = t || [];
    }
  }
  async function getRules() {
    if (S.live) return (await api("api/rules")).data;
    await staticData(); return S.rules;
  }
  async function searchAddresses(q) {
    if (S.live) return (await api("api/addresses?limit=8&q=" + encodeURIComponent(q))).data;
    await staticData();
    const ql = q.toLowerCase();
    return Object.entries(S.detail).filter(([id, v]) => (id + " " + v.address.street_address + " " + v.address.postal_city + " " + v.address.zip).toLowerCase().includes(ql))
      .slice(0, 8).map(([id, v]) => ({ id, street: v.address.street_address, city: v.jurisdiction.city, state: v.jurisdiction.state, postal_city: v.address.postal_city }));
  }
  async function getLookup(id, asOf) {
    if (S.live) return (await api(`api/lookup/${encodeURIComponent(id)}?as_of=${asOf}`)).data;
    await staticData();
    const v = S.detail[id]; if (!v) throw new Error("Address not found");
    const byId = Object.fromEntries(S.rules.map((r) => [r.team_rule_id, r]));
    return { ...v, results: v.results.map((it) => ({ ...it, rule: byId[it.team_rule_id] })) };
  }
  async function getChanges() {
    if (S.live) return (await api("api/changes")).data;
    await staticData();
    const tests = Object.fromEntries(S.tests.map((t) => [t.test_id, t]));
    return Object.entries(S.changes).map(([k, v]) => ({ test_id: k, title: (tests[k] || {}).title || "New law", type: (tests[k] || {}).type, expected: (tests[k] || {}).expected_behavior, ...v }));
  }

  // ---------- views ----------
  function setNav(r) { document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.r === r)); }

  async function viewHome() {
    setNav("home");
    let stats = null;
    const myHash = location.hash;
    try { stats = S.live ? (await api("api/stats")).data : null; } catch {}
    if (!stats) {
      await staticData();
      const by = {}; const cities = {};
      Object.values(S.detail).forEach((v) => { cities[v.jurisdiction.city] = (cities[v.jurisdiction.city] || 0) + 1; v.results.forEach((i) => (by[i.result] = (by[i.result] || 0) + 1)); });
      stats = { counts: { rules: S.rules.length, addresses: Object.keys(S.detail).length }, by_result: Object.entries(by).map(([result, n]) => ({ result, n })), by_city: Object.entries(cities).map(([city, n]) => ({ city, n })) };
    }
    if (location.hash !== myHash) return;
    const n = (r) => (stats.by_result.find((x) => x.result === r) || {}).n || 0;
    const total = stats.by_result.reduce((a, b) => a + b.n, 0);
    app.innerHTML = `
    <section class="hero">
      <div>
        <h1>${T("Which rules apply to <em>this</em> apartment today?", "¿Qué normas aplican a <em>este</em> apartamento hoy?")}</h1>
        <p class="lead">${T("Search any of 500 apartment buildings in California, New Jersey and Massachusetts. CiteMap extracts rules from the law texts with rule-based code, decides with transparent rules, and quotes the exact text behind every answer.",
          "Busque entre 500 edificios en California, Nueva Jersey y Massachusetts. CiteMap extrae normas de los textos legales con código basado en reglas, decide con reglas transparentes y cita el texto exacto de cada respuesta.")}</p>
        <div class="search">
          <span class="icon">⌕</span>
          <input id="q" autocomplete="off" placeholder="${T("Street, ZIP or address id — e.g. Mission, 07030, A0001", "Calle, código postal o id — p. ej. Mission, 07030, A0001")}" aria-label="Search address">
          <div class="sugg" id="sugg" hidden></div>
        </div>
        <div class="chips">${["Mission", "Clinton", "Dorchester", "Commonwealth", "Van Nuys"].map((s) => `<span class="pill" role="button" tabindex="0" data-q="${s}">${s}</span>`).join("")}</div>
      </div>
      <div class="stats">
        <div class="card stat hero-n"><div class="n">${total.toLocaleString()}</div><div class="l">${T("address-level answers, each tied to a quoted source", "respuestas por dirección, cada una con su fuente citada")}</div></div>
        <div class="card stat"><div class="n">${stats.counts.rules}</div><div class="l">${T("rules extracted automatically", "normas extraídas automáticamente")}</div></div>
        <div class="card stat"><div class="n">${T("Exact", "Exactas")}</div><div class="l">${T("every quote is checked against the source text before a rule is kept", "cada cita se comprueba contra el texto fuente antes de guardar la norma")}</div></div>
        <div class="card stat"><div class="n">${n("unknown")}</div><div class="l">${T("honest “unknown” answers (missing public data)", "respuestas “desconocido” honestas")}</div></div>
        <div class="card stat"><div class="n">${n("superseded")}</div><div class="l">${T("state rules yielding to stricter local law", "normas estatales desplazadas por la local")}</div></div>
      </div>
    </section>
    <section class="section">
      <div class="section-head"><h2>${T("Cities covered", "Ciudades cubiertas")}</h2><span class="why">${T("as of", "al")} ${DEFAULT_DATE}</span></div>
      <div class="cities">${stats.by_city.map((c) => `<div class="card city" role="button" tabindex="0" data-city="${esc(c.city)}"><b>${esc(c.city)}</b><small>${c.n} ${T("buildings", "edificios")}</small></div>`).join("")}</div>
    </section>
    <section class="section">
      <div class="section-head"><h2>${T("How an answer is made", "Cómo se produce una respuesta")}</h2><a href="#/how">${T("Details & trade-offs →", "Detalles →")}</a></div>
      <div class="steps">${[["1 · READ", T("Extract", "Extraer"), T("Each law document is read and turned into rule records.", "Cada ley se convierte en registros de normas.")],
        ["2 · VERIFY", T("Verify", "Verificar"), T("Every quote must exist word-for-word in the source.", "Cada cita debe existir literalmente en la fuente.")],
        ["3 · RESOLVE", T("Resolve", "Resolver"), T("The address is placed in its legal state and city.", "La dirección se ubica en su estado y ciudad legal.")],
        ["4 · DECIDE", T("Decide", "Decidir"), T("Plain code tests coverage: yes, no or unknown.", "Código simple evalúa la cobertura: sí, no o desconocido.")],
        ["5 · TRACK", T("Track", "Seguir"), T("Any date, any new law: see what changes.", "Cualquier fecha o ley nueva: vea qué cambia.")]]
        .map(([k, b, p]) => `<div class="card step"><div class="k">${k}</div><b>${b}</b><p>${p}</p></div>`).join("")}</div>
    </section>`;
    wireSearch();
    app.querySelectorAll(".pill[data-q]").forEach((p) => p.onclick = () => { $("#q").value = p.dataset.q; $("#q").dispatchEvent(new Event("input")); $("#q").focus(); });
    app.querySelectorAll(".city").forEach((c) => c.onclick = async () => { $("#q").value = ""; const res = S.live ? (await api("api/addresses?limit=8&city=" + encodeURIComponent(c.dataset.city))).data : (await searchAddresses("")).filter((x) => x.city === c.dataset.city); showSugg(res); $("#q").focus(); });
  }

  function showSugg(list) {
    const box = $("#sugg");
    if (!box) return;                       // user already left the page
    if (!list.length) { box.innerHTML = `<div class="empty">${T("No address found", "No se encontró la dirección")}</div>`; box.hidden = false; return; }
    box.innerHTML = list.map((a, i) => `<a href="#/a/${esc(a.id)}" class="${i === 0 ? "sel" : ""}"><span><b>${esc(a.street)}</b> · ${esc(a.city || a.postal_city)}, ${esc(a.state)}</span><small>${esc(a.id)}</small></a>`).join("");
    box.hidden = false;
  }
  function wireSearch() {
    const q = $("#q"); let t;
    q.addEventListener("input", () => { clearTimeout(t); const v = q.value.trim(); if (v.length < 2) { $("#sugg").hidden = true; return; } t = setTimeout(async () => { const r = await searchAddresses(v).catch(() => []); if ($("#q") === q) showSugg(r); }, 160); });
    q.addEventListener("keydown", (e) => { if (e.key === "Enter") { const a = $("#sugg a"); if (a) location.hash = a.getAttribute("href"); } if (e.key === "Escape") $("#sugg").hidden = true; });
  }

  async function viewAddress(id, asOf) {
    setNav("home");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(asOf || "") || isNaN(Date.parse(asOf + "T00:00:00Z")) || new Date(asOf + "T00:00:00Z").toISOString().slice(0, 10) !== asOf) asOf = DEFAULT_DATE;
    app.innerHTML = `<div class="empty">${T("Reading the law for this address…", "Leyendo la ley para esta dirección…")}</div>`;
    let d;
    const myHash = location.hash;
    try { d = await getLookup(id, asOf); } catch (e) { if (location.hash === myHash) app.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    if (location.hash !== myHash) return;   // a newer navigation happened while loading
    const a = d.address, j = d.jurisdiction, f = d.facts;
    const units = f.units_min == null ? null : (f.units_max === f.units_min ? f.units_min : `${f.units_min}${f.units_max ? "–" + f.units_max : "+"}`);
    const counts = {}; d.results.forEach((r) => (counts[r.result] = (counts[r.result] || 0) + 1));
    const groups = {}; d.results.forEach((r) => (groups[r.rule.category] = groups[r.rule.category] || []).push(r));
    let saved = false;
    if (S.user && S.live) { try { saved = (await api("api/saved")).data.some((s) => s.address_id === id); } catch {} }
    app.innerHTML = `
      <div class="addr-head">
        <div>
          <a href="#/" class="why">← ${T("New search", "Nueva búsqueda")}</a>
          <h1 style="font-size:34px;margin-top:6px">${esc(a.street_address)}</h1>
          <div class="crumb">${T("Jurisdiction", "Jurisdicción")}: <b>${esc(j.state)}</b> › <b>${esc(j.county || "")}</b> › <b>${esc(j.city || "—")}</b>
            <span class="chip outline" title="${esc(j.note || "")}">${esc(j.method.replace(/_/g, " "))}</span></div>
          <div class="facts">
            <span class="fact">${T("Built", "Construido")}: ${f.year_built ?? `<span class="m">${T("not in public data", "sin dato público")}</span>`}</span>
            <span class="fact">${T("Units", "Unidades")}: ${units ?? `<span class="m">${T("not in public data", "sin dato público")}</span>`} <small class="why">(${esc(f.units_source)})</small></span>
            <span class="fact">${T("Owner type", "Tipo de propietario")}: <span class="m">${T("never in public data", "nunca en datos públicos")}</span></span>
            <span class="fact">${esc(f.use_description)}</span>
          </div>
        </div>
        <div>${S.user && S.live ? `<button class="btn ${saved ? "" : "primary"}" id="saveBtn">${saved ? T("Saved ✓", "Guardado ✓") : T("Save address", "Guardar")}</button>` : `<a class="btn ghost" href="#/account">${T("Sign in to save", "Inicie sesión para guardar")}</a>`}</div>
      </div>
      <div class="toolbar">
        <b>${T("Answer as of", "Respuesta al")}</b>
        <input type="date" id="asof" value="${asOf}" ${S.live ? "" : "disabled title='Live server needed for other dates'"}>
        ${[["2025-12-31", "31 Dec 2025"], ["2026-01-02", "2 Jan 2026"], [DEFAULT_DATE, T("Today", "Hoy")], ["2027-07-02", "2 Jul 2027"]]
          .map(([v, l]) => `<button class="btn ${v === asOf ? "primary" : ""}" data-date="${v}" ${S.live ? "" : "disabled"}>${l}</button>`).join("")}
        <span class="why" style="margin-left:auto">${T("Every answer cites its source", "Cada respuesta cita su fuente")}</span>
      </div>
      <div class="summary">${ORDER.filter((r) => counts[r]).map((r) => `${chip(r)}<b>${counts[r]}</b>`).join(" &nbsp; ")}</div>
      ${Object.keys(CAT).filter((c) => groups[c]).map((c) => `
        <div class="cat"><h3>${catName(c)}</h3>${groups[c].sort((x, y) => ORDER.indexOf(x.result) - ORDER.indexOf(y.result)).map(ruleCard).join("")}</div>`).join("")}
      ${Object.keys(CAT).filter((c) => !groups[c]).length ? `<div class="cat"><h3>${T("Not found in our sources for this address", "No encontrado en nuestras fuentes para esta dirección")}</h3><div class="why">${Object.keys(CAT).filter((c) => !groups[c]).map(catName).join(" · ")}</div></div>` : ""}`;
    app.querySelectorAll("[data-date]").forEach((b) => b.onclick = () => (location.hash = `#/a/${id}/${b.dataset.date}`));
    const ai = $("#asof"); if (ai) ai.onchange = () => ai.value && (location.hash = `#/a/${id}/${ai.value}`);
    const sb = $("#saveBtn"); if (sb) sb.onclick = async () => { await api(`api/saved/${id}`, { method: saved ? "DELETE" : "POST" }); toast(saved ? T("Removed", "Eliminado") : T("Saved to your account", "Guardado")); viewAddress(id, asOf); };
  }

  function ruleCard(it) {
    const r = it.rule || {};
    const ex = S.lang === "es" && it.explanation_es ? it.explanation_es : it.explanation;
    const cc = r.coverage_conditions || {};
    const missing = (it.missing_facts || []).map((m) => m.replace(/_/g, " "));
    return `<div class="card rule ${it.result}">
      <div class="row"><div>${chip(it.result)} ${it.conflict_flag ? `<span class="chip conflict">${T("Conflict — review", "Conflicto — revisar")}</span>` : ""}
        <div class="title" style="margin-top:6px">${esc(r.title || r.citation)}</div></div>
        ${r.key_value && /\d/.test(r.key_value) ? `<div class="kv">${esc(r.key_value)}</div>` : ""}</div>
      <p class="ex">${esc(ex)}</p>
      <div class="cite">${esc(r.citation)} · ${esc(r.jurisdiction)} · ${T("status", "estado")}: ${esc(statusName(r.status))}${r.effective_date ? " · " + T("effective", "vigente") + " " + esc(r.effective_date) : ""}</div>
      ${r.conflict_note && it.conflict_flag ? `<div class="note-conf">${esc(r.conflict_note)}</div>` : ""}
      <details><summary>${T("Show the law", "Ver la ley")}</summary>
        <blockquote>${esc(r.quoted_span)}</blockquote>
        <div class="why">${T("Source", "Fuente")}: <a href="${esc(safeUrl(r.source_url))}" target="_blank" rel="noopener noreferrer">${esc(r.source_doc_id)}</a> · ${T("retrieved", "consultado")} ${esc(r.retrieval_date)} · ${T("source type", "tipo de fuente")}: ${esc(r.source_authority || "—")}</div>
        <div class="why">${T("Extraction confidence", "Confianza de extracción")}: ${r.confidence == null ? "—" : r.confidence >= 0.85 ? T("High", "Alta") : r.confidence >= 0.65 ? T("Medium", "Media") : T("Low", "Baja")} (${r.confidence ?? "—"}) <i>(${T("heuristic score of the rule reader, not a probability that the law applies", "puntuación heurística del lector de normas, no una probabilidad")})</i> · ${T("extracted by", "extraído por")} ${esc(r.extracted_by)}</div>
        ${r.exemptions ? `<div class="why">${T("Exemptions stated in the source", "Exenciones en la fuente")}: “${esc(r.exemptions)}”</div>` : ""}
        ${r.penalty ? `<div class="why">${T("Penalty stated in the source", "Sanción en la fuente")}: ${esc(r.penalty)}</div>` : ""}
      </details>
      <details><summary>${T("Why this answer?", "¿Por qué esta respuesta?")}</summary>
        ${it.certainty ? `<div class="why"><b>${T("Applicability", "Aplicabilidad")}:</b> ${esc(it.certainty)}${it.unknown_reason ? " · " + T("reason", "motivo") + ": " + esc(it.unknown_reason.replace(/_/g, " ")) : ""}</div>` : ""}
        ${(it.trace || []).length ? `<ol class="why trace">${it.trace.map((t) => `<li>${esc(t)}</li>`).join("")}</ol>` : ""}
        <ul class="why">
          <li>${T("Coverage", "Cobertura")}: ${esc(cc.text || "—")}</li>
          ${missing.length ? `<li>${T("Missing facts", "Datos faltantes")}: <b>${esc(missing.join(", "))}</b></li>` : ""}
          ${(cc.evidence || []).slice(0, 3).map((e) => `<li>${T("Evidence", "Evidencia")} (${esc(e.doc_id)}): “${esc(e.text)}”</li>`).join("")}
          ${r.interaction ? `<li>${esc(r.interaction)}</li>` : ""}
        </ul></details>
    </div>`;
  }

  async function viewChanges() {
    setNav("changes");
    app.innerHTML = `<div class="empty">…</div>`;
    const myHash = location.hash;
    const list = await getChanges();
    let cityOf = {};
    await staticData().catch(() => {});
    if (S.detail) Object.entries(S.detail).forEach(([id, v]) => (cityOf[id] = v.jurisdiction.city));
    if (location.hash !== myHash) return;
    app.innerHTML = `<h1>${T("What's changing", "Qué está cambiando")}</h1>
      <p class="lead">${T("Real 2025–2027 law changes, pending bills and a struck ballot question. For each, the addresses whose answer changes — enacted law is never mixed with proposals.",
        "Cambios reales de 2025–2027, proyectos de ley y una pregunta electoral anulada.")}</p>
      ${list.map((c) => {
        const by = {}; c.affected_address_ids.forEach((a) => (by[cityOf[a] || "?"] = (by[cityOf[a] || "?"] || 0) + 1));
        const fl = {}; c.conflict_flag_address_ids.forEach((a) => (fl[cityOf[a] || "?"] = (fl[cityOf[a] || "?"] || 0) + 1));
        const max = Math.max(1, ...Object.values(by));
        return `<div class="card change">
          <div class="meta"><span class="chip outline">${esc(c.test_id)}</span><b style="font-size:17px">${esc(c.title)}</b>
            <span class="chip ${c.type === "pending" ? "pending" : c.type === "negative" ? "superseded" : "not_yet_effective"}">${esc(c.type || "new law")}</span></div>
          ${c.expected ? `<p class="why">${T("Expected", "Esperado")}: ${esc(c.expected)}</p>` : ""}
          <p><b class="${c.affected_address_ids.length || c.type === "negative" ? "ok" : ""}">${c.affected_address_ids.length}</b> ${T("addresses affected", "direcciones afectadas")}
            ${c.conflict_flag_address_ids.length ? ` · <b style="color:var(--conflict)">${c.conflict_flag_address_ids.length}</b> ${T("flagged for human review", "marcadas para revisión")}` : ""}</p>
          <div class="bars">${Object.entries(by).sort((a, b) => b[1] - a[1]).map(([city, n]) => `<div class="bar"><span>${esc(city)}</span><div class="track"><div class="fill ${fl[city] ? "flag" : ""}" style="width:${(100 * n) / max}%"></div></div><b>${n}</b></div>`).join("") || `<div class="why">${T("Empty set — as required.", "Conjunto vacío — como se requiere.")}</div>`}</div>
          <details><summary>${T("How this was computed", "Cómo se calculó")}</summary><p class="why">${esc(c.notes)}</p></details>
        </div>`; }).join("")}`;
  }

  async function viewRules() {
    setNav("rules");
    const myHash = location.hash;
    const rules = await getRules();
    if (location.hash !== myHash) return;
    const juris = [...new Set(rules.map((r) => r.jurisdiction))].sort();
    app.innerHTML = `<h1>${T("Rule library", "Biblioteca de normas")}</h1>
      <p class="lead">${T("Every rule the system extracted, with its citation, status and exact quote.", "Todas las normas extraídas, con cita, estado y texto exacto.")}</p>
      <div class="filters">
        <select id="fc"><option value="">${T("All categories", "Todas las categorías")}</option>${Object.keys(CAT).map((c) => `<option value="${c}">${catName(c)}</option>`).join("")}</select>
        <select id="fj"><option value="">${T("All jurisdictions", "Todas las jurisdicciones")}</option>${juris.map((j) => `<option>${esc(j)}</option>`).join("")}</select>
        <select id="fs"><option value="">${T("All statuses", "Todos los estados")}</option>${["in_force", "not_yet_effective", "pending", "failed"].map((s) => `<option value="${s}">${statusName(s)}</option>`).join("")}</select>
      </div>
      <table><thead><tr><th>ID</th><th>${T("Jurisdiction", "Jurisdicción")}</th><th>${T("Category", "Categoría")}</th><th>${T("Key value", "Valor")}</th><th>${T("Status", "Estado")}</th><th>${T("Citation", "Cita")}</th></tr></thead><tbody id="tb"></tbody></table>
      <div id="rd"></div>`;
    const draw = () => {
      if (!$("#tb")) return;
      const c = $("#fc").value, j = $("#fj").value, s = $("#fs").value;
      $("#tb").innerHTML = rules.filter((r) => (!c || r.category === c) && (!j || r.jurisdiction === j) && (!s || r.status === s))
        .map((r) => `<tr class="click" role="button" tabindex="0" data-id="${r.team_rule_id}"><td class="cite">${r.team_rule_id}</td><td>${esc(r.jurisdiction)}</td><td>${catName(r.category)}</td><td>${esc(r.key_value || "—")}</td><td>${esc(statusName(r.status))}${r.conflict_flag ? ' <span class="chip conflict">!</span>' : ""}</td><td class="cite">${esc(r.citation)}</td></tr>`).join("");
      app.querySelectorAll("tr.click").forEach((tr) => tr.onclick = () => {
        const r = rules.find((x) => x.team_rule_id === tr.dataset.id);
        if (!r || !$("#rd")) return;
        $("#rd").innerHTML = `<div class="card box" style="margin-top:14px"><h2>${esc(r.title)}</h2><p>${esc(r.requirement)}</p>
          <div class="cite">${esc(r.citation)} · ${esc(r.status)} · ${T("effective", "vigente")} ${esc(r.effective_date || "—")}</div>
          <blockquote>${esc(r.quoted_span)}</blockquote><div class="why">${esc((r.coverage_conditions || {}).text || "")}</div>
          ${r.conflict_note ? `<div class="note-conf">${esc(r.conflict_note)}</div>` : ""}
          <div class="why"><a href="${esc(safeUrl(r.source_url))}" target="_blank" rel="noopener noreferrer">${esc(r.source_url)}</a> · ${T("retrieved", "consultado")} ${esc(r.retrieval_date)}</div></div>`;
        $("#rd").scrollIntoView({ behavior: "smooth" });
      });
    };
    ["#fc", "#fj", "#fs"].forEach((s) => ($(s).onchange = draw)); draw();
  }

  async function viewHow() {
    setNav("how");
    let log = [];
    const myHash = location.hash;
    if (S.live) { try { log = (await api("api/audit?limit=40")).data; } catch {} }
    if (location.hash !== myHash) return;
    app.innerHTML = `<h1>${T("How it works", "Cómo funciona")}</h1>
      <p class="lead">${T("No AI at answer time: rules are extracted automatically by rule-based code from the law corpus, then every address is decided by transparent code. Same input, same answer, every time.",
        "Sin IA al responder: las normas se extraen automáticamente y cada dirección se decide con código transparente.")}</p>
      <div class="steps section">${[["EXTRACT", "Document profiling → sentence scoring → key values → coverage patterns"], ["VERIFY", "Official schema check + quote must be an exact substring of the source"],
        ["RECONCILE", "Drop noise, merge duplicates, link precedence, flag conflicts — every step logged"], ["DECIDE", "Three-valued logic: true / false / unknown, never a guess"], ["TRACK", "Any date + any new law file → affected addresses"]]
        .map(([k, p]) => `<div class="card step"><div class="k">${k}</div><p>${p}</p></div>`).join("")}</div>
      <div class="two section">
        <div class="card box"><h2>${T("Trade-offs we chose", "Decisiones que tomamos")}</h2><ul>
          <li>${T('<b>No AI at question time.</b> We gave up "ask anything" chat so answers are reproducible and can never invent law.', '<b>Sin IA al preguntar.</b> Renunciamos al chat libre para que las respuestas sean reproducibles y nunca inventen leyes.')}</li>
          <li>${T("<b>“Unknown” over guessing.</b> When public records lack a fact (year built, owner type), we say so and name the fact.", "<b>“Desconocido” antes que adivinar.</b> Si falta un dato público (año de construcción, tipo de propietario), lo decimos y lo nombramos.")}</li>
          <li>${T("<b>Word-for-word quotes.</b> Rules whose quote can't be found in the source are dropped, not shown. Rules named in the sources whose text we do not have are shown as unknown, labelled 'text not in corpus'.", "<b>Citas literales.</b> Las normas cuya cita no aparece en la fuente se descartan. Las normas citadas sin texto disponible se muestran como desconocidas.")}</li>
          <li>${T("<b>Runs offline, free.</b> The core is rule-based code with no cloud service; an optional AI extractor can be switched on.", "<b>Funciona sin conexión y gratis.</b> El núcleo es código basado en reglas; un extractor de IA opcional puede activarse.")}</li></ul></div>
        <div class="card box"><h2>${T("What CiteMap never does", "Lo que CiteMap nunca hace")}</h2><ul>
          ${[["Give legal advice or certify compliance.", "Dar asesoría legal o certificar cumplimiento."], ["Suggest ways to avoid or structure around a rule.", "Sugerir formas de evitar una norma."],
            ["Invent rules or citations.", "Inventar normas o citas."], ["Use customer, resident, pricing or other non-public data.", "Usar datos de clientes, inquilinos, precios u otros datos no públicos."],
            ["Report a pending bill or a struck ballot question as law.", "Presentar un proyecto pendiente o una iniciativa anulada como ley."]].map(([a, b]) => `<li>${T(a, b)}</li>`).join("")}</ul></div>
      </div>
      <div class="section"><h2>${T("Audit log (latest events)", "Registro de auditoría")}</h2>
        <div class="log">${log.length ? log.map((l) => esc(JSON.stringify(l))).join("<br>") : T("The audit log is shown when the app runs with its server (it is written to audit/audit.jsonl when the pipeline runs).", "El registro se muestra cuando la app funciona con su servidor.")}</div></div>`;
  }

  async function viewAccount() {
    setNav("");
    if (!S.live) { app.innerHTML = `<div class="card auth"><h2>${T("Accounts need the live server", "Las cuentas requieren el servidor")}</h2><p class="why">${T("Everything else works without signing in.", "Todo lo demás funciona sin iniciar sesión.")}</p></div>`; return; }
    if (S.user) {
      const myHash = location.hash;
      const saved = (await api("api/saved")).data;
      if (location.hash !== myHash) return;
      app.innerHTML = `<div class="card auth"><h2>${T("Hello", "Hola")}, ${esc(S.user.name || S.user.email)}</h2><p class="why">${esc(S.user.email)} · ${esc(S.user.role)}</p>
        <h3 style="margin-top:16px">${T("Saved addresses", "Direcciones guardadas")}</h3>
        ${saved.length ? saved.map((s) => `<div style="margin:8px 0"><a href="#/a/${esc(s.address_id)}">${esc(s.street)}</a> · ${esc(s.city)}, ${esc(s.state)}</div>`).join("") : `<p class="why">${T("Nothing saved yet.", "Nada guardado aún.")}</p>`}
        <button class="btn" id="out" style="margin-top:12px">${T("Sign out", "Cerrar sesión")}</button></div>`;
      $("#out").onclick = async () => { await api("api/auth/logout", { method: "POST" }); S.user = null; renderAcct(); location.hash = "#/"; };
      return;
    }
    app.innerHTML = `<div class="card auth"><h2 id="ttl">${T("Sign in", "Iniciar sesión")}</h2>
      <p class="why">${T("Optional — save addresses and come back to them. You can use everything as a guest.", "Opcional — guarde direcciones. Todo funciona como invitado.")}</p>
      <form id="f"><div id="regOnly" hidden><label>${T("Name", "Nombre")}</label><input name="name">
        <label>${T("I am a", "Soy")}</label><select name="role"><option value="renter">${T("Renter", "Inquilino")}</option><option value="advocate">${T("Advocate / agency", "Defensor / agencia")}</option><option value="provider">${T("Housing provider", "Arrendador")}</option></select></div>
        <label>Email</label><input name="email" type="email" required><label>${T("Password", "Contraseña")}</label><input name="password" type="password" minlength="8" required>
        <div class="err" id="err"></div>
        <div style="display:flex;gap:8px;margin-top:16px"><button class="btn primary" id="go">${T("Sign in", "Entrar")}</button><button type="button" class="btn ghost" id="sw">${T("Create an account", "Crear cuenta")}</button><a class="btn ghost" href="#/">${T("Continue as guest", "Seguir como invitado")}</a></div></form></div>`;
    let reg = false;
    $("#sw").onclick = () => { reg = !reg; $("#regOnly").hidden = !reg; $("#ttl").textContent = reg ? T("Create an account", "Crear cuenta") : T("Sign in", "Iniciar sesión"); $("#go").textContent = reg ? T("Create account", "Crear") : T("Sign in", "Entrar"); $("#sw").textContent = reg ? T("I have an account", "Ya tengo cuenta") : T("Create an account", "Crear cuenta"); };
    $("#f").onsubmit = async (e) => {
      e.preventDefault(); const fd = Object.fromEntries(new FormData(e.target));
      try { const r = await api(reg ? "api/auth/register" : "api/auth/login", { method: "POST", body: JSON.stringify(fd) }); S.user = r.data; renderAcct(); toast(T("Signed in", "Sesión iniciada")); location.hash = "#/account"; viewAccount(); }
      catch (err) { const e2 = $("#err"); if (e2) e2.textContent = err.message; }
    };
  }

  function renderAcct() { $("#acct").textContent = S.user ? (S.user.name || S.user.email).split(" ")[0] : T("Sign in", "Entrar"); }

  async function route() {
    const h = location.hash.replace(/^#\/?/, "");
    if (h.length > 400) { location.hash = "#/"; return; }
    const [p, a, b] = h.split("/");
    window.scrollTo(0, 0);
    try {
      if (p === "a" && a) await viewAddress(decodeURIComponent(a), b);
      else if (p === "changes") await viewChanges();
      else if (p === "rules") await viewRules();
      else if (p === "how") await viewHow();
      else if (p === "account") await viewAccount();
      else await viewHome();
    } catch (e) { app.innerHTML = `<div class="empty">${T("Something went wrong", "Algo salió mal")}: ${esc(e.message)}</div>`; }
  }

  // keyboard: anything acting as a button opens with Enter / Space
  document.addEventListener("keydown", (e) => {
    const t = e.target;
    if ((e.key === "Enter" || e.key === " ") && t && t.getAttribute && t.getAttribute("role") === "button") { e.preventDefault(); t.click(); }
  });
  $("#langBtn").onclick = () => { S.lang = S.lang === "en" ? "es" : "en"; document.documentElement.lang = S.lang; $("#langBtn").textContent = S.lang === "en" ? "EN · ES" : "ES · EN"; renderAcct(); route(); };
  window.addEventListener("hashchange", route);
  document.addEventListener("click", (e) => { if (!e.target.closest(".search")) { const s = $("#sugg"); if (s) s.hidden = true; } });
  (async () => {
    await detectMode();
    if (S.live) { try { S.user = (await api("api/me")).data; } catch {} }
    renderAcct(); route();
  })();
})();
