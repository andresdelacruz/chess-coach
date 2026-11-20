/* Chess Coach UI — vanilla JS, sin dependencias. */
const $ = (s) => document.querySelector(s);
const S = { ctx: null, convs: [], conv: null, busy: false, train: null, sel: null, picker: false, mode: "recomendar", pickerOpts: [] };

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({ error: "respuesta inválida" }));
  if (!r.ok || j.error) throw new Error(j.error || r.statusText);
  return j;
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ───────── markdown mínimo ───────── */
function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
function md(src) {
  const lines = esc(src).split("\n");
  let html = "", i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (l.startsWith("```")) {
      const buf = []; i++;
      while (i < lines.length && !lines[i].startsWith("```")) buf.push(lines[i++]);
      i++; html += `<pre><code>${buf.join("\n")}</code></pre>`; continue;
    }
    if (/^\s*\|/.test(l)) {
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const body = rows.filter((r) => !/^\s*\|[\s:|-]+\|\s*$/.test(r));
      html += "<table>" + body.map((r, k) => "<tr>" + cells(r).map((c) => (k ? `<td>${inline(c)}</td>` : `<th>${inline(c)}</th>`)).join("") + "</tr>").join("") + "</table>";
      continue;
    }
    let m;
    if ((m = l.match(/^(#{1,4})\s+(.*)/))) { html += `<h3>${inline(m[2])}</h3>`; i++; continue; }
    if (/^\s*[-*]\s+/.test(l)) {
      html += "<ul>"; while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) html += `<li>${inline(lines[i++].replace(/^\s*[-*]\s+/, ""))}</li>`; html += "</ul>"; continue;
    }
    if (/^\s*\d+\.\s+/.test(l)) {
      html += "<ol>"; while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) html += `<li>${inline(lines[i++].replace(/^\s*\d+\.\s+/, ""))}</li>`; html += "</ol>"; continue;
    }
    if (!l.trim()) { i++; continue; }
    const p = [];
    while (i < lines.length && lines[i].trim() && !/^(```|\s*\||#{1,4}\s|\s*[-*]\s|\s*\d+\.\s)/.test(lines[i])) p.push(lines[i++]);
    html += `<p>${inline(p.join("<br>"))}</p>`;
  }
  return html;
}

/* ───────── contexto ───────── */
async function loadContext() {
  try { S.ctx = await api("/api/context"); } catch (e) { console.error(e); return; }
  const c = S.ctx;
  if (c.needs_setup) { openSetup(true); return false; }
  const acc = Object.entries(c.accounts).map(([k, v]) => `${v} · ${k === "chesscom" ? "Chess.com" : "Lichess"}`).join(" / ");
  $("#brandSub").textContent = acc;
  $("#meName").textContent = Object.values(c.accounts)[0] || "Tu coach";
  const st = c.styles.find((s) => s.name === c.style);
  $("#meStyle").textContent = "Estilo: " + (st?.title || c.style);
  $("#selStyle").innerHTML = c.styles.map((s) => `<option value="${esc(s.name)}" ${s.name === c.style ? "selected" : ""}>${esc(s.title || s.name)}</option>`).join("");
  const saved = localStorage.getItem("cc_model") || "sonnet";
  $("#selModel").innerHTML = Object.entries(c.models || { sonnet: "Sonnet" }).map(([k, v]) => `<option value="${k}" ${k === saved ? "selected" : ""}>${esc(v)}</option>`).join("");
  $("#pillEngine").textContent = c.claude ? "Claude Code conectado" : "Claude Code no encontrado";
  $("#pillEngine").classList.toggle("bad", !c.claude);
  $("#engineState").textContent = c.claude ? "Claude Code conectado" : "Sin Claude Code (el tablero funciona igual)";
  $("#conns").innerHTML = renderWorker(c.worker) + c.connections.map((x) => `<div class="conn"><div><b>${esc(x.name)}</b>${x.user ? " · " + esc(x.user) : ""}<small>${esc(x.detail)}</small></div><span class="${x.ok ? "ok" : "no"}">${x.ok ? "Verificado" : "Revisar"}</span></div>`).join("");
  $("#pending").textContent = c.pending ? `${c.pending} sin analizar` : (c.total ? `${c.total} en base` : "");
  $("#games").innerHTML = (c.games || []).map((g) => `<div class="game" data-uuid="${esc(g.uuid)}" data-opp="${esc(g.opp)}"><span><span class="res ${g.result}">${{ win: "1", loss: "0", draw: "½" }[g.result]}</span> ${esc(g.opp)} (${g.opp_rating ?? "-"}) · ${esc(g.tc)}</span><span class="muted">${esc(g.date.slice(5))}</span></div>`).join("") || '<p class="muted small">Sin partidas todavía.</p>';
  document.querySelectorAll(".game").forEach((el) => el.onclick = () => {
    $("#input").value = `Revisa mi partida contra ${el.dataset.opp} (${el.dataset.uuid})`; $("#input").focus();
  });
  $("#rep").innerHTML = Object.entries(c.rep_labels).map(([k, label]) => `<div class="rep"><span>${esc(label)}:</span> ${esc((c.repertoire[k] || []).join(", ") || "—")}</div>`).join("");
  $("#skillsN").textContent = `${c.skills.length} cargadas`;
  $("#skills").innerHTML = c.skills.map((s) => `<div class="skill" title="${esc(s.description)}">${esc(s.name.replace(/-/g, " "))}</div>`).join("");
  $("#decisions").innerHTML = c.decisions.map((d) => `<div class="agree">${esc(d)}</div>`).join("");
  $("#next").textContent = c.next_step || "—";
  return true;
}


/* ───────── conversaciones ───────── */
async function loadConvs() {
  S.convs = await api("/api/conversations");
  $("#convList").innerHTML = S.convs.map((c) => `<button class="conv ${S.conv?.id === c.id ? "on" : ""}" data-id="${c.id}">${esc(c.title)}${c.cost ? `<small>≈ US$${c.cost.toFixed(2)}</small>` : ""}</button>`).join("");
  document.querySelectorAll(".conv").forEach((b) => b.onclick = () => openConv(b.dataset.id));
}
async function openConv(id) { S.conv = await api("/api/conversations/" + id); renderMessages(); loadConvs(); }
function newConv() { S.conv = null; S.picker = false; renderMessages(); loadConvs(); $("#input").focus(); }
async function ensureConv() {
  if (!S.conv?.id) { const c = await api("/api/conversations", {}); S.conv = c; }
  return S.conv.id;
}

function quickChips() {
  const rep = S.ctx?.repertoire || {};
  if (dockMode() === "tree") return ["¿Por qué me va mal acá?", "¿Qué plan tiene esta posición?", "Entrenar desde aquí"];
  if (S.train?.active) return ["Pista", "¿Por qué esa jugada?", "¿Cuál es el plan acá?", "Fin"];
  const w = (rep.white || [])[0];
  return [w ? `Entrenemos la ${w}` : "Entrenemos con blancas", "Revisa mi última partida", "Hazme un diagnóstico", "¿Cómo me va con mi repertorio?"];
}
function renderMessages() {
  $("#title").textContent = S.conv?.title || "Nueva conversación";
  const box = $("#messages");
  const msgs = S.conv?.messages || [];
  let html = msgs.map((m, i) => msgHTML(m, i)).join("");
  if (!msgs.length) {
    const n = S.ctx?.total || 0;
    html = n ? `<div class="empty"><h2>♞ ¿Qué entrenamos hoy?</h2><p>Tengo tus ${n.toLocaleString("es-CL")} partidas y tu repertorio. Escribe «entrenemos la catalana» y jugamos jugada a jugada acá mismo, o pregúntame lo que quieras.</p></div>`
      : `<div class="empty"><h2>♞ ¡Hola!</h2><p>Estoy bajando y analizando tus partidas (mira "Conexiones" a la derecha). Mientras, escribe «entrenemos con blancas» y jugamos.</p></div>`;
  }
  if (S.picker) html += pickerHTML();
  box.innerHTML = html;
  bindFeedback(box); bindPicker(box);
  $("#quick").innerHTML = quickChips().map((q) => `<button class="chip">${esc(q)}</button>`).join("");
  document.querySelectorAll(".chip").forEach((b) => b.onclick = () => send(b.textContent));
  box.scrollTop = box.scrollHeight;
}
function msgHTML(m, i) {
  const tr = m.kind === "train" ? " train" : "";
  if (m.role === "user") return `<div class="msg user${tr}"><div class="bubble">${esc(m.text).replace(/\n/g, "<br>")}</div></div>`;
  if (tr) return `<div class="msg bot train"><div class="bubble">${md(m.text)}</div></div>`;
  const fb = m.feedback?.value;
  const cost = m.cost != null ? `≈ US$${Number(m.cost).toFixed(3)} · ${m.secs ?? "?"} s` : "";
  return `<div class="msg bot"><div class="bubble ${m.error ? "err" : ""}">${md(m.text)}</div>
    <div class="fb" data-i="${i}"><button data-v="up" class="${fb === "up" ? "on" : ""}">Me sirvió</button><button data-v="down" class="${fb === "down" ? "on" : ""}">Hay que corregir</button><span class="cost">${cost}</span></div></div>`;
}
function bindFeedback(root) {
  root.querySelectorAll(".fb button").forEach((b) => b.onclick = async () => {
    const i = +b.parentElement.dataset.i; let note = "";
    if (b.dataset.v === "down") { note = prompt("¿Qué hay que corregir? (queda en estado/feedback.md)") ?? ""; }
    await api("/api/feedback", { conv: S.conv.id, index: i, value: b.dataset.v, note });
    S.conv.messages[i].feedback = { value: b.dataset.v }; renderMessages();
  });
}
function pickerHTML() {
  const rep = S.ctx?.repertoire || {}, opts = [];
  (rep.white || []).forEach((o) => opts.push({ label: `${o} · blancas`, color: "white", opening: o }));
  (rep.vs_e4 || []).forEach((o) => opts.push({ label: `${o} · negras vs 1.e4`, color: "black", opening: o, moves: "1.e4" }));
  (rep.vs_d4 || []).forEach((o) => opts.push({ label: `${o} · negras vs 1.d4`, color: "black", opening: o, moves: "1.d4" }));
  opts.push({ label: "Libre con blancas", color: "white" }, { label: "Libre con negras", color: "black" });
  S.pickerOpts = opts;
  return `<div class="msg bot train"><div class="bubble"><p>¿Qué entrenamos? Te recomiendo una jugada a la vez y el rival responde como tu nivel. <b>Gratis</b>.</p>
    <div class="picker">${opts.map((o, k) => `<button data-k="${k}">${esc(o.label)}</button>`).join("")}<button data-k="guia">🧭 Modo guía (juego yo)</button></div></div></div>`;
}
function bindPicker(root) {
  root.querySelectorAll(".picker button").forEach((b) => b.onclick = () => {
    if (b.dataset.k === "guia") { S.mode = S.mode === "guia" ? "recomendar" : "guia"; b.textContent = S.mode === "guia" ? "🧭 Modo guía ✓" : "🧭 Modo guía (juego yo)"; return; }
    const o = S.pickerOpts[+b.dataset.k]; S.picker = false;
    trainCall("start", { color: o.color, opening: o.opening || "", moves: o.moves || "", mode: S.mode || "recomendar" }, `Entrenemos: ${o.label}`);
  });
}

/* ───────── intención: ¿jugada, comando o pregunta? ───────── */
const MOVE_RE = /^(O-O(-O)?|0-0(-0)?|[KQRBN][a-h]?[1-8]?x?[a-h][1-8]|[a-h](x[a-h])?[1-8](=?[QRBN])?|[a-h][1-8][a-h][1-8][qrbn]?)[+#!?]*$/;
function parseStart(t) {
  const m = t.match(/^(entrenemos|entrenar|entreno|practiquemos|practicar|juguemos|jugada a jugada|quiero (entrenar|practicar))\b(.*)$/i);
  if (!m) return null;
  let rest = m[3] || "";
  const mode = /\b(gu[ií]a|d[eé]jame|juego yo)\b/i.test(rest) ? "guia" : (S.mode || "recomendar");
  let color = /\b(negras|black)\b/i.test(rest) ? "black" : /\b(blancas|white)\b/i.test(rest) ? "white" : null;
  const mv = rest.match(/\b1\.\s*\S.*$/); const moves = mv ? mv[0] : "";
  if (mv) rest = rest.slice(0, mv.index);
  const opening = rest.replace(/\b(la|el|los|las|con|mi|de|en|modo|gu[ií]a|d[eé]jame|juego yo|jugada a jugada|blancas|negras|white|black|salida|apertura|defensa)\b/gi, " ").replace(/[.,!?]/g, " ").replace(/\s+/g, " ").trim();
  const rep = S.ctx?.repertoire || {};
  const inList = (k) => (rep[k] || []).some((x) => opening && x.toLowerCase().includes(opening.toLowerCase()));
  if (!color) color = inList("vs_e4") || inList("vs_d4") ? "black" : "white";
  const pretty = opening ? opening.charAt(0).toUpperCase() + opening.slice(1) : "";
  return { color, opening: pretty, moves, mode };
}

async function send(textArg) {
  const text = (textArg ?? $("#input").value).trim();
  if (!text || S.busy) return;
  $("#input").value = "";
  if (S.train?.active) {
    if (/^(fin|terminar|stop|parar)$/i.test(text)) return trainCall("end", {});
    if (/^(pista|sugerencia|sugi[eé]reme|hint|\?)$/i.test(text)) return trainCall("suggest", {});
    if (MOVE_RE.test(text.replace(/\s/g, ""))) return trainCall("turn", { move: text.replace(/\s/g, "").replace(/^0-0-0/, "O-O-O").replace(/^0-0/, "O-O") }, `Jugué ${text}`);
  }
  if (/^(mi )?[aá]rbol( de aperturas)?$/i.test(text)) return openTree();
  if (/^entrenar desde aqu[ií]$/i.test(text) && S.tree?.data) return trainFromHere();
  const st = parseStart(text);
  if (st) {
    if (!st.opening && !st.moves && !(textArg ?? "").length && false) { S.picker = true; return renderMessages(); }
    return trainCall("start", st, text);
  }
  return chat(text);
}

async function chat(text) {
  S.busy = true; $("#btnSend").disabled = true;
  if (!S.conv) S.conv = { id: null, title: text.slice(0, 70), messages: [] };
  S.conv.messages.push({ role: "user", text });
  renderMessages();
  const box = $("#messages");
  const live = document.createElement("div");
  live.className = "msg bot"; live.innerHTML = `<div class="bubble"></div><div class="status">Pensando…</div>`;
  box.appendChild(live); box.scrollTop = box.scrollHeight;
  let acc = "";
  try {
    const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conv: S.conv.id, text, model: $("#selModel").value, ctx: dockMode() === "tree" ? S.tree.data?.chat_ctx : "" }) });
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let k;
      while ((k = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, k); buf = buf.slice(k + 2);
        if (!chunk.startsWith("data: ")) continue;
        const ev = JSON.parse(chunk.slice(6));
        if (ev.type === "conv") { S.conv.id = ev.conv; S.conv.title = ev.title; $("#title").textContent = ev.title; }
        if (ev.type === "status") live.querySelector(".status").textContent = ev.text;
        if (ev.type === "text") { acc += (acc ? "\n\n" : "") + ev.text; live.querySelector(".bubble").innerHTML = md(acc); }
        if (ev.type === "done") live.querySelector(".status")?.remove();
        box.scrollTop = box.scrollHeight;
      }
    }
  } catch (e) { live.querySelector(".bubble").innerHTML = `<p class="err">Error: ${esc(e.message)}</p>`; }
  S.busy = false; $("#btnSend").disabled = false;
  if (S.conv.id) await openConv(S.conv.id);
  await refreshTrain(); loadContext();
}

/* ───────── jugada a jugada (dentro del chat, sin LLM) ───────── */
async function trainCall(action, body, userEcho) {
  if (S.busy) return;
  S.busy = true; $("#btnSend").disabled = true; S.picker = false;
  if (action === "start") S.dock = "train";
  body.conv = await ensureConv();
  if (userEcho && action !== "turn") S.conv.messages.push({ role: "user", text: userEcho, kind: "train" });
  if (action === "turn") S.conv.messages.push({ role: "user", text: userEcho || `Jugué ${body.move}`, kind: "train" });
  renderMessages();
  const box = $("#messages"); const live = document.createElement("div");
  live.className = "msg bot train"; live.innerHTML = `<div class="status">${{ start: "Armando el tablero…", turn: "Stockfish mira tu jugada y el rival piensa…", suggest: "Buscando la mejor para tu nivel…", end: "Guardando el informe…" }[action] || "…"}</div>`;
  box.appendChild(live); box.scrollTop = box.scrollHeight;
  $("#boardFoot").textContent = "…";
  let r;
  try { r = await api("/api/train/" + action, body); } catch (e) { r = { ok: false, text: e.message }; }
  S.busy = false; $("#btnSend").disabled = false; S.sel = null;
  if (r.state) S.train = r.state;
  if (!r.ok) { live.innerHTML = `<div class="bubble err">${esc(r.text)}</div>`; S.conv.messages.pop(); renderDock(); return; }
  await openConv(S.conv.id); renderDock(); loadContext();
}
async function refreshTrain() {
  S.train = await api("/api/train").catch(() => ({ active: false }));
  if (S.train.active && S.dock === undefined) S.dock = "train";
  renderDock();
}

const GLYPH = { k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" };
function parseFen(fen) {
  const rows = fen.split(" ")[0].split("/"); const b = {};
  rows.forEach((row, ri) => { let f = 0; for (const ch of row) { if (/\d/.test(ch)) f += +ch; else { b["abcdefgh"[f] + (8 - ri)] = ch; f++; } } });
  return b;
}
/* tablero genérico: st = {fen, color, legal[uci], last(uci), check} */
function drawBoard(st) {
  const el = $("#board"); const pos = parseFen(st.fen); const flip = st.color === "black";
  const files = flip ? "hgfedcba" : "abcdefgh"; const ranks = flip ? [1, 2, 3, 4, 5, 6, 7, 8] : [8, 7, 6, 5, 4, 3, 2, 1];
  const turnWhite = st.fen.split(" ")[1] === "w";
  const lastSq = st.last ? [st.last.slice(0, 2), st.last.slice(2, 4)] : [];
  const legal = st.legal || [];
  const targets = S.sel ? legal.filter((u) => u.startsWith(S.sel)).map((u) => u.slice(2, 4)) : [];
  const movable = new Set(legal.map((u) => u.slice(0, 2)));
  let html = "";
  ranks.forEach((r, ri) => [...files].forEach((f, fi) => {
    const sq = f + r; const p = pos[sq]; const light = ("abcdefgh".indexOf(f) + r) % 2 === 1;
    const chk = st.check && p && p.toLowerCase() === "k" && ((p === "K") === turnWhite);
    const cls = ["sq", light ? "l" : "d", lastSq.includes(sq) ? "last" : "", S.sel === sq ? "sel" : "", targets.includes(sq) ? "tgt" + (p ? " cap" : "") : "", movable.has(sq) ? "mov" : "", chk ? "chk" : ""].join(" ");
    html += `<div class="${cls}" data-sq="${sq}">${p ? `<span class="pc ${p === p.toUpperCase() ? "w" : "b"}">${GLYPH[p.toLowerCase()]}</span>` : ""}` +
      (fi === 7 ? `<span class="coord r">${r}</span>` : "") + (ri === 7 ? `<span class="coord f">${f}</span>` : "") + "</div>";
  }));
  el.innerHTML = html;
  el.querySelectorAll(".sq").forEach((d) => d.onclick = () => clickSq(d.dataset.sq));
}
function dockMode() {
  if (S.dock === "tree") return "tree";
  if (S.dock === "train" && S.train?.active) return "train";
  return null;
}
function renderDock() {
  const mode = dockMode();
  $("#dock").classList.toggle("hidden", !mode);
  $("#btnTrain").textContent = S.train?.active ? "♟ Entrenando (activa)" : "♟ Entrenar jugada a jugada";
  $("#tabTrain").classList.toggle("on", mode === "train"); $("#tabTree").classList.toggle("on", mode === "tree");
  ["treeBar", "treeActions", "treePanel"].forEach((id) => $("#" + id).classList.toggle("hidden", mode !== "tree"));
  $("#trainActions").classList.toggle("hidden", mode !== "train");
  $("#quick").innerHTML = quickChips().map((q) => `<button class="chip">${esc(q)}</button>`).join("");
  document.querySelectorAll(".chip").forEach((b) => b.onclick = () => send(b.textContent));
  if (mode === "train") {
    const t = S.train;
    drawBoard({ fen: t.fen, color: t.color, legal: t.legal, last: t.last?.uci, check: t.check });
    $("#boardHead").textContent = (t.opening ? t.opening + " · " : "") + (t.color === "white" ? "blancas" : "negras") + " · " + (t.mode === "guia" ? "modo guía" : "te recomiendo cada jugada");
    $("#boardFoot").textContent = t.game_over ? `Fin: ${t.result}` : t.my_turn ? (t.suggestion ? `Tu turno · sugerencia: ${t.suggestion.san}` : "Tu turno") : "Turno del rival…";
    $("#trainMoves").textContent = t.pgn || "";
  } else if (mode === "tree") renderTree();
}

/* ───────── 🌳 árbol de aperturas (estilo OpeningTree + Stockfish) ───────── */
function treeQS() {
  const t = S.tree;
  return `moves=${t.moves.join(",")}&color=${t.color}&tc=${t.tc}&days=${t.days}`;
}
async function openTree() {
  S.dock = "tree"; S.sel = null;
  if (!S.tree) S.tree = { moves: [], color: "white", tc: "", days: "", data: null };
  await fetchTree();
}
async function fetchTree() {
  $("#boardFoot").textContent = "Cargando…";
  try { S.tree.data = await api("/api/tree?" + treeQS()); } catch (e) { $("#treePanel").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  S.sel = null; renderDock();
}
function treeMove(uci) { S.tree.moves.push(uci); fetchTree(); }
function renderTree() {
  const d = S.tree.data; if (!d) return;
  document.querySelectorAll("#tColor button").forEach((b) => b.classList.toggle("on", b.dataset.v === S.tree.color));
  drawBoard({ fen: d.fen, color: S.tree.color, legal: d.legal, last: d.last, check: d.check });
  $("#boardHead").textContent = (d.opening || "Tu árbol") + ` · ${d.reached.toLocaleString("es-CL")} partidas llegaron acá`;
  const ev = d.engine || {};
  $("#boardFoot").textContent = (d.my_turn ? "Te toca a ti" : "Le toca al rival") + (ev.best ? ` · Stockfish: ${ev.best} (${ev.eval})` : "");
  $("#trainMoves").textContent = d.pgn || "(posición inicial)";
  const dropCls = (x) => x == null ? "" : x >= 10 ? "hi" : x >= 5 ? "mid" : "lo";
  const rows = d.rows.map((r) => {
    const pw = (100 * r.w / r.n), pd = (100 * r.d / r.n), pl = (100 * r.l / r.n);
    return `<tr class="mv ${r.flag ? "flag" : ""}" data-uci="${r.uci}"><td class="san">${esc(r.san)}</td><td>${r.n}</td>
      <td><div class="wdl" title="${r.w} ganadas · ${r.d} tablas · ${r.l} perdidas"><span class="w" style="width:${pw}%">${pw >= 14 ? Math.round(pw) + "%" : ""}</span><span class="d" style="width:${pd}%">${pd >= 14 ? Math.round(pd) + "%" : ""}</span><span class="l" style="width:${pl}%">${pl >= 14 ? Math.round(pl) + "%" : ""}</span></div></td>
      <td class="drop ${dropCls(r.drop)}" title="${r.analyzed} analizadas">${r.drop == null ? "—" : "−" + r.drop + "%"}</td><td class="muted">${esc((r.last || "").slice(2))}</td></tr>`;
  }).join("");
  const lich = d.lichess?.length ? `<div class="sub">Tu nivel en Lichess</div><div class="tree-info">${d.lichess.slice(0, 5).map((m) => `<b>${esc(m.san)}</b> ${m.pct}% (${m.score}%)`).join(" · ")}</div>` : "";
  const games = d.games.length ? `<div class="sub">Partidas que pasaron por acá</div>` + d.games.map((g) => `<div class="tg"><span>${{ win: "✅", loss: "❌", draw: "½" }[g.my_result]} ${esc(g.opp_name)} (${g.opp_rating ?? "-"}) · ${esc(g.time_class)}</span><span>${esc(g.date)} ${g.url ? `<a href="${esc(g.url)}" target="_blank" rel="noopener">ver</a>` : ""}</span></div>`).join("") : "";
  const note = d.unindexed ? `<p class="tree-info">⏳ Indexando ${d.unindexed} partidas más…</p>` : "";
  $("#treePanel").innerHTML = (d.rows.length
    ? `<table class="tt"><tr><th>Jugada</th><th>Partidas</th><th>Ganadas · tablas · perdidas</th><th title="Caída media de tu prob. de ganar según Stockfish">Te cuesta</th><th>Última</th></tr>${rows}</table>`
    : `<p class="tree-info">No tienes partidas en esta posición${S.tree.tc || S.tree.days ? " con estos filtros" : ""}.</p>`) +
    (d.best_mine ? `<p class="tree-info">En tus análisis Stockfish suele preferir <b>${esc(d.best_mine)}</b> acá.</p>` : "") + lich + games + note;
  $("#treePanel").querySelectorAll("tr.mv").forEach((tr) => tr.onclick = () => treeMove(tr.dataset.uci));
}
function trainFromHere() {
  const d = S.tree?.data; if (!d) return;
  S.dock = "train";
  trainCall("start", { color: S.tree.color, opening: d.opening ? d.opening.split(":")[0] : "", moves: d.pgn, mode: S.mode || "recomendar" }, `Entrenemos desde: ${d.pgn || "el inicio"}`);
}
function clickSq(sq) {
  const mode = dockMode();
  const st = mode === "train" ? S.train : mode === "tree" ? S.tree.data : null;
  if (!st || S.busy) return;
  if (mode === "train" && !st.my_turn) return;
  if (S.sel) {
    let mv = st.legal.find((u) => u.startsWith(S.sel + sq));
    if (mv) {
      if (st.legal.includes(S.sel + sq + "q")) mv = S.sel + sq + "q";
      S.sel = null;
      return mode === "train" ? trainCall("turn", { move: mv }) : treeMove(mv);
    }
  }
  S.sel = st.legal.some((u) => u.startsWith(sq)) ? sq : null; renderDock();
}

/* ───────── onboarding / configuración ───────── */
let SETUP = null;
async function openSetup(firstTime) {
  SETUP = await api("/api/setup");
  const s = SETUP;
  $("#setupTitle").textContent = firstTime ? "Bienvenido a Chess Coach" : "Configuración";
  $("#btnSetupCancel").classList.toggle("hidden", !!firstTime);
  $("#sChesscom").value = s.accounts.chesscom || ""; $("#sLichess").value = s.accounts.lichess || ""; $("#sEmail").value = s.email || "";
  $("#sToken").value = ""; $("#sToken").placeholder = s.token_set ? "token guardado ✓ (déjalo vacío para no cambiarlo)" : "lip_… (opcional)";
  $("#sStyles").innerHTML = s.styles.map((st) => `<button class="style-opt ${st.name === s.style ? "on" : ""}" data-v="${esc(st.name)}"><b>${esc(st.title || st.name)}</b><span>${esc(st.description || "")}</span></button>`).join("");
  document.querySelectorAll(".style-opt").forEach((b) => b.onclick = () => { document.querySelectorAll(".style-opt").forEach((x) => x.classList.remove("on")); b.classList.add("on"); });
  $("#sRep").innerHTML = Object.entries(s.rep_labels).map(([k, l]) => `<label>${esc(l)}<input data-k="${k}" value="${esc((s.repertoire[k] || []).join(", "))}" placeholder="${{ white: "ej. Catalana", vs_e4: "ej. Caro-Kann", vs_d4: "ej. Nimzo-India", vs_otros: "" }[k]}"></label>`).join("");
  renderClaudeCheck(s.checks);
  $("#setupErr").textContent = ""; $("#setupProgress").classList.add("hidden"); $("#btnSetupSave").disabled = false;
  $("#setup").classList.remove("hidden");
}
function renderClaudeCheck(ch) {
  const box = $("#sClaude");
  if (!ch.claude) {
    box.innerHTML = `<div class="claude-box"><span class="claude-no">✗ No encuentro Claude Code.</span> Instálalo en la Terminal con:<br><code>curl -fsSL https://claude.ai/install.sh | bash</code><br>después escribe <code>claude</code> e inicia sesión con tu cuenta. El tablero funciona igual sin esto.</div>`;
    return;
  }
  box.innerHTML = `<div class="claude-box"><span class="claude-ok">✓ Claude Code instalado</span> <span class="muted">(${esc(ch.claude)})</span> · <button class="mini" id="btnClaudeTest">Probar conexión</button> <span id="claudeTestOut" class="muted"></span>
    <div class="muted small">Si nunca lo has usado: en la Terminal escribe <code>claude</code>, dentro <code>/login</code> y entra con tu cuenta.</div></div>`;
  $("#btnClaudeTest").onclick = async (e) => {
    e.preventDefault(); $("#claudeTestOut").textContent = "probando…";
    try { const r = await api("/api/claude-test", {}); $("#claudeTestOut").innerHTML = r.ok ? '<span class="claude-ok">✓ responde</span>' : `<span class="err">${esc(r.error)}</span>`; }
    catch (err) { $("#claudeTestOut").innerHTML = `<span class="err">${esc(err.message)}</span>`; }
  };
}
async function saveSetup() {
  const rep = {}; document.querySelectorAll("#sRep input").forEach((i) => rep[i.dataset.k] = i.value);
  const body = { chesscom: $("#sChesscom").value, lichess: $("#sLichess").value, email: $("#sEmail").value,
    style: document.querySelector(".style-opt.on")?.dataset.v || "profe", repertoire: rep };
  if ($("#sToken").value.trim()) body.lichess_token = $("#sToken").value.trim();
  $("#btnSetupSave").disabled = true; $("#setupErr").textContent = "Verificando tus cuentas…";
  let r;
  try { r = await api("/api/setup", body); } catch (e) { r = { ok: false, error: e.message }; }
  if (!r.ok) { $("#setupErr").textContent = r.error; $("#btnSetupSave").disabled = false; return; }
  $("#setupErr").textContent = "";
  if (!r.first) { $("#setup").classList.add("hidden"); return boot(); }
  $("#setupProgress").classList.remove("hidden");
  for (let i = 0; i < 600; i++) {   // espera a que baje las partidas (las cuentas grandes tardan 1–2 min)
    const st = await api("/api/status").catch(() => ({}));
    $("#setupProgressText").textContent = st.msg || "Preparando…";
    if (st.last_sync) break;
    await new Promise((res) => setTimeout(res, 1500));
  }
  $("#setup").classList.add("hidden"); boot();
}
function renderWorker(w) {
  if (!w) return "";
  if (w.phase === "idle") return w.last_error ? `<div class="worker" style="color:var(--bad)">⚠ ${esc(w.last_error.slice(0, 120))}</div>` : "";
  return `<div class="worker"><div class="spinner"></div>${esc(w.msg)}</div>`;
}
$("#btnGear").onclick = () => openSetup(false);
$("#btnSetupCancel").onclick = () => $("#setup").classList.add("hidden");
$("#btnSetupSave").onclick = saveSetup;


function repDialog() {
  const c = S.ctx; const d = $("#dlg");
  $("#dlgForm").innerHTML = `<h3>Repertorio declarado</h3><p class="muted small">Separa con comas. Cada cambio queda en <code>estado/decisiones.md</code>.</p>` +
    Object.entries(c.rep_labels).map(([k, l]) => `<div class="row"><label>${esc(l)}</label><input name="${k}" value="${esc((c.repertoire[k] || []).join(", "))}"></div>`).join("") +
    `<div class="row"><label>Motivo (opcional)</label><input name="why" placeholder="ej. la estoy estudiando"></div><menu><button value="cancel">Cancelar</button><button value="ok" class="primary">Guardar</button></menu>`;
  d.showModal();
  d.onclose = async () => {
    if (d.returnValue !== "ok") return;
    const f = new FormData($("#dlgForm")); const why = f.get("why");
    for (const k of Object.keys(c.rep_labels)) {
      const v = (f.get(k) || "").trim(); if (v !== (c.repertoire[k] || []).join(", ")) await api("/api/repertoire", { key: k, value: v, why });
    }
    loadContext();
  };
}


/* ───────── eventos ───────── */
$("#btnNew").onclick = newConv;
$("#btnTrain").onclick = () => { if (S.train?.active) { S.dock = "train"; renderDock(); $("#input").focus(); return; } S.conv = null; S.picker = true; renderMessages(); loadConvs(); };
$("#btnSend").onclick = () => send();
$("#input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
$("#selStyle").onchange = async (e) => { await api("/api/style", { name: e.target.value, why: "elegido en la UI" }); loadContext(); };
$("#selModel").onchange = (e) => localStorage.setItem("cc_model", e.target.value);
$("#btnSync").onclick = async (e) => { e.target.textContent = "Sincronizando…"; try { await api("/api/sync", {}); } catch (err) { alert(err.message); } e.target.textContent = "Actualizar"; loadContext(); };
$("#btnRepEdit").onclick = repDialog;
$("#btnHint").onclick = () => send("Pista");
$("#btnTree").onclick = openTree;
$("#tabTree").onclick = openTree;
$("#tabTrain").onclick = () => { if (S.train?.active) { S.dock = "train"; renderDock(); } else { S.dock = null; S.conv = null; S.picker = true; renderMessages(); renderDock(); } };
$("#dockClose").onclick = () => { S.dock = null; renderDock(); };
$("#tBack").onclick = () => { if (S.tree.moves.length) { S.tree.moves.pop(); fetchTree(); } };
$("#tRoot").onclick = () => { S.tree.moves = []; fetchTree(); };
$("#tTrain").onclick = trainFromHere;
document.querySelectorAll("#tColor button").forEach((b) => b.onclick = () => { S.tree.color = b.dataset.v; S.tree.moves = []; fetchTree(); });
$("#tTc").onchange = (e) => { S.tree.tc = e.target.value; fetchTree(); };
$("#tDays").onchange = (e) => { S.tree.days = e.target.value; fetchTree(); };
document.addEventListener("keydown", (e) => { if (dockMode() === "tree" && document.activeElement !== $("#input") && e.key === "ArrowLeft") $("#tBack").click(); });
$("#btnEnd").onclick = () => { if (confirm("¿Terminar? Guardo el informe en entregables/ y en la bitácora.")) send("Fin"); };

async function boot() {
  const ready = await loadContext(); if (ready === false) return;
  await loadConvs(); renderMessages(); await refreshTrain();
}
boot();
setInterval(() => { if (!S.busy && $("#setup").classList.contains("hidden")) loadContext(); }, 20000);
