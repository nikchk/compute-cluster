const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let token = localStorage.token || null, limits = null, openTask = null, timer = null;
const NAMES = {linear_system: "СЛАР · метод Гауса", fem_poisson: "МСЕ · рівняння Пуассона", mandelbrot: "Зображення · Мандельброт"};
const STATUS = {queued: "у черзі", running: "виконується", done: "готово", failed: "помилка", cancelled: "скасовано"};

async function api(path, opts = {}) {
  const r = await fetch("/api" + path, {
    method: opts.method || "GET",
    headers: {"Content-Type": "application/json", ...(token ? {Authorization: "Bearer " + token} : {})},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const srv = r.headers.get("X-Served-By"); if (srv) $("#served").textContent = srv;
  if (r.status === 401 && token) { logout(); throw new Error("Сесію завершено"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : (data.detail?.[0]?.msg || "Помилка " + r.status));
  return data;
}

// ---------------- авторизація
async function auth(kind) {
  $("#authMsg").textContent = "";
  try {
    const d = await api("/" + kind, {method: "POST", body: {username: $("#u").value.trim(), password: $("#p").value}});
    token = d.token; localStorage.token = token; localStorage.user = JSON.stringify({u: d.username, a: d.is_admin});
    start();
  } catch (e) { $("#authMsg").textContent = e.message; }
}
function logout() { token = null; localStorage.removeItem("token"); clearInterval(timer); start(); }
$("#btnLogin").onclick = () => auth("login");
$("#btnReg").onclick = () => auth("register");
$("#logout").onclick = logout;

async function start() {
  const logged = !!token;
  $("#auth").classList.toggle("hidden", logged);
  $("#app").classList.toggle("hidden", !logged);
  $("#userbox").classList.toggle("hidden", !logged);
  if (!logged) return;
  const u = JSON.parse(localStorage.user || "{}");
  $("#who").textContent = u.u || "";
  $("#adminLink").classList.toggle("hidden", !u.a);
  limits = await api("/limits");
  buildForm();
  $("#limitsHint").textContent = `Ліміти: ≤${limits.max_active_per_user} активних задач, ≤${limits.max_history_per_user} записів історії, ≤${limits.max_task_seconds} с на задачу.`;
  refresh(); timer = setInterval(refresh, 1000);
}

// ---------------- форма + валідація на клієнті
function buildForm() {
  const sel = $("#type");
  sel.innerHTML = Object.entries(limits.tasks).map(([k, v]) => `<option value="${k}">${esc(v.title)}</option>`).join("");
  sel.onchange = renderFields; renderFields();
}
function renderFields() {
  const spec = limits.tasks[$("#type").value];
  $("#fields").innerHTML = Object.entries(spec.fields).map(([k, f]) =>
    `<label>${esc(f.label)} <span style="opacity:.6">[${f.min}…${f.max}]</span></label>
     <input data-f="${k}" type="number" step="${f.type === "int" ? 1 : "any"}" value="${f.default}">`).join("");
  document.querySelectorAll("#fields input").forEach(i => i.oninput = validate);
  validate();
}
function readParams() {
  const p = {}; document.querySelectorAll("#fields input").forEach(i => p[i.dataset.f] = i.value === "" ? NaN : Number(i.value)); return p;
}
function validate() {
  const spec = limits.tasks[$("#type").value], p = readParams(); let err = "";
  document.querySelectorAll("#fields input").forEach(i => {
    const f = spec.fields[i.dataset.f], v = p[i.dataset.f];
    const bad = isNaN(v) || v < f.min || v > f.max || (f.type === "int" && !Number.isInteger(v));
    i.classList.toggle("bad", bad); if (bad && !err) err = `«${f.label}»: допустимо ${f.min}…${f.max}`;
  });
  let frac = 0;
  const pr = spec.product;
  if (pr) {
    const tot = pr.fields.reduce((a, n) => a * p[n], 1); frac = tot / pr.max;
    $("#costHint").textContent = `${pr.label} = ${isNaN(tot) ? "—" : tot.toLocaleString()} / ${pr.max.toLocaleString()}`;
    if (tot > pr.max && !err) err = "Задача надто трудомістка — зменште розмір або кількість ітерацій";
  } else {
    const f = Object.values(spec.fields)[0]; frac = p[Object.keys(spec.fields)[0]] / f.max;
    $("#costHint").textContent = `навантаження: ${Math.round(Math.min(frac, 9.99) * 100)}% від максимуму`;
  }
  $("#costBar").style.width = Math.min(100, Math.max(0, frac * 100)) + "%";
  $("#costBar").style.background = frac > 1 ? "var(--err)" : frac > .7 ? "var(--accent)" : "var(--ok)";
  $("#runMsg").className = "msg err"; $("#runMsg").textContent = err;
  $("#btnRun").disabled = !!err;
}
$("#btnRun").onclick = async () => {
  try {
    const t = await api("/tasks", {method: "POST", body: {type: $("#type").value, params: readParams()}});
    $("#runMsg").className = "msg ok"; $("#runMsg").textContent = `Задачу #${t.id} прийнято`;
    openTask = null; refresh();
  } catch (e) { $("#runMsg").className = "msg err"; $("#runMsg").textContent = e.message; }
};

// ---------------- список задач
const fmtEta = s => s == null ? "невідомо (немає активних серверів)" : s < 5 ? "кілька секунд" : s < 90 ? `~${Math.round(s)} с` : `~${Math.round(s / 60)} хв`;
const paramsStr = p => Object.entries(p).map(([k, v]) => `${k}=${v}`).join(" ");

async function refresh() {
  let list; try { list = await api("/tasks"); } catch { return; }
  const box = $("#tasks");
  if (!list.length) { box.innerHTML = `<div class="hint">Задач ще немає — запустіть першу.</div>`; return; }
  // не перемальовуємо відкриті результати, оновлюємо лише статус
  box.innerHTML = list.map(t => {
    const active = t.status === "queued" || t.status === "running";
    const q = t.queue;
    return `<div class="task" data-id="${t.id}">
      <div class="top">
        <span class="name">#${t.id} ${esc(NAMES[t.type] || t.type)}</span>
        <span class="badge b-${t.status}">${STATUS[t.status]}</span>
        <span class="spacer"></span>
        ${active ? `<button class="danger sm" data-act="cancel">Скасувати</button>` :
          `<button class="ghost sm" data-act="open">${openTask === t.id ? "Сховати" : "Результат"}</button>
           <button class="ghost sm" data-act="del">✕</button>`}
      </div>
      <div class="meta">${esc(paramsStr(t.params))}${t.server_id ? ` · виконує: ${esc(t.server_id)}` : ""}</div>
      ${active ? `<div class="bar"><i style="width:${t.progress}%"></i></div>
        <div class="meta">${t.progress}% · ${esc(t.stage)}</div>` : ""}
      ${q ? `<div class="queue-note">${q.all_busy ? "Усі ресурси зайняті — запит поставлено в чергу. " : ""}
         Позиція в черзі: ${q.position} · орієнтовне очікування: ${fmtEta(q.eta_seconds)}
         · слотів: ${q.total_slots - q.free_slots}/${q.total_slots} зайнято</div>` : ""}
      ${t.error ? `<div class="meta" style="color:var(--err);margin-top:6px">${esc(t.error)}</div>` : ""}
      <div class="result-slot" id="res-${t.id}"></div>
    </div>`;
  }).join("");
  box.querySelectorAll("[data-act]").forEach(b => b.onclick = async () => {
    const id = Number(b.closest(".task").dataset.id), act = b.dataset.act;
    try {
      if (act === "cancel") await api(`/tasks/${id}/cancel`, {method: "POST"});
      if (act === "del") { await api(`/tasks/${id}`, {method: "DELETE"}); if (openTask === id) openTask = null; }
      if (act === "open") openTask = openTask === id ? null : id;
    } catch (e) { alert(e.message); }
    refresh();
  });
  if (openTask) showResult(openTask);
}

const resCache = {};
async function showResult(id) {
  const slot = $("#res-" + id); if (!slot) return;
  if (!resCache[id]) {
    const t = await api("/tasks/" + id); const r = t.result;
    let html = "Результату немає.", grid = null;
    if (r) {
      if (t.type === "linear_system") html = kv({"n": r.n, "відносна нев'язка": r.relative_residual.toExponential(3), "‖x‖": r.x_norm.toFixed(4),
        "x[0..7]": r.x_head.map(v => v.toFixed(4)).join(", "), "час": r.elapsed_s + " с"});
      if (t.type === "fem_poisson") { grid = r.grid; html = kv({"невідомих": r.unknowns, "елементів": r.elements, "ітерацій CG": r.iterations,
        "max u": r.u_max.toFixed(6), "час": r.elapsed_s + " с"}) + `<canvas width="360" height="360" style="width:360px;margin-top:10px"></canvas>`; }
      if (t.type === "mandelbrot") html = kv({"розмір": `${r.width}×${r.height}`, "час": r.elapsed_s + " с"}) +
        `<img style="margin-top:10px" src="data:image/png;base64,${r.png_base64}" alt="Mandelbrot">`;
    }
    resCache[id] = {html, grid};
  }
  const c = resCache[id];
  slot.innerHTML = `<div class="result">${c.html}</div>`;
  if (c.grid) heat(slot.querySelector("canvas"), c.grid);
}
const kv = o => `<div class="kv">${Object.entries(o).map(([k, v]) => `<span>${esc(k)}</span><span>${esc(v)}</span>`).join("")}</div>`;
function heat(cv, g) {
  const n = g.length, ctx = cv.getContext("2d"), cell = cv.width / n; let mx = 0;
  g.forEach(r => r.forEach(v => mx = Math.max(mx, v)));
  g.forEach((row, j) => row.forEach((v, i) => {
    const t = mx ? v / mx : 0;
    ctx.fillStyle = `hsl(${240 - 240 * t},85%,${25 + 35 * t}%)`;
    ctx.fillRect(i * cell, (n - 1 - j) * cell, cell + 1, cell + 1);
  }));
}
start();
