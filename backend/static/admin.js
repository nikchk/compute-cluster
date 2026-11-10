const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const token = localStorage.token;
const NAMES = {linear_system: "СЛАР", fem_poisson: "МСЕ", mandelbrot: "Мандельброт"};
const meter = v => `<div class="meter ${v > 85 ? "hot" : v > 60 ? "warm" : ""}" title="${v.toFixed(0)}%"><i style="width:${Math.min(100, v)}%"></i></div>`;
const dur = s => s == null ? "—" : s < 120 ? Math.round(s) + " с" : Math.round(s / 60) + " хв";

async function call(path, method = "GET") {
  const r = await fetch("/api" + path, {method, headers: {Authorization: "Bearer " + token}});
  const s = r.headers.get("X-Served-By"); if (s) $("#served").textContent = s;
  if (!r.ok) throw new Error(r.status);
  return r.json();
}
async function load() {
  let d;
  try { d = await call("/admin/overview"); } catch { $("#denied").classList.remove("hidden"); return; }
  $("#denied").classList.add("hidden");
  $("#tick").textContent = new Date().toLocaleTimeString();
  const t = d.totals;
  $("#stats").innerHTML = [["серверів онлайн", t.servers], ["слотів усього", t.slots], ["виконується", t.running],
    ["у черзі", t.queued], ["користувачів", d.users]].map(([l, v]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");
  $("#servers").innerHTML = d.servers.map(s => `<tr>
    <td><span class="dot ${!s.alive ? "off" : s.draining ? "drain" : ""}"></span>${esc(s.id)} ${s.dynamic ? '<span class="chip">авто</span>' : ""}</td>
    <td>${!s.alive ? "не відповідає" : s.draining ? "draining" : "онлайн"}</td>
    <td>${s.running}/${s.capacity}</td><td>${meter(s.cpu)}</td><td>${meter(s.mem)}</td><td>${dur(s.uptime)}</td></tr>`).join("");
  $("#active").innerHTML = d.active.length ? d.active.map(a => `<tr>
    <td>#${a.id}</td><td>${esc(a.username)}</td><td>${NAMES[a.type] || a.type}</td><td>${a.status}</td>
    <td>${a.status === "running" ? `<div class="meter"><i style="width:${a.progress}%"></i></div>${Math.round(a.progress)}% · ${esc(a.stage)}` : "—"}</td>
    <td>${esc(a.server_id || "—")}</td>
    <td><button class="danger sm" data-id="${a.id}">Скасувати</button></td></tr>`).join("")
    : `<tr><td colspan="7" style="color:var(--dim)">Немає активних задач</td></tr>`;
  $("#active").querySelectorAll("button").forEach(b => b.onclick = async () => { try { await call(`/admin/tasks/${b.dataset.id}/cancel`, "POST"); } catch {} load(); });
  $("#recent").innerHTML = d.recent.map(r => `<tr><td>#${r.id}</td><td>${esc(r.username)}</td><td>${NAMES[r.type] || r.type}</td>
    <td>${r.status}${r.error ? ` <span title="${esc(r.error)}" style="color:var(--err)">⚠</span>` : ""}</td><td>${esc(r.server_id || "—")}</td><td>${dur(r.duration)}</td></tr>`).join("");
  $("#events").innerHTML = d.events.map(e => `<div><time>${new Date(e.ts).toLocaleTimeString()}</time>${esc(e.message)}</div>`).join("");
}
load(); setInterval(load, 2000);
