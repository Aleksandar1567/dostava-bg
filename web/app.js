// Dostava BG - web client (same origin as the FastAPI backend).
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hm = (iso) => (iso ? iso.slice(11, 16) : "");

let S = null;               // last /api/state
let geomVersion = null;
let pinFor = null;          // package id or "depot" waiting for a map click
let busy = false;
let autoTimer = null;
let settingsDirty = false;

async function api(path, opts = {}) {
  const r = await fetch(path, {
    method: opts.method || (opts.body ? "POST" : "GET"),
    headers: { "Content-Type": "application/json" },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}

async function act(fn, okMsg) {
  try {
    const out = await fn();
    if (okMsg) toast(typeof okMsg === "function" ? okMsg(out) : okMsg);
    await refresh();
    return out;
  } catch (e) {
    toast("Greška: " + e.message, 4000);
  }
}

// ------------------------------------------------------------------ map
const map = L.map("map").setView([44.815, 20.40], 12);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "© OpenStreetMap contributors",
}).addTo(map);
const layers = {
  route: L.layerGroup().addTo(map),
  stops: L.layerGroup().addTo(map),
  courier: L.layerGroup().addTo(map),
};
let fitted = false;

const icon = (cls, text = "") =>
  L.divIcon({ className: "", html: `<div class="mk ${cls}">${esc(text)}</div>`, iconSize: [26, 26], iconAnchor: [13, 13] });

function stopPopup(st) {
  const pk = st.packages.map((p) => `${esc(p.id)} ${esc(p.name)}`).join("<br>");
  const win = st.window ? `<br>Prozor kupca: ${esc(st.window.from || "…")}–${esc(st.window.to || "…")}` : "";
  return `<b>${st.seq ? st.seq + ". " : ""}${esc(st.label)}</b><br>ETA ${hm(st.eta)}${win}` +
    `${st.late_min ? `<br><b style="color:#c62828">kasni ${st.late_min} min</b>` : ""}<br>${pk}` +
    `<br><a href="${gmNav(st.lat, st.lon)}" target="_blank">Navigiraj (Google Maps)</a>`;
}

function renderMap() {
  layers.stops.clearLayers();
  layers.courier.clearLayers();
  const d = S.settings.depot;
  L.marker([d.lat, d.lon], { icon: icon("depot", "M"), title: d.label }).bindPopup(esc(d.label)).addTo(layers.stops);
  const pts = [[d.lat, d.lon]];
  for (const st of S.done) {
    L.marker([st.lat, st.lon], { icon: icon(st.status === "done" ? "done" : "failed") })
      .bindPopup(`${esc(st.label)}<br>${st.status === "done" ? "Isporučeno" : "Neuspešno"} ${hm(st.done_at)}`)
      .addTo(layers.stops);
  }
  S.route.forEach((st, i) => {
    const cls = [i === 0 ? "next" : "", st.late_min ? "late" : "", st.window ? "win" : ""].join(" ");
    L.marker([st.lat, st.lon], { icon: icon(cls, st.seq), zIndexOffset: i === 0 ? 1000 : 0 })
      .bindPopup(stopPopup(st)).addTo(layers.stops);
    pts.push([st.lat, st.lon]);
  });
  for (const st of S.unplanned) {
    L.marker([st.lat, st.lon], { icon: icon("unplanned", "?") }).bindPopup(stopPopup(st)).addTo(layers.stops);
    pts.push([st.lat, st.lon]);
  }
  if (!S.route.length) {
    for (const p of S.packages) {
      if (p.lat != null && p.status === "pending" && !p.stop) {
        L.marker([p.lat, p.lon], { icon: icon("unplanned", "") }).bindPopup(esc(p.id + " " + p.address)).addTo(layers.stops);
        pts.push([p.lat, p.lon]);
      }
    }
  }
  const c = S.courier;
  if (c.lat != null && (c.state !== "depot" || S.clock === "sim")) {
    L.marker([c.lat, c.lon], { icon: icon("courier", "🚚"), zIndexOffset: 2000 }).addTo(layers.courier);
  }
  if (!fitted && pts.length > 1) {
    map.fitBounds(pts, { padding: [30, 30] });
    fitted = true;
  }
}

async function renderGeometry() {
  if (S.geom_version === geomVersion) return;
  geomVersion = S.geom_version;
  try {
    const g = await api("/api/geometry");
    layers.route.clearLayers();
    for (const line of g.rest || []) {
      L.polyline(line, { color: "#0b6bcb", weight: 3, opacity: 0.5 }).addTo(layers.route);
    }
    if (g.current && g.current.length) {
      L.polyline(g.current, { color: "#6f42c1", weight: 6, opacity: 0.85 }).addTo(layers.route);
    }
  } catch (e) {
    geomVersion = null;
  }
}

map.on("click", async (ev) => {
  if (!pinFor) return;
  const { lat, lng } = ev.latlng;
  const target = pinFor;
  pinFor = null;
  $("#pinBar").classList.add("hidden");
  if (target === "depot") {
    await act(() => api("/api/settings", { method: "PUT", body: { depot: { lat, lon: lng, label: "Magacin" } } }),
      "Magacin postavljen");
  } else {
    await act(() => api(`/api/packages/${encodeURIComponent(target)}/location`, { method: "PUT", body: { lat, lon: lng } }),
      `Lokacija za ${target} postavljena`);
  }
});

// ------------------------------------------------------------------ helpers
const gmNav = (lat, lon) =>
  `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}&travelmode=driving`;

function gmNext10() {
  const stops = S.route.slice(0, 10);
  if (!stops.length) return null;
  const dest = stops[stops.length - 1];
  const wps = stops.slice(0, -1).map((s) => `${s.lat},${s.lon}`).join("|");
  const c = S.courier;
  return `https://www.google.com/maps/dir/?api=1&origin=${c.lat},${c.lon}` +
    `&destination=${dest.lat},${dest.lon}&waypoints=${encodeURIComponent(wps)}&travelmode=driving`;
}

function call(phone) {
  if (!phone || /^TEST/i.test(phone)) {
    toast("Test broj: poziv je namerno onemogućen");
    return;
  }
  location.href = "tel:" + phone.replace(/\s+/g, "");
}

const confText = { ok: "ok", approx: "proveri", none: "nema lokacije", manual: "ručno" };

// ------------------------------------------------------------------ render
function renderHeader() {
  $("#clock").textContent = hm(S.now);
  const b = $("#clockBadge");
  b.textContent = S.clock === "sim" ? "SIMULACIJA" : "stvarno vreme";
  b.classList.toggle("sim", S.clock === "sim");
  const sm = S.plan && S.plan.summary;
  const t = S.traffic;
  const chips = [];
  if (sm) {
    chips.push(`<span class="chip">Stanica <b>${sm.stops}</b></span>`);
    chips.push(`<span class="chip">Paketa <b>${sm.packages}</b></span>`);
    chips.push(`<span class="chip">Kraj <b>${hm(sm.end)}</b></span>`);
    chips.push(`<span class="chip">Vožnja <b>${Math.round(sm.drive_min / 6) / 10} h</b> · čekanje kupaca <b>${Math.round(sm.service_min / 6) / 10} h</b></span>`);
    if (sm.late_stops) chips.push(`<span class="chip bad">Kasni <b>${sm.late_stops}</b></span>`);
  }
  chips.push(`<span class="chip ${t.factor_now > 1.3 ? "warn" : ""}" title="procena gužve po dobu dana">Gužva ×<b>${t.factor_now.toFixed(2)}</b></span>`);
  if (t.live_ratio && Math.abs(t.live_ratio - 1) > 0.15) {
    chips.push(`<span class="chip ${t.live_ratio > 1 ? "bad" : ""}" title="koliko je kurir sporiji od plana na trenutnoj deonici">Uživo ×<b>${t.live_ratio.toFixed(2)}</b></span>`);
  }
  if (t.source === "procena") chips.push(`<span class="chip bad">OSRM nedostupan: procena</span>`);
  $("#chips").innerHTML = chips.join("");
  $("#pkgCount").textContent = S.packages.length || "";
  $("#noteCount").textContent = S.notifications.length || "";
}

function pkgLines(st) {
  return st.packages.map((p) =>
    `<div class="row gap" style="justify-content:space-between"><span>${esc(p.id)} · ${esc(p.name)}` +
    ` <a href="/t/${esc(p.track)}" target="_blank" class="hint">kupac ↗</a></span>` +
    `<button class="small" data-act="call" data-phone="${esc(p.phone)}">Pozovi</button></div>`).join("");
}

function renderRoute() {
  const r = S.route;
  const c = S.courier;
  if (!S.plan) {
    $("#nextCard").innerHTML = `<div class="card">Još nema rute. Dodaj pakete (tab <b>Paketi</b>) pa klikni <b>Optimizuj rutu</b>. Najbrže: Paketi → Učitaj test skup → Simulacija → Pokreni.</div>`;
  } else if (!r.length) {
    $("#nextCard").innerHTML = `<div class="card">Sve stanice su obiđene. 🎉</div>`;
  } else {
    const st = r[0];
    const state = c.state === "driving" && c.target === st.id ? "Vozi se ka" :
      c.state === "serving" && c.target === st.id ? "Na adresi" : "Sledeća stanica";
    const win = st.window ? `<span class="tag win">prozor ${esc(st.window.from || "…")}–${esc(st.window.to || "…")}</span>` : "";
    const late = st.late_min ? `<span class="tag late">kasni ${st.late_min} min</span>` : "";
    const retry = st.attempts ? `<span class="tag">pokušaj ${st.attempts + 1}</span>` : "";
    $("#nextCard").innerHTML = `<div class="next">
      <div class="k">${state} · stanica ${S.done.length + 1} od ${r.length + S.done.length}</div>
      <div class="addr">${esc(st.label)}</div>
      <div class="row gap"><span class="eta">${hm(st.eta)}</span>
        <span class="hint">dolazak ${hm(st.arrival)}${st.travel_min != null ? ` · vožnja ${st.travel_min} min` : ""}${st.wait_min ? ` · čeka prozor ${st.wait_min} min` : ""}</span></div>
      <div>${win}${late}${retry}</div>
      <div style="margin-top:8px">${pkgLines(st)}</div>
      <div class="actions">
        <a class="btn" href="${gmNav(st.lat, st.lon)}" target="_blank">Navigiraj</a>
        <button data-act="arrived" data-id="${st.id}" ${c.state === "serving" && c.target === st.id ? "disabled" : ""}>Stigao</button>
        <button class="primary" data-act="delivered" data-id="${st.id}">Isporučeno</button>
        <button data-act="failed" data-id="${st.id}">Nije se javio</button>
      </div></div>`;
  }
  $("#routeList").innerHTML = r.slice(1).map((st) => {
    const win = st.window ? `<span class="tag win">${esc(st.window.from || "…")}–${esc(st.window.to || "…")}</span>` : "";
    const late = st.late_min ? `<span class="tag late">+${st.late_min}</span>` : "";
    const n = st.packages.length > 1 ? `<span class="tag">${st.packages.length} pak.</span>` : "";
    return `<div class="stop ${st.late_min ? "late" : ""}" data-act="focus" data-lat="${st.lat}" data-lon="${st.lon}">
      <div class="seq">${st.seq}</div>
      <div><div>${esc(st.label)}${n}${win}${late}</div>
        <div class="sub">${st.packages.map((p) => esc(p.name)).join(", ")}${st.wait_min ? ` · čeka ${st.wait_min} min` : ""}</div></div>
      <div class="t">${hm(st.eta)}</div></div>`;
  }).join("");
  $("#doneCount").textContent = S.done.length ? `(${S.done.length})` : "";
  $("#doneList").innerHTML = S.done.slice().reverse().map((st) =>
    `<div class="stop ${st.status}" data-act="focus" data-lat="${st.lat}" data-lon="${st.lon}">
      <div class="seq">${st.status === "done" ? "✓" : "✕"}</div>
      <div><div>${esc(st.label)}</div><div class="sub">${st.packages.map((p) => esc(p.name)).join(", ")}</div></div>
      <div class="t">${hm(st.done_at)}</div></div>`).join("");
  const t = S.traffic;
  $("#trafficNote").innerHTML = S.plan
    ? `Vremena: OSRM (putna mreža, bez saobraćaja) × procena gužve po dobu dana (sada ×${t.factor_now.toFixed(2)})` +
      `${t.live_ratio && t.live_ratio !== 1 ? ` × izmerena brzina kurira (×${t.live_ratio.toFixed(2)}, slabi s vremenom)` : ""}. ` +
      `Rešavač: ${S.plan.solve_ms} ms, razlog: ${esc(S.plan.reason)}.`
    : "";
}

function renderPackages() {
  const order = { none: 0, approx: 1, manual: 2, ok: 3 };
  const list = S.packages.slice().sort((a, b) =>
    (order[a.confidence] - order[b.confidence]) || a.id.localeCompare(b.id));
  $("#pkgList").innerHTML = list.map((p) => {
    const win = p.window ? `<span class="tag win">${esc(p.window.from || "…")}–${esc(p.window.to || "…")}</span>` : "";
    const st = p.status !== "pending" ? `<span class="tag ${p.status === "delivered" ? "ok" : "none"}">${p.status === "delivered" ? "isporučeno" : "neuspešno"}</span>` : "";
    const promise = p.promise ? ` · obećano ${hm(p.promise.from)}–${hm(p.promise.to)}` : "";
    return `<div class="pkg">
      <div class="top2"><span><b>${esc(p.id)}</b> ${esc(p.name)}</span>
        <span><span class="tag ${p.confidence}">${confText[p.confidence] || p.confidence}</span>${win}${st}</span></div>
      <div>${esc(p.address)}${p.place ? ", " + esc(p.place) : ""}</div>
      <div class="sub">${p.geo.label && p.geo.label !== p.address ? "→ " + esc(p.geo.label) : ""}${p.geo.note ? " · " + esc(p.geo.note) : ""}${promise}</div>
      <div class="acts">
        <button class="small" data-act="pin" data-id="${esc(p.id)}">${p.lat == null ? "Postavi pin" : "Pomeri pin"}</button>
        ${p.lat != null ? `<button class="small" data-act="focus" data-lat="${p.lat}" data-lon="${p.lon}">Na mapi</button>` : ""}
        <a class="small btn" href="/t/${esc(p.track)}" target="_blank">Stranica kupca</a>
        <button class="small danger" data-act="del" data-id="${esc(p.id)}">Obriši</button>
      </div></div>`;
  }).join("") || `<p class="hint">Nema paketa.</p>`;
}

const kindText = { plan: "plan", narrow: "preciznije", update: "promena", arriving: "izađite", delivered: "isporučeno", retry: "novi pokušaj", failed: "neuspešno" };

function renderNotes() {
  $("#noteList").innerHTML = S.notifications.map((n) =>
    `<li><time>${hm(n.t)}</time><span class="kind">${esc(kindText[n.kind] || n.kind)}</span><b>${esc(n.pkg)}</b> ${esc(n.text)}</li>`).join("") ||
    `<li class="hint">Još nema poruka.</li>`;
  $("#logList").innerHTML = S.log.map((l) => `<li><time>${hm(l.t)}</time>${esc(l.text)}</li>`).join("");
  $("#simInfo").textContent = S.clock === "sim"
    ? `Simulirani sat: ${hm(S.now)} · kurir: ${S.courier.state}${S.courier.target ? " → " + S.courier.target : ""}`
    : "Sat je stvaran. Pokreni simulaciju da vidiš ceo dan za par minuta.";
}

function renderSettings() {
  const f = $("#settings");
  const s = S.settings;
  if (!settingsDirty) {
    for (const k of ["shift_start", "shift_end", "service_min", "notify_before_min", "promise_width_min", "solver_seconds"]) {
      f.elements[k].value = s[k];
    }
    f.elements.traffic_profile.checked = !!s.traffic_profile;
    f.elements.return_to_depot.checked = !!s.return_to_depot;
  }
  $("#depotLabel").textContent = `Magacin: ${s.depot.label} (${s.depot.lat.toFixed(4)}, ${s.depot.lon.toFixed(4)})`;
}

async function refresh() {
  if (busy) return;
  busy = true;
  try {
    S = await api("/api/state");
    renderHeader();
    renderRoute();
    renderPackages();
    renderNotes();
    renderSettings();
    renderMap();
    await renderGeometry();
  } catch (e) {
    $("#clock").textContent = "server?";
  } finally {
    busy = false;
  }
}

// ------------------------------------------------------------------ events
$$(".tabs button").forEach((b) => b.addEventListener("click", () => {
  $$(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
  $$(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== "tab-" + b.dataset.tab));
}));

document.addEventListener("click", async (ev) => {
  const el = ev.target.closest("[data-act]");
  if (!el) return;
  const a = el.dataset.act;
  const id = el.dataset.id;
  if (a === "call") return call(el.dataset.phone);
  if (a === "focus") {
    map.setView([+el.dataset.lat, +el.dataset.lon], 17);
    return;
  }
  if (a === "arrived") return act(() => api(`/api/stops/${id}/arrived`, { body: {} }), "Kupac je obavešten da izađe");
  if (a === "delivered") return act(() => api(`/api/stops/${id}/delivered`, { body: {} }), "Isporučeno ✓");
  if (a === "failed") return act(() => api(`/api/stops/${id}/failed`, { body: {} }), "Zabeleženo, ruta je preračunata");
  if (a === "del") {
    if (!confirm(`Obrisati paket ${id}?`)) return;
    return act(() => api(`/api/packages/${encodeURIComponent(id)}`, { method: "DELETE" }), "Obrisano");
  }
  if (a === "pin") {
    pinFor = id;
    const p = S.packages.find((x) => x.id === id);
    const hint = p && (p.geo.alternatives || [])[0];
    if (hint) map.setView([hint.lat, hint.lon], 16);
    const bar = $("#pinBar");
    bar.innerHTML = `Klikni na mapu da postaviš lokaciju za <b>${esc(id)}</b> (${esc(p ? p.address : "")}). <button class="small" id="pinCancel">Otkaži</button>`;
    bar.classList.remove("hidden");
    $("#pinCancel").onclick = () => { pinFor = null; bar.classList.add("hidden"); };
  }
});

$("#btnPlan").onclick = () => act(() => api("/api/plan", { body: {} }), (o) => `Ruta: ${o.plan.summary ? o.plan.summary.stops : 0} stanica, ${o.plan.solve_ms} ms`);
$("#btnStart").onclick = () => act(() => api("/api/day/start", { body: {} }), "Kurir je krenuo");
$("#btnGmaps10").onclick = () => {
  const u = S && gmNext10();
  if (u) window.open(u, "_blank"); else toast("Nema rute");
};
$("#btnReset").onclick = () => {
  if (confirm("Obrisati sve pakete i rutu za danas?")) act(() => api("/api/reset", { body: {} }), "Novi dan");
};

$("#btnDemo").onclick = () => act(() => api(`/api/demo/${$("#demoSel").value}`, { body: {} }),
  (o) => `Učitano ${o.added} paketa${o.errors.length ? ", greške: " + o.errors.length : ""}`);

$("#manual").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const body = Object.fromEntries(new FormData(ev.target).entries());
  act(() => api("/api/packages", { body }), (o) => `Dodat ${o.package.id} (${confText[o.package.confidence]})`)
    .then(() => ev.target.reset());
});

$("#csvFile").addEventListener("change", async (ev) => {
  const f = ev.target.files[0];
  if (!f) return;
  const text = await f.text();
  ev.target.value = "";
  act(() => api("/api/packages/import", { body: { csv: text } }), (o) => `Uvezeno ${o.added} paketa`);
});

// QR scanning --------------------------------------------------------------
let scanner = null;
let lastScan = { text: "", t: 0 };

async function sendQr(text) {
  const now = Date.now();
  if (text === lastScan.text && now - lastScan.t < 4000) return;
  lastScan = { text, t: now };
  try {
    const o = await api("/api/packages", { body: { qr: text } });
    const p = o.package;
    toast(o.new ? `✓ ${p.id} · ${p.geo.label || p.address} · ${confText[p.confidence]}` : `${p.id} je već skeniran`);
    if (navigator.vibrate) navigator.vibrate(60);
    refresh();
  } catch (e) {
    toast("QR: " + e.message, 4000);
  }
}

$("#btnCam").onclick = async () => {
  const box = $("#reader");
  if (scanner) {
    await scanner.stop().catch(() => {});
    scanner = null;
    box.classList.add("hidden");
    $("#btnCam").textContent = "Skeniraj kamerom";
    return;
  }
  box.classList.remove("hidden");
  scanner = new Html5Qrcode("reader");
  try {
    await scanner.start({ facingMode: "environment" }, { fps: 10, qrbox: { width: 240, height: 240 } }, sendQr);
    $("#btnCam").textContent = "Zaustavi kameru";
  } catch (e) {
    scanner = null;
    box.classList.add("hidden");
    toast("Kamera nije dostupna (" + e + "). Probaj „Slikaj / učitaj QR“.", 5000);
  }
};

$("#qrFile").addEventListener("change", async (ev) => {
  const f = ev.target.files[0];
  ev.target.value = "";
  if (!f) return;
  try {
    const reader = new Html5Qrcode("reader", { verbose: false });
    const text = await reader.scanFile(f, false);
    await sendQr(text);
  } catch (e) {
    toast("Na slici nije pronađen QR kod", 3500);
  }
});

// simulation ---------------------------------------------------------------
$("#simStart").onclick = () => act(() => api("/api/sim/start", { body: {} }), "Simulacija: plan za ceo dan je napravljen");
$$("[data-step]").forEach((b) => b.addEventListener("click", () =>
  act(() => api("/api/sim/step", { body: { minutes: +b.dataset.step } }))));
$("#simStop").onclick = () => {
  stopAuto();
  act(() => api("/api/sim/stop", { body: {} }), "Sat je ponovo stvaran");
};

function stopAuto() {
  clearTimeout(autoTimer);
  autoTimer = null;
  $("#simAuto").textContent = "▶ Auto";
}

$("#simAuto").onclick = () => {
  if (autoTimer) return stopAuto();
  $("#simAuto").textContent = "⏸ Pauza";
  const tick = async () => {
    try {
      await api("/api/sim/step", { body: { minutes: 2 } });
      await refresh();
      if (S && S.courier.state === "done") return stopAuto();
    } catch (e) {
      toast(e.message, 4000);
      return stopAuto();
    }
    if (autoTimer !== null) autoTimer = setTimeout(tick, 700);
  };
  autoTimer = setTimeout(tick, 0);
};

// settings -----------------------------------------------------------------
$("#settings").addEventListener("input", () => { settingsDirty = true; });
$("#settings").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const f = ev.target.elements;
  const body = {
    shift_start: f.shift_start.value, shift_end: f.shift_end.value,
    service_min: +f.service_min.value, notify_before_min: +f.notify_before_min.value,
    promise_width_min: +f.promise_width_min.value, solver_seconds: +f.solver_seconds.value,
    traffic_profile: f.traffic_profile.checked, return_to_depot: f.return_to_depot.checked,
  };
  settingsDirty = false;
  act(() => api("/api/settings", { method: "PUT", body }), "Sačuvano. Klikni Optimizuj rutu da primeniš.");
});
$("#btnDepot").onclick = () => {
  pinFor = "depot";
  toast("Klikni na mapu na mesto magacina");
};

// boot ---------------------------------------------------------------------
(async () => {
  try {
    const ds = await api("/api/datasets");
    $("#demoSel").innerHTML = ds.map((d) => `<option>${esc(d)}</option>`).join("");
    const h = await api("/api/health");
    $("#healthInfo").textContent = `Registar adresa: ${h.addresses.toLocaleString("sr")} zgrada (OSM). Rute: ${h.osrm}`;
  } catch (e) { /* server down: refresh() shows it */ }
  await refresh();
  setInterval(refresh, 3000);
})();
