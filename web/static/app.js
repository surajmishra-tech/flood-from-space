// Flood damage from space - dashboard
const $ = (id) => document.getElementById(id);
const fmt = (v, d = 0) => (v === null || v === undefined) ? "–" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------------- map ----------------
const map = L.map("map", { zoomControl: true }).setView([28.2, 85.35], 11);
// Background pictures only: the analysis never reads them. The satellite mosaic is from 2020,
// before every flood we test, so it cannot show post-flood information.
const satellite = L.tileLayer("https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg", {
  maxNativeZoom: 14, maxZoom: 19,
  attribution: '<a href="https://s2maps.eu" target="_blank">Sentinel-2 cloudless</a> by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2020)',
}).addTo(map);
// The terrain shading sits in its own pane and multiplies with the background, so the photo shows through it
map.createPane("shade");
map.getPane("shade").style.zIndex = 250;
map.getPane("shade").style.mixBlendMode = "multiply";
const street = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19, className: "tiles-dark", attribution: "© OpenStreetMap contributors (today's map, background picture only)",
});
const plain = L.tileLayer("", { attribution: "" });
// On phones the layer list folds into a small icon (tap it to open), so it doesn't cover the map
const smallScreen = window.matchMedia("(max-width: 760px)").matches;
const layerControl = L.control.layers({
  "Satellite 2020 (background only)": satellite, "Street map with names (background only)": street, "No background": plain,
}, {}, { collapsed: smallScreen }).addTo(map);
if (smallScreen) $("layersTab").hidden = true;                      // phones: the list stays folded into the map icon
else $("layersCard").appendChild(layerControl.getContainer());      // bigger screens: the list lives in the "Map layers" tab
map.on("baselayerchange", (e) => { map.getPane("shade").style.mixBlendMode = e.layer === plain ? "normal" : "multiply"; });
L.control.scale({ imperial: false }).addTo(map);
// Place names (towns, rivers, roads) drawn over the photo. A picture only: the analysis never reads it.
map.createPane("labels");
map.getPane("labels").style.zIndex = 450;
map.getPane("labels").style.pointerEvents = "none";
const placeNames = L.layerGroup([
  // towns, districts and borders
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", {
    pane: "labels", maxZoom: 13,   // closer in, this layer adds ward numbers that look like stray digits
    attribution: "Place names: Esri, HERE, Garmin, © OpenStreetMap contributors (today's map, picture only)",
  }),
  // rivers, peaks and passes
  L.tileLayer("https://tiles.maps.eox.at/wmts/1.0.0/overlay_bright_3857/default/g/{z}/{y}/{x}.png", {
    pane: "labels", maxZoom: 19, maxNativeZoom: 14,   // no closer tiles exist; zoom 14 is stretched beyond
    attribution: 'Rivers and peaks: © OpenStreetMap contributors, rendering © <a href="https://eox.at" target="_blank">EOX</a>',
  }),
]).addTo(map);
layerControl.addOverlay(placeNames, "Place names (today's map, picture only)");
$("legendTitle").onclick = () => $("legend").classList.toggle(smallScreen ? "open" : "closed");  // fold the key away
new ResizeObserver(() => map.invalidateSize()).observe($("map"));  // panels open/close: keep the map filling its space
let runLayers = [];
let currentRun = null;
let drawTool = null;
const drawn = new L.FeatureGroup().addTo(map);

let runRefs = {};    // the layers the map key switches on and off
let labelled = [];   // markers that carry a name on the map
function clearRunLayers() {
  runLayers.forEach(({ layer }) => { map.removeLayer(layer); layerControl.removeLayer(layer); });
  runLayers = [];
  runRefs = {};
  labelled = [];
}
function addLayer(name, layer, on = true) {
  runLayers.push({ name, layer });
  layerControl.addOverlay(layer, name);
  if (on) layer.addTo(map);
}

// ---------------- drawing a box ----------------
$("drawBtn").onclick = () => {
  if (drawTool) { drawTool.disable(); drawTool = null; $("drawBtn").classList.remove("active"); return; }
  drawTool = new L.Draw.Rectangle(map, { shapeOptions: { color: "#22d3ee", weight: 2, fillOpacity: 0.06 } });
  drawTool.enable();
  $("drawBtn").classList.add("active");
};
map.on(L.Draw.Event.CREATED, (e) => {
  drawn.clearLayers(); drawn.addLayer(e.layer);
  const b = e.layer.getBounds();
  $("bbox").value = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(4)).join(", ");
  drawTool = null; $("drawBtn").classList.remove("active");
});

// ---------------- runs ----------------
async function loadRunList(selectId) {
  const runs = await (await fetch("/api/runs")).json();
  const sel = $("runs");
  sel.innerHTML = '<option value="">— choose —</option>' + runs.map((r) =>
    `<option value="${r.run_id}">${esc(r.name || "Unnamed")} · ${r.flood_date} ${r.status !== "done" ? "(" + esc(r.status) + ")" : ""}</option>`).join("");
  if (selectId) sel.value = selectId;
  return runs;
}
$("runs").onchange = (e) => { if (e.target.value) openRun(e.target.value); };

$("runBtn").onclick = async () => {
  const bbox = $("bbox").value.split(",").map((v) => parseFloat(v.trim()));
  if (bbox.length !== 4 || bbox.some(isNaN)) { alert("Draw a box on the map first (or type west, south, east, north)."); return; }
  const body = { bbox, date: $("date").value, name: $("name").value || null };
  const r = await fetch("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json();
  if (!r.ok) { alert(j.detail || "Could not start"); return; }
  watch(j.run_id);
};

async function watch(rid) {
  $("progress").hidden = false;
  for (;;) {
    const s = await (await fetch(`/api/runs/${rid}`)).json();
    $("progress").querySelector("span").style.width = (s.progress || 0) + "%";
    $("progress").querySelector(".msg").textContent = `${s.progress || 0}% · ${s.message || s.status}`;
    if (s.status === "error") { $("progress").querySelector(".msg").textContent = "Error: " + (s.error || ""); return; }
    if (s.status === "done" && s.message === "Done") break;
    await new Promise((res) => setTimeout(res, 2000));
  }
  await loadRunList(rid);
  openRun(rid);
}

// ---------------- showing a run ----------------
const statusColor = { cut_off: "#d7191c", detour: "#f39c12", ok: "#1a9641", no_road: "#999999" };
const statusText = { cut_off: "CUT OFF", detour: "long detour", ok: "still connected", no_road: "no road before the flood" };

let openSeq = 0;  // only the newest openRun() may draw, so two quick clicks never mix two runs' layers
async function openRun(rid, { fit = true } = {}) {
  const my = ++openSeq;
  const st = await (await fetch(`/api/runs/${rid}`)).json();
  if (my !== openSeq) return;
  if (st.status !== "done") { watch(rid); return; }
  currentRun = rid;
  clearRunLayers();
  const [facts, ov] = await Promise.all([fetch(`/api/runs/${rid}/facts`).then((r) => r.json()), fetch(`/api/runs/${rid}/overlays`).then((r) => r.json())]);
  if (my !== openSeq) return;
  const img = (key, name, on, opacity = 1) => {
    if (!ov[key]) return null;
    const l = L.imageOverlay(`/api/runs/${rid}/overlays/${key}.png`, ov[key], { opacity, pane: key === "hillshade" ? "shade" : "overlayPane" });
    addLayer(name, l, on);
    return l;
  };
  img("hillshade", "Terrain (Copernicus DEM)", true, 0.75);
  img("photo_before", "Photo before (Sentinel-2)", false);
  img("photo_after", "Photo after (Sentinel-2)", false);
  img("radar_before", "Radar before (Sentinel-1)", false);
  img("radar_after", "Radar after (Sentinel-1)", false);
  img("radar_change", "Radar change score", false, 0.9);
  img("valley", "Valley floor checked", false, 0.8);
  runRefs.flood = img("flood", "Flood / debris map", true, 0.9);

  const gj = async (name) => (await fetch(`/api/runs/${rid}/layers/${name}`)).json();
  const [aoi, bh, rc, br, se, ho] = await Promise.all(["aoi", "buildings_hit", "roads_cut", "bridges", "settlements", "hospitals"].map(gj));
  if (my !== openSeq) return;  // the newer openRun() clears and draws its own layers
  runRefs.aoi = L.geoJSON(aoi, { style: { color: "#22d3ee", weight: 1.6, fill: false, dashArray: "6 5" }, interactive: false });
  addLayer("Study area", runRefs.aoi, true);
  runRefs.buildings = L.geoJSON(bh, { style: (f) => ({ color: "#7f0000", weight: 1, fillColor: f.properties.confidence === "high" ? "#d7191c" : "#fc8d59", fillOpacity: 0.8 }) });
  addLayer(`Buildings hit (${bh.features.length})`, runRefs.buildings, true);
  runRefs.roads = L.geoJSON(rc, { style: (f) => ({ color: "#d7191c", weight: f.properties.bridge ? 7 : 4, opacity: 0.95 }) });
  addLayer("Cut roads", runRefs.roads, true);
  runRefs.bridges = L.geoJSON(br, {
    pointToLayer: (f, ll) => L.circleMarker(ll, { radius: f.properties.hit ? 6 : 4, color: "#fff", weight: 1.5, fillColor: f.properties.hit ? "#7f0000" : "#666", fillOpacity: 1 }),
    onEachFeature: (f, l) => l.bindPopup(`<b>Bridge</b> ${esc(f.properties.name)}<br>${f.properties.hit ? "<b style='color:#d7191c'>reached by the flood</b>" : "not reached"}<br><span class="ne">${esc(f.properties.road_type)}</span>`),
  });
  addLayer("Bridges", runRefs.bridges, true);
  runRefs.hospitals = L.geoJSON(ho, {
    pointToLayer: (f, ll) => L.marker(ll, { icon: L.divIcon({ className: `hosp-icon${f.properties.hit ? " hit" : ""}`, html: "+", iconSize: [17, 17] }), keyboard: false }),
    onEachFeature: (f, l) => {
      l.bindPopup(`<b>${esc(f.properties.name || "Health post")}</b> <span class="ne">${esc(f.properties.name_ne || "")}</span><br>${f.properties.hit ? "<b style='color:#d7191c'>reached by the flood</b>" : "not reached by the flood"}`);
      if (f.properties.name) addLabel(l, f.properties, "hospital");
    },
  });
  addLayer("Hospitals / health posts", runRefs.hospitals, true);
  const seLayer = L.geoJSON(se, {
    pointToLayer: (f, ll) => L.circleMarker(ll, { radius: f.properties.status === "cut_off" ? 8 : 5, color: "#fff", weight: 1.5, fillColor: statusColor[f.properties.status] || "#999", fillOpacity: 1 }),
    onEachFeature: (f, l) => { l.bindPopup(settlementPopup(f.properties)); if (f.properties.name) addLabel(l, f.properties, f.properties.status); },
  });
  runRefs.settlements = seLayer;
  runRefs.seMarkers = seLayer.getLayers();
  addLayer("Settlements (road access)", seLayer, true);
  applyStatus();

  if (fit) map.fitBounds(L.geoJSON(aoi).getBounds(), { padding: [20, 20] });
  showFacts(facts, rid, seLayer);
  syncKey();
  requestAnimationFrame(declutter);
  store.set("run", rid);
  if (focusId) refreshRequests();  // a /?focus= link waits for the map before zooming
}

// ---------------- names on the map ----------------
// Cut-off villages first (most urgent first), then detours, hospitals and the rest. Names that would
// overlap a more important one are hidden until you zoom in.
const labelBase = { cut_off: 4000, detour: 3000, hospital: 2000, ok: 1000, no_road: 500 };
const genericName = /^\(?unnamed\)?$|^hospital \/ health post$/i;
function addLabel(layer, p, kind) {
  if (!p.name || genericName.test(p.name.trim())) return;
  const prio = kind === "cut_off" && p.priority != null ? `<span class="lp">${p.priority}</span>` : "";
  layer.bindTooltip(`${esc(p.name)}${prio}`, { permanent: true, direction: "right", offset: [9, 0], className: `map-label ml-${kind}`, interactive: false });
  layer.labelRank = (labelBase[kind] || 0) + (p.priority ?? 0) * 10 + Math.min(p.buildings_nearby || 0, 999) / 1000;
  labelled.push(layer);
}
function declutter() {
  const show = map.hasLayer(placeNames);   // the "names" row of the map key
  const items = labelled.filter((l) => map.hasLayer(l) && l.getTooltip() && l.getTooltip().getElement())
    .map((l) => ({ l, el: l.getTooltip().getElement(), r: l.getTooltip().getElement().getBoundingClientRect() }))
    .sort((a, b) => b.l.labelRank - a.l.labelRank);
  const placed = [];
  items.forEach(({ el, r }) => {
    const free = show && r.width > 0 && !placed.some((q) => r.left - 3 < q.right && r.right + 3 > q.left && r.top - 2 < q.bottom && r.bottom + 2 > q.top);
    if (free) placed.push(r);
    el.style.visibility = free ? "" : "hidden";
  });
}
map.on("zoomend moveend", declutter);

// ---------------- the map key: each row switches its layer ----------------
const hiddenStatus = new Set();   // kinds of village switched off in the map key
function applyStatus() {
  const se = runRefs.settlements;
  if (!se) return;
  runRefs.seMarkers.forEach((m) => {
    const hide = hiddenStatus.has(m.feature.properties.status);
    if (hide && se.hasLayer(m)) se.removeLayer(m);
    else if (!hide && !se.hasLayer(m)) se.addLayer(m);
  });
}
const keyLayer = (k) => ({
  names: placeNames, help: helpLayer, here: hereLayer, flood: runRefs.flood, roads: runRefs.roads,
  buildings: runRefs.buildings, bridges: runRefs.bridges, hospitals: runRefs.hospitals, aoi: runRefs.aoi,
}[k]);
function syncKey() {
  document.querySelectorAll("#legend button[data-key]").forEach((b) => {
    const k = b.dataset.key;
    let on;
    if (statusColor[k]) { on = !hiddenStatus.has(k); b.classList.toggle("na", !runRefs.settlements); }
    else { const l = keyLayer(k); b.classList.toggle("na", !l || (k === "help" && mode !== "team")); on = !!l && map.hasLayer(l); }
    b.classList.toggle("off", !on);
    b.setAttribute("aria-pressed", String(on));
  });
}
let syncSoon = false;
map.on("layeradd layerremove", () => {  // the "Map layers" tab switches the same layers
  if (syncSoon) return;
  syncSoon = true;
  requestAnimationFrame(() => { syncSoon = false; syncKey(); });
});
document.querySelectorAll("#legend button[data-key]").forEach((b) => b.addEventListener("click", () => {
  const k = b.dataset.key;
  if (statusColor[k]) {
    if (!hiddenStatus.delete(k)) hiddenStatus.add(k);
    applyStatus();
  } else {
    const l = keyLayer(k);
    if (!l) return;
    if (map.hasLayer(l)) map.removeLayer(l); else l.addTo(map);
  }
  syncKey();
  declutter();
}));

function settlementPopup(p) {
  let breaks = [];
  try { breaks = JSON.parse(p.breaks || "[]"); } catch (e) { /* ignore */ }
  const why = breaks.slice(0, 3).map((b) => `${b.bridge ? "bridge" : "road"} ${esc(b.road_name || b.road_type)} (${fmt(b.length_m)} m)`).join("<br>");
  return `<b>${esc(p.name)}</b> <span class="ne">${esc(p.name_ne || "")}</span><br>
    <b style="color:${statusColor[p.status]}">${statusText[p.status] || p.status}</b>${p.priority != null ? ` · rescue priority <b>${p.priority}</b>/100` : ""}<br>
    ${p.buildings_nearby} buildings nearby, ${p.buildings_hit_nearby} hit<br>
    ${p.dist_before_km != null ? `Before: ${fmt(p.dist_before_km, 1)} km to ${esc(p.reach_before)}<br>` : ""}
    ${p.dist_after_km != null ? `After: ${fmt(p.dist_after_km, 1)} km to ${esc(p.reach_after)}<br>` : ""}
    ${why ? `<b>Broken on the old route:</b><br>${why}` : ""}`;
}

// rescue priority 0-100, computed by code from size, damage, isolation and remoteness (floodmap/priority.py)
function prioChip(a) {
  if (a.priority == null) return "";
  const lvl = a.priority >= 70 ? "hi" : a.priority >= 45 ? "mid" : "lo";
  const p = a.priority_parts;
  const tip = p ? `size ${p.size} · damage ${p.damage} · isolation ${p.isolation} · remoteness ${p.remote} (each out of 100)` : "";
  return `<span class="prio prio-${lvl}" title="${tip}">${a.priority}</span>`;
}

function showFacts(f, rid, seLayer) {
  $("results").hidden = false;
  document.body.classList.add("has-run");
  const area = f.meta.area_name || "Unnamed area";
  $("areaName").textContent = `${area} · ${f.meta.flood_date}`;
  $("runChipName").textContent = area;
  $("runChipDate").textContent = f.meta.flood_date;
  const t = (cls, label, value) => `<div class="kpi ${cls}"><span>${label}</span><b>${value}</b></div>`;
  $("tiles").innerHTML = [
    t("k-flood", "Flooded / debris", `${fmt(f.flood.flood_km2, 2)} <small>km²</small>`),
    t("k-bld", "Buildings hit", `${fmt(f.buildings.hit)} <small>of ${fmt(f.buildings.total)}</small>`),
    t("k-road", "Road under flood", `${fmt(f.roads.flooded_km, 1)} <small>km</small>`),
    t("k-bridge", "Bridges reached", `${fmt(f.bridges.hit)} <small>of ${fmt(f.bridges.total)}</small>`),
    t(`k-cut${f.settlements.cut_off > 0 ? " alert" : ""}`, "Settlements cut off", fmt(f.settlements.cut_off)),
    t("k-detour", "Long detour", fmt(f.settlements.detour)),
  ].join("");
  const zoomTo = (lat, lon) => map.setView([lat, lon], 15);
  const byName = {};
  seLayer.eachLayer((l) => { byName[l.feature.properties.name + l.feature.properties.lat] = l; });
  const li = (a, extra) => `<li data-lat="${a.lat}" data-lon="${a.lon}" data-key="${esc(a.name + a.lat)}">${prioChip(a)}<b>${esc(a.name)}</b> <span class="sub">${esc(a.name_ne || "")}</span><br><span class="sub">${extra}</span></li>`;
  const urgent = (a, b) => (b.priority ?? -1) - (a.priority ?? -1) || b.buildings_nearby - a.buildings_nearby;
  const cut = [...f.settlements.cut_off_list].sort(urgent);
  $("cutCount").textContent = cut.length;
  $("cutList").innerHTML = cut.map((a) => li(a, `${a.buildings_nearby} buildings nearby · ${a.buildings_hit_nearby} hit · ${a.breaks.length} broken stretch(es)${a.dist_before_km != null ? ` · ${fmt(a.dist_before_km, 1)} km to help before` : ""}`)).join("") || "<li>None</li>";
  const det = f.settlements.detour_list;
  $("detourCount").textContent = det.length;
  $("detourList").innerHTML = det.map((a) => li(a, `+${fmt(a.dist_after_km - a.dist_before_km, 1)} km (${fmt(a.dist_before_km, 1)} → ${fmt(a.dist_after_km, 1)} km)`)).join("") || "<li>None</li>";
  $("bridgeCount").textContent = f.bridges.hit;
  $("bridgeList").innerHTML = f.bridges.hit_list.map((b) => `<li data-lat="${b.lat}" data-lon="${b.lon}"><b>${esc(b.name || "Unnamed bridge")}</b> <span class="sub">${esc(b.road_type)}</span></li>`).join("") || "<li>None</li>";
  document.querySelectorAll(".list li[data-lat]").forEach((el) => el.onclick = () => {
    const l = byName[el.dataset.key];
    if (l) {
      if (!map.hasLayer(seLayer)) seLayer.addTo(map);
      if (hiddenStatus.delete(l.feature.properties.status)) { applyStatus(); syncKey(); }
    }
    zoomTo(+el.dataset.lat, +el.dataset.lon);
    if (l) l.openPopup();
  });
  $("sitrepEn").href = `/api/runs/${rid}/sitrep/en`;
  $("sitrepNe").href = `/api/runs/${rid}/sitrep/ne`;
  const radar = f.images.radar.map((r) => `Radar track ${r.track} (${r.direction}): before ${r.before.join(", ")} → after ${r.after.join(", ")}`).join("<br>") || "No radar pair found";
  const opt = f.images.optical ? `Photos (Sentinel-2): ${f.images.optical.before} → ${f.images.optical.after}, ${f.images.optical.clear_valley_pct}% of the valley cloud-free` : "No cloud-free photo pair";
  $("images").innerHTML = `${radar}<br>${opt}<br>OpenStreetMap as on ${f.osm.snapshot_date} (${esc(f.osm.source)})<br>Pixel size ${f.meta.resolution_m} m · valley floor seen: ${f.flood.valley_seen_pct}%`;
  $("warnings").innerHTML = (f.warnings || []).map((w) => "⚠ " + esc(w)).join("<br>");
  $("attrib").innerHTML = f.attribution.map(esc).join("<br>") + "<br>Made by Suraj Mishra · Educational prototype, not an operational tool.";
}

// ---------------- copilot ----------------
$("askBtn").onclick = async () => {
  if (!currentRun) return;
  const q = $("question").value.trim(); if (!q) return;
  $("answer").hidden = false; $("answer").textContent = "Thinking…";
  const r = await fetch(`/api/runs/${currentRun}/ask`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q, lang: $("lang").value }) });
  const j = await r.json();
  const pr = j.proof || {};
  const hl = (t) => esc(t).replace(/\{([a-z0-9_]+)\}/g, '<mark>{$1}</mark>');
  $("answer").innerHTML = esc(j.text || j.detail || "No answer") +
    `<span class="by">${esc(j.by || "")} · ✓ ${pr.values_inserted_by_code ?? 0} values inserted by our code, 0 digits written by the AI` +
    (pr.model_text ? ` · <a href="#" id="proofLink">show the AI's own words</a>` : "") + `</span>` +
    (pr.model_text ? `<div id="proofBox" class="proof" hidden><b>What the AI wrote (placeholders, no numbers):</b><br>${hl(pr.model_text)}` +
      ((pr.rejected_drafts || []).length ? `<br><b>Drafts our checker rejected:</b> ${pr.rejected_drafts.length}` : "") + `</div>` : "");
  const link = document.getElementById("proofLink");
  if (link) link.onclick = (e) => { e.preventDefault(); const b = document.getElementById("proofBox"); b.hidden = !b.hidden; };
};

// ---------------- bonus: flood path ----------------
let pathMode = false; let pathLayer = null;
$("pathBtn").onclick = () => { setPickMode(false); pathMode = !pathMode; $("pathBtn").classList.toggle("active", pathMode); map.getContainer().style.cursor = pathMode ? "crosshair" : ""; };
// "preclick" also fires when the click lands on a village or bridge marker (their popups swallow "click")
map.on("preclick", async (e) => {
  if (!pathMode) return;
  pathMode = false; $("pathBtn").classList.remove("active"); map.getContainer().style.cursor = "";
  $("pathList").innerHTML = "<li>Tracing downhill…</li>";
  const r = await fetch("/api/floodpath", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lon: e.latlng.lng, lat: e.latlng.lat, date: $("date").value || null }) });
  const j = await r.json();
  if (!r.ok) { $("pathList").innerHTML = `<li>${esc(j.detail)}</li>`; return; }
  if (pathLayer) map.removeLayer(pathLayer);
  pathLayer = L.geoJSON(j.geojson, {
    style: { color: "#e879f9", weight: 4 },
    pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 6, color: "#fff", weight: 1.5, fillColor: "#c026d3", fillOpacity: 1 }).bindPopup(`<b>${esc(f.properties.name)}</b><br>${fmt(f.properties.km_downstream, 1)} km downstream, ${fmt(f.properties.height_above_path_m)} m above the flow path`),
  }).addTo(map);
  $("pathList").innerHTML = `<li class="sub">Path ${fmt(j.length_km, 1)} km downhill.` +
    (j.buildings_at_risk != null ? ` <b>${fmt(j.buildings_at_risk)} buildings</b> lie within 200 m of it and less than 30 m above it.` : "") +
    ` Named settlements along it:</li>` +
    (j.settlements.map((s) => `<li data-lat="${s.lat}" data-lon="${s.lon}"><b>${esc(s.name)}</b> <span class="sub">${esc(s.name_ne || "")} · ${fmt(s.km_downstream, 1)} km · ${fmt(s.height_above_path_m)} m above</span></li>`).join("") || "<li>None found</li>");
  document.querySelectorAll("#pathList li[data-lat]").forEach((el) => el.onclick = () => map.setView([+el.dataset.lat, +el.dataset.lon], 15));
});

// ================= two sides: "I need help" and "Rescue team" =================
const store = {  // per-device conveniences only; the page works without them
  get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
};
const helpLayer = L.layerGroup();
let mode = "team";
function setMode(m) {
  mode = m;
  document.body.classList.toggle("mode-help", m === "help");  // the CSS shows .help-only or .team-only parts
  document.body.classList.toggle("mode-team", m === "team");
  $("modeHelp").classList.toggle("on", m === "help");
  $("modeTeam").classList.toggle("on", m === "team");
  if (m === "team") helpLayer.addTo(map); else map.removeLayer(helpLayer);
  if (m !== "help") setPickMode(false);
  store.set("mode", m);
  refreshRequests();  // help side: counts only; rescue side: the inbox (with PIN)
}
$("modeHelp").onclick = () => setMode("help");
$("modeTeam").onclick = () => setMode("team");

// right-panel tabs: Copilot · Map layers · Help requests
function showTab(id) {
  if (!$(id) || (id === "tabLayers" && smallScreen)) id = "tabCopilot";
  document.querySelectorAll(".tabs .tab").forEach((b) => b.classList.toggle("on", b.dataset.tab === id));
  document.querySelectorAll(".tabpane").forEach((p) => { p.hidden = p.id !== id; });
  store.set("tab", id);
}
document.querySelectorAll(".tabs .tab").forEach((b) => { b.onclick = () => showTab(b.dataset.tab); });

// ---------------- "I need help": find the person's place ----------------
let pickMode = false;
let here = null;          // {lat, lon, accuracy_m, how, place_text}
let hereLayer = null;
let lastSafety = null;
function setPickMode(on) {
  pickMode = on;
  $("pickBtn").classList.toggle("active", on);
  if (on) { pathMode = false; $("pathBtn").classList.remove("active"); }
  map.getContainer().style.cursor = on ? "crosshair" : "";
}
$("pickBtn").onclick = () => {
  setPickMode(!pickMode);
  $("locMsg").textContent = pickMode ? "Now tap your place on the map. · अब नक्सामा आफ्नो ठाउँ छुनुहोस्।" : "";
  if (pickMode && smallScreen) $("map").scrollIntoView({ behavior: "smooth" });
};
map.on("preclick", (e) => {  // see the flood-path handler: works on markers too
  if (!pickMode) return;
  setPickMode(false);
  checkPlace({ lat: e.latlng.lat, lon: e.latlng.lng, how: "map", place_text: "tapped on the map" });
});

$("gpsBtn").onclick = () => {
  if (!navigator.geolocation) { $("locMsg").textContent = "This browser cannot share location. Type your village instead."; return; }
  $("locMsg").textContent = "Finding your location… · तपाईंको स्थान खोज्दै…";
  navigator.geolocation.getCurrentPosition(
    (p) => checkPlace({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy_m: Math.round(p.coords.accuracy), how: "gps", place_text: "current location (GPS)" }),
    (err) => { $("locMsg").textContent = (err.code === 1 ? "Location permission was refused." : "Could not get your location.") + " Type your village or tap the map instead."; },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
};

async function searchPlace() {
  const q = $("placeQ").value.trim(); if (!q) return;
  const list = await (await fetch(`/api/places?q=${encodeURIComponent(q)}`)).json();
  $("placeList").innerHTML = list.map((p, i) => `<li data-i="${i}"><b>${esc(p.name)}</b> <span class="sub">${esc(p.name_ne)} · ${esc(p.kind)}</span></li>`).join("")
    || `<li class="sub">Not found in our maps. Try another spelling, or tap your place on the map.</li>`;
  $("placeList").querySelectorAll("li[data-i]").forEach((el) => el.onclick = () => {
    const p = list[+el.dataset.i];
    $("placeList").innerHTML = "";
    checkPlace({ lat: p.lat, lon: p.lon, how: "typed", place_text: p.name });
  });
}
$("placeBtn").onclick = searchPlace;
$("placeQ").onkeydown = (e) => { if (e.key === "Enter") searchPlace(); };

let checkSeq = 0;
async function checkPlace(p, { showForm = true } = {}) {
  const my = ++checkSeq;
  here = p;
  $("locMsg").textContent = "Checking our flood maps… · बाढी नक्सा जाँच्दै…";
  const r = await fetch("/api/safety", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lat: p.lat, lon: p.lon, run_id: currentRun }) });
  const s = await r.json();
  if (my !== checkSeq) return;  // a newer check started meanwhile
  if (!r.ok) { $("locMsg").textContent = "Error: " + (s.detail || r.status); return; }
  lastSafety = s;
  if (s.run_id && s.run_id !== currentRun) { $("runs").value = s.run_id; await openRun(s.run_id, { fit: false }); }
  if (my !== checkSeq) return;
  if (hereLayer) map.removeLayer(hereLayer);
  hereLayer = L.layerGroup([
    ...(p.accuracy_m ? [L.circle([p.lat, p.lon], { radius: p.accuracy_m, color: "#2563eb", weight: 1, fillOpacity: 0.08 })] : []),
    L.circleMarker([p.lat, p.lon], { radius: 9, color: "#bcd3ff", weight: 4, fillColor: "#2563eb", fillOpacity: 1 }).bindPopup("You are here · तपाईं यहाँ हुनुहुन्छ"),
  ]).addTo(map);
  map.setView([p.lat, p.lon], 14);
  $("locMsg").textContent = `Place: ${p.place_text}${p.accuracy_m ? ` (±${p.accuracy_m} m)` : ""}`;
  showSafety();
  $("sendMsg").hidden = true;
  // help requests can only be sent from inside a mapped flood area; outside it, the card above says to call 112 / 100
  $("sendCard").hidden = !showForm || s.level === "unknown";
}

function showSafety() {
  if (!lastSafety) return;
  const t = lastSafety.text[$("helpLang").value] || lastSafety.text.en;
  $("safetyCard").hidden = false;
  $("safetyBox").className = `safety lvl-${lastSafety.level}`;
  $("safetyBox").innerHTML = `<h3>${esc(t.title)}</h3><ul>${t.lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>`;
}
$("helpLang").onchange = () => { store.set("helpLang", $("helpLang").value); showSafety(); showSent(); };

// ---------------- "I need help": send the request ----------------
const statusWords = {
  new: ["Sent. Waiting for the rescue team to see it.", "पठाइयो। उद्धार टोलीले हेर्ने प्रतीक्षामा।"],
  seen: ["The rescue team has seen your request.", "उद्धार टोलीले तपाईंको अनुरोध हेरेको छ।"],
  team_sent: ["The rescue team says a team has been sent.", "उद्धार टोलीले टोली पठाइएको जनाएको छ।"],
  helped: ["The rescue team marked your request as helped.", "उद्धार टोलीले मद्दत पुगेको भनी जनाएको छ।"],
};
let myRequest = null; let myTimer = null;
// the phone's own location, read again at send time (the server checks it is in the flood area and near the chosen place)
const readGps = () => new Promise((ok, fail) => {
  if (!navigator.geolocation) { fail(new Error("no geolocation")); return; }
  navigator.geolocation.getCurrentPosition((p) => ok({ lat: p.coords.latitude, lon: p.coords.longitude, acc: Math.round(p.coords.accuracy) }),
    fail, { enableHighAccuracy: true, timeout: 20000, maximumAge: 30000 });
});
function sendProblem(en, ne) {
  $("sendMsg").hidden = false;
  $("sendMsg").textContent = $("helpLang").value === "ne" ? ne : en;
}
$("sendBtn").onclick = async () => {
  if (!here) return;
  if (!$("hConsent").checked) { alert("Please tick the box to agree to share your location.\nकृपया स्थान बाँड्न सहमतिको बाकसमा चिनो लगाउनुहोस्।"); return; }
  $("sendBtn").disabled = true; $("sendMsg").hidden = true;
  $("locMsg").textContent = "Reading your phone's location… · फोनको स्थान पढ्दै…";
  let gps;
  try { gps = await readGps(); } catch (e) {
    $("sendBtn").disabled = false; $("locMsg").textContent = "";
    sendProblem("To send a request we must read your phone's location (this stops fake requests). Please allow location and try again, or call 112 (India) / 100 (Nepal).",
      "अनुरोध पठाउन तपाईंको फोनको स्थान पढ्नुपर्छ (यसले नक्कली अनुरोध रोक्छ)। कृपया स्थान अनुमति दिनुहोस् र फेरि प्रयास गर्नुहोस्, वा ११२ (भारत) / १०० (नेपाल) मा फोन गर्नुहोस्।");
    return;
  }
  $("locMsg").textContent = `Place: ${here.place_text}${here.accuracy_m ? ` (±${here.accuracy_m} m)` : ""}`;
  const body = {
    lat: here.lat, lon: here.lon, accuracy_m: here.accuracy_m || null, how: here.how, place_text: here.place_text,
    name: $("hName").value.trim() || null, phone: $("hPhone").value.replace(/[^0-9+\-() ]/g, "").slice(0, 20) || null,
    people: Math.max(1, Math.min(999, parseInt($("hPeople").value, 10) || 1)), note: $("hNote").value.trim() || null,
    lang: $("helpLang").value, consent: true, run_id: currentRun, gps_lat: gps.lat, gps_lon: gps.lon, gps_accuracy_m: gps.acc,
  };
  const r = await fetch("/api/help", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json();
  $("sendBtn").disabled = false;
  if (!r.ok) {
    if (j.detail && j.detail.en) sendProblem(j.detail.en, j.detail.ne);  // outside the flood area, or too far from the chosen place
    else sendProblem("Could not send. Please check the form.", "पठाउन सकिएन। कृपया फारम जाँच्नुहोस्।");
    return;
  }
  myRequest = { id: j.id, status: j.status, level: j.level };
  store.set("myRequest", j.id);
  $("sendCard").hidden = true;  // the form is done; the "Your help request" card follows it from now on
  showSent(); followMyRequest();
  $("trackCard").scrollIntoView({ behavior: "smooth", block: "nearest" });
};
const levelNe = { danger: "खतरा", cut_off: "सडक सम्पर्क टुटेको", near: "बाढी नजिक", clear: "नक्सामा बाढी छैन", unknown: "नक्सा छैन" };
function showSent() {
  if (!myRequest) return;
  const ne = $("helpLang").value === "ne";
  const w = statusWords[myRequest.status] || statusWords.new;
  const lvl = myRequest.level ? ((ne ? levelNe : levelText)[myRequest.level] || myRequest.level) : "";
  $("trackCard").hidden = false;
  $("sentBox").innerHTML = `<b>${ne ? "अनुरोध नम्बर" : "Request number"}: ${esc(myRequest.id)}</b><br>${esc(ne ? w[1] : w[0])}` +
    (lvl ? `<br><span class="sub">${ne ? "हाम्रो नक्सा अनुसार" : "Our maps say"}:</span> <b class="lvl-txt lvl-${myRequest.level}">${esc(lvl)}</b>` : "");
  const link = `${location.origin}/?request=${myRequest.id}`;  // status only: it never shows the place, name or phone
  $("trackLink").href = link; $("trackLink").textContent = link;
}
$("trackCopy").onclick = () => {
  const t = $("trackLink").href;
  const done = () => { $("trackCopy").textContent = "Copied ✓"; setTimeout(() => { $("trackCopy").textContent = "Copy"; }, 1500); };
  if (navigator.clipboard) navigator.clipboard.writeText(t).then(done, () => prompt("Copy this link:", t)); else prompt("Copy this link:", t);
};
function followMyRequest() {
  clearInterval(myTimer);
  myTimer = setInterval(async () => {
    if (document.visibilityState !== "visible") return;
    const r = await fetch(`/api/help/${myRequest.id}`);
    if (!r.ok) return;
    const j = await r.json();
    if (j.status !== myRequest.status || j.level !== myRequest.level) { myRequest.status = j.status; myRequest.level = j.level; showSent(); }
    if (j.status === "helped") clearInterval(myTimer);
  }, 12000);
}

// ---------------- Rescue team: the help-request inbox ----------------
// Requests hold names, phones and places. The "I need help" side only ever gets counts;
// the full list needs the rescue team PIN (when the server has one), remembered on this device.
const levelText = { danger: "DANGER", cut_off: "CUT OFF", near: "NEAR FLOOD", clear: "NO FLOOD MAPPED", unknown: "NOT MAPPED" };
const stLabel = { seen: "Seen", team_sent: "Team sent", helped: "Helped" };
const ago = (iso) => { const m = Math.round((Date.now() - Date.parse(iso)) / 60000); return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`; };
const coords = (q) => `${Number(q.lat).toFixed(5)}, ${Number(q.lon).toFixed(5)}`;
const directions = (q) => `https://www.google.com/maps/dir/?api=1&destination=${Number(q.lat)},${Number(q.lon)}`;
const howText = { gps: "GPS", typed: "typed place", map: "tapped on map" };
let rescuePin = store.get("rescuePin") || "";
let focusId = (new URLSearchParams(location.search).get("focus") || "").toUpperCase();  // /?focus=<id>: jump to it once
let highlightId = "";
const pinHeaders = (extra = {}) => (rescuePin ? { ...extra, "X-Rescue-Pin": rescuePin } : extra);

function showLocked(msg) {
  $("pinBox").hidden = false; $("reqTools").hidden = true; $("recheckMsg").hidden = true;
  $("reqList").innerHTML = ""; $("pinMsg").textContent = msg || "";
  helpLayer.clearLayers();
}
function unlockWith(pin) { rescuePin = pin.trim(); store.set("rescuePin", rescuePin); $("pinInput").value = ""; refreshRequests(); }
$("pinBtn").onclick = () => unlockWith($("pinInput").value);
$("pinInput").onkeydown = (e) => { if (e.key === "Enter") unlockWith($("pinInput").value); };
$("lockBtn").onclick = () => { rescuePin = ""; store.set("rescuePin", ""); refreshRequests(); };
$("recheckBtn").onclick = async () => {
  $("recheckBtn").disabled = true;
  const r = await fetch("/api/help/recheck", { method: "POST", headers: pinHeaders() });
  $("recheckBtn").disabled = false;
  const j = await r.json().catch(() => ({}));
  $("recheckMsg").hidden = false;
  $("recheckMsg").textContent = r.ok ? `Checked ${j.checked} open request(s) with the newest maps: ${j.changed} danger level(s) changed.` : (j.detail || "Could not re-check.");
  refreshRequests();
};

let inboxStamp = null;   // when the inbox last changed; the full list is only downloaded again after a change
async function refreshRequests({ quiet = false } = {}) {
  let sum;
  try { sum = await (await fetch("/api/help/summary")).json(); } catch (e) { return; }
  $("newCount").hidden = !sum.new; $("newCount").textContent = `${sum.new} new`;
  $("reqTab").classList.toggle("alert", sum.new > 0);
  $("reqCount").textContent = sum.open;
  if (mode !== "team") { helpLayer.clearLayers(); return; }  // the "I need help" side never downloads other people's requests
  if (sum.pin_required && !rescuePin) { showLocked(""); return; }
  if (quiet && sum.stamp === inboxStamp) return;   // nothing new since the last download
  let r;
  try { r = await fetch("/api/help", { headers: pinHeaders() }); } catch (e) { return; }
  if (r.ok) inboxStamp = sum.stamp;
  if (r.status === 401 || r.status === 429) {
    const j = await r.json().catch(() => ({}));
    if (r.status === 401) { rescuePin = ""; store.set("rescuePin", ""); }
    showLocked(j.detail || "PIN needed.");
    return;
  }
  if (!r.ok) return;
  const reqs = await r.json();
  $("pinBox").hidden = true; $("reqTools").hidden = false; $("lockBtn").hidden = !sum.pin_required;
  $("reqList").innerHTML = reqs.map((q) => {
    const lc = [...(q.history || [])].reverse().find((h) => h.level_to);  // the last danger-level change, if any
    return `<li class="lvl-${q.level} st-${q.status}${q.id === highlightId ? " focus" : ""}" data-id="${q.id}">
      <span class="tag">${levelText[q.level] || q.level}</span> <b>${esc(q.people)} ${q.people > 1 ? "people" : "person"}</b>
      · ${esc(q.settlement || q.place_text || "")}${q.settlement_status === "cut_off" ? " (cut off)" : ""}
      <span class="sub">· #${q.id} · ${ago(q.created_utc)} · ${howText[q.how] || esc(q.how)}</span>
      <div class="loc">📍 <span class="mono">${coords(q)}</span>${q.accuracy_m ? ` <span class="sub">±${fmt(q.accuracy_m)} m</span>` : ""}
        <button type="button" class="mini" data-act="copy">Copy</button></div>
      ${q.gps_checked ? `<div class="ok-line">📡 Phone GPS checked: inside the mapped flood area${q.how !== "gps" && q.gap_km != null ? ` · the chosen place "${esc(q.place_text)}" is ${fmt(q.gap_km, 1)} km from the phone` : ""}</div>` : ""}
      ${q.area_name ? `<div class="sub">Map used: ${esc(q.area_name)}</div>` : ""}
      ${lc ? `<div class="upd">↻ Danger level updated ${ago(lc.utc)}: ${levelText[lc.level_from] || esc(lc.level_from)} → ${levelText[lc.level_to] || esc(lc.level_to)}</div>` : ""}
      ${q.note ? `<div>“${esc(q.note)}”</div>` : ""}
      ${q.name || q.phone ? `<div class="sub">${esc(q.name || "")} ${q.phone ? "· " + esc(q.phone) : ""}</div>` : ""}
      <div class="acts">
        <button type="button" data-act="zoom">Show on map</button>
        <a class="button" href="${directions(q)}" target="_blank" rel="noopener">Directions</a>
        ${q.phone ? `<a class="button" href="tel:${esc(q.phone)}">Call</a>` : ""}
        ${Object.entries(stLabel).map(([k, v]) => `<button type="button" data-act="${k}" class="${q.status === k ? "on" : ""}">${v}</button>`).join("")}
      </div></li>`;
  }).join("") || `<li class="sub">No help requests yet.</li>`;
  const markers = {};
  helpLayer.clearLayers();
  reqs.forEach((q) => {
    if (q.accuracy_m) L.circle([q.lat, q.lon], { radius: q.accuracy_m, color: "#ef4444", weight: 1, dashArray: "4 4", fillOpacity: 0.07, interactive: false }).addTo(helpLayer);
    markers[q.id] = L.marker([q.lat, q.lon], {
      icon: L.divIcon({ className: `sos-icon lvl-${q.level} st-${q.status}`, html: "SOS", iconSize: [30, 20] }),
      zIndexOffset: 1000,
    }).bindPopup(`<b>${levelText[q.level]}</b> · ${esc(q.people)} people · #${q.id}<br>${esc(q.settlement || q.place_text || "")}${q.note ? `<br>“${esc(q.note)}”` : ""}
      <br><span class="mono">${coords(q)}</span>${q.accuracy_m ? ` ±${fmt(q.accuracy_m)} m` : ""} · <a href="${directions(q)}" target="_blank" rel="noopener">Directions</a>
      <br><span class="ne">${esc(q.summary_en)} · ${ago(q.created_utc)}</span>`).addTo(helpLayer);
  });
  $("reqList").querySelectorAll("li[data-id]").forEach((li) => {
    const q = reqs.find((x) => x.id === li.dataset.id);
    li.querySelectorAll("button").forEach((b) => { b.onclick = async () => {
      if (b.dataset.act === "copy") {
        const text = coords(q);
        const done = () => { b.textContent = "Copied ✓"; setTimeout(() => { b.textContent = "Copy"; }, 1500); };
        if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, () => prompt("Copy these coordinates:", text));
        else prompt("Copy these coordinates:", text);
        return;
      }
      if (b.dataset.act === "zoom") {
        map.setView([q.lat, q.lon], 15); markers[q.id].openPopup();
        if (smallScreen) $("map").scrollIntoView({ behavior: "smooth" });
        return;
      }
      const r2 = await fetch(`/api/help/${q.id}/status`, { method: "POST", headers: pinHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ status: b.dataset.act }) });
      if (r2.status === 401) { rescuePin = ""; store.set("rescuePin", ""); }
      refreshRequests();
    }; });
  });
  // a /?focus=<id> link: once the map is ready, zoom to that request and highlight its card
  const fq = focusId && document.body.classList.contains("has-run") && reqs.find((x) => x.id === focusId);
  if (fq) {
    focusId = ""; highlightId = fq.id;
    const li = $("reqList").querySelector(`li[data-id="${fq.id}"]`);
    li.classList.add("focus"); li.scrollIntoView({ block: "nearest" });
    map.setView([fq.lat, fq.lon], 15); markers[fq.id].openPopup();
  }
}
// Checked every 20 s, and only while this page is on screen (the live link has a monthly request limit)
const onScreen = () => document.visibilityState === "visible";
setInterval(() => { if (onScreen()) refreshRequests({ quiet: true }); }, 20000);
document.addEventListener("visibilitychange", () => { if (onScreen()) refreshRequests({ quiet: true }); });

// ---------------- start ----------------
// Links: /?mode=help · /?run=<id> · /?request=<id> (follow one help request) · &lat=..&lon=.. (check that place)
//        /?mode=team&tab=tabRequests&focus=<id> (rescue team: jump to one request)
const qs = new URLSearchParams(location.search);
const linkReq = (qs.get("request") || "").toUpperCase().replace(/[^A-F0-9]/g, "");
const linkLat = parseFloat(qs.get("lat")), linkLon = parseFloat(qs.get("lon"));
const startMode = linkReq ? "help" : (qs.get("mode") || store.get("mode") || "team");
if (store.get("helpLang")) $("helpLang").value = store.get("helpLang");
setMode(startMode === "help" ? "help" : "team");
showTab(qs.get("tab") || store.get("tab") || "tabCopilot");
const savedReq = linkReq || store.get("myRequest");
if (savedReq) {
  fetch(`/api/help/${savedReq}`).then((r) => r.ok ? r.json() : null).then((j) => {
    if (j) { myRequest = { id: j.id, status: j.status, level: j.level }; store.set("myRequest", j.id); showSent(); if (j.status !== "helped") followMyRequest(); }
    else if (linkReq) $("locMsg").textContent = `Help request ${linkReq} was not found.`;
  });
}
loadRunList().then((runs) => {
  if (Number.isFinite(linkLat) && Number.isFinite(linkLon)) {  // a link with a place: check it as the phone did (this opens the right map)
    checkPlace({ lat: linkLat, lon: linkLon, accuracy_m: parseFloat(qs.get("acc")) || null, how: "gps", place_text: "place from the link" }, { showForm: !linkReq });
    return;
  }
  const wanted = qs.get("run");  // e.g. /?run=fb7507902568
  // open the asked-for run, else the one this browser viewed last, else the newest named one (unnamed runs are quick tests)
  const ok = (id) => runs.find((r) => r.run_id === id && r.status === "done");
  const done = ok(wanted) || ok(store.get("run")) || runs.find((r) => r.status === "done" && r.name)
    || runs.find((r) => r.status === "done");
  if (done) { $("runs").value = done.run_id; openRun(done.run_id); }
});
