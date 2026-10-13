// Evidence section under the dashboard. The numbers come from /static/evidence/evidence.json
// (made by tools/make_evidence.py from saved results and the EMSR927 check).
(() => {
  const $ = (id) => document.getElementById(id);
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const day = (iso) => { const [, m, d] = iso.split("-").map(Number); return `${d} ${MONTHS[m - 1]}`; };
  const dayY = (iso) => `${day(iso)} ${iso.slice(0, 4)}`;
  const longDate = (iso) => { const [y, m, d] = iso.split("-").map(Number); return `${d} ${LONG[m - 1]} ${y}`; };
  const pct = (v) => `${Math.round(v * 100)}%`;
  const pct1 = (v) => `${(Math.round(v * 1000) / 10).toFixed(1)}%`;   // 0.998 -> 99.8% (not a rounded-up "1.00")
  const num = (v, d = 0) => Number(v).toLocaleString("en-US", { maximumFractionDigits: d });

  // ---------- the dashboard fills exactly one screen; the evidence starts below it ----------
  const sizeWork = () => {
    const h = innerHeight - $("topbar").offsetHeight - $("kpis").offsetHeight;
    document.documentElement.style.setProperty("--work-h", `${Math.max(420, h)}px`);
  };
  sizeWork();
  addEventListener("resize", sizeWork);
  new ResizeObserver(sizeWork).observe($("kpis"));

  // the map zooms with the mouse wheel only while the page is at the top; lower down, the wheel scrolls the page
  const onScroll = () => {
    const down = scrollY > 8;
    if (typeof map !== "undefined") { if (down) map.scrollWheelZoom.disable(); else map.scrollWheelZoom.enable(); }
    $("evBack").classList.toggle("show", scrollY > innerHeight * 0.7);
  };
  addEventListener("scroll", onScroll, { passive: true });

  // ---------- before / after slider ----------
  const cmp = $("cmp"), range = cmp.querySelector("input");
  const setPos = (v) => cmp.style.setProperty("--pos", `${v}%`);
  range.addEventListener("input", () => { cmp.classList.add("touched"); setPos(range.value); });
  function demoSweep() {  // a slow sweep the first time it is seen, so people notice it can be dragged
    if (cmp.classList.contains("touched") || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const t0 = performance.now(), path = [50, 88, 12, 50];
    const step = (now) => {
      if (cmp.classList.contains("touched")) return;
      const k = Math.min(1, (now - t0) / 3600) * (path.length - 1), i = Math.min(path.length - 2, Math.floor(k)), f = k - i;
      const v = path[i] + (path[i + 1] - path[i]) * (f * f * (3 - 2 * f));
      setPos(v); range.value = v;
      if (k < path.length - 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  // ---------- reveal on scroll ----------
  const io = new IntersectionObserver((entries) => entries.forEach((e) => {
    if (!e.isIntersecting) return;
    e.target.classList.add("in");
    if (e.target.id === "ev-radar") setTimeout(demoSweep, 700);
    io.unobserve(e.target);
  }), { threshold: 0.12 });
  document.querySelectorAll("#evidence .reveal").forEach((el) => io.observe(el));

  // ---------- numbers ----------
  Promise.all([
    fetch("/static/evidence/evidence.json").then((r) => r.json()),
    fetch("/api/runs").then((r) => r.json()).catch(() => []),
  ]).then(([E, runs]) => fill(E, new Set(runs.filter((r) => r.status === "done").map((r) => r.run_id)))).catch(() => {});

  function fill(E, haveRuns) {
    const m = E.main;
    $("evDate").textContent = longDate(m.date);
    $("evZoomKm").textContent = `${E.radar.zoom_km} km`;
    $("evZoomNear").textContent = E.radar.zoom_near;
    $("evRadarDates").innerHTML = E.radar.tracks.map((t) =>
      `<b>Track ${t.track} (${t.direction})</b>: before ${t.before.slice().reverse().map(day).join(", ")} → after ${t.after.map(day).join(", ")} ${t.after[0].slice(0, 4)}` +
      ` · valley floor seen ${t.valley_seen_pct}%${t.valley_seen_pct < 10 ? " (steep slopes hide it from this side, so the other direction is used)" : ""}`).join("<br>");
    if (E.optical) {
      $("evClear").textContent = `${E.optical.clear_valley_pct}%`;
      $("evOptBefore").textContent = dayY(E.optical.before);
      $("evOptAfter").textContent = dayY(E.optical.after);
    }
    const stat = (v, label, sub = "", cls = "") => `<div class="ev-stat ${cls}"><b>${v}</b><span>${label}</span>${sub ? `<small>${sub}</small>` : ""}</div>`;
    $("evMainStats").innerHTML = [
      stat(`${num(m.flood_km2, 2)} km²`, "flooded or covered in debris", `${num(m.debris_km2, 2)} mud and debris · ${num(m.new_water_km2, 2)} new water`),
      stat(num(m.buildings_hit), "buildings hit", `of ${num(m.buildings_total)} in the box`),
      stat(`${num(m.road_km, 1)} km`, "of road under the flood"),
      stat(`${m.bridges_hit} of ${m.bridges_total}`, "bridges reached"),
      stat(m.cut_off, "villages cut off", "no road left to a town or hospital", "alert"),
    ].join("");

    // official check
    const P = E.ems.pooled, rows = E.ems.rows, PW = E.ems.pooled_with_possible;
    const bidur = rows.find((r) => /bidur/i.test(r.place)) || rows[0];
    $("evBig").innerHTML = [
      stat(pct(P.precision), "of the flood we map is real", `precision, all ${rows.length} areas together`, "good"),
      stat(`${bidur.destroyed_found_pct}%`, `of ${num(bidur.ems_destroyed)} destroyed buildings found`, `in ${bidur.place}, the biggest area`, "good"),
      stat(`${bidur.bridges_found} of ${bidur.bridges_ems}`, "damaged bridges on our map", `in ${bidur.place}`, "good"),
      PW ? stat(pct(PW.recall), "of the official flood area found, with the possible-damage zone",
        `confirmed map alone: ${pct(P.recall)} · precision stays ${pct(PW.precision)} · F1 ${PW.f1.toFixed(2)}`, "good")
        : stat(pct(P.recall), "of the official flood area found", "we miss part of the outer edge (honest limit)", "warn"),
    ].join("");
    $("evTable").innerHTML = `<thead><tr><th>Area</th><th>Official km²</th><th>Ours km²</th><th>Precision</th><th>Recall</th>
        <th>F1 (within 20 m)</th><th>+ possible zone: recall</th><th>+ possible zone: F1</th><th>Destroyed buildings found</th><th>Damaged bridges found</th></tr></thead><tbody>` +
      rows.map((r) => `<tr><td>${esc(r.place)}</td><td>${num(r.ems_km2, 2)}</td><td>${num(r.ours_km2, 2)}</td><td class="g">${pct1(r.precision)}</td>
        <td>${pct1(r.recall)}</td><td>${r.f1_20m.toFixed(2)}</td>
        <td class="g">${r.with_possible ? pct1(r.with_possible.recall) : "–"}</td><td class="g">${r.with_possible ? r.with_possible.f1.toFixed(2) : "–"}</td>
        <td>${r.destroyed_found_pct != null ? r.destroyed_found_pct + "%" : "–"}</td>
        <td>${r.bridges_found} / ${r.bridges_ems}</td></tr>`).join("") +
      `<tr class="pooled"><td>All, pooled</td><td>${num(P.ems_km2, 1)}</td><td>${num(P.ours_km2, 1)}</td><td class="g">${pct(P.precision)}</td>
        <td>${pct(P.recall)}</td><td>–</td><td class="g">${PW ? pct(PW.recall) : "–"}</td><td class="g">${PW ? PW.f1.toFixed(2) : "–"}</td><td>–</td><td>–</td></tr></tbody>`;

    // no-flood test
    const C = E.control;
    $("evCtlDate").textContent = longDate(C.date);
    $("evCtlDate2").textContent = dayY(C.date);
    const bar = (label, a, b, unit = "", d = 0) => {
      const w = a > 0 ? Math.max(1.5, (b / a) * 100) : 0;
      return `<div class="ev-bar"><span>${label}</span>
        <div class="row flood"><i style="width:100%"></i><b>${num(a, d)}${unit}</b></div>
        <div class="row dry"><i style="width:${w}%"></i><b>${num(b, d)}${unit}</b></div></div>`;
    };
    $("evBars").innerHTML = `<div class="ev-legend"><span class="flood">flood date</span><span class="dry">dry season</span></div>` + [
      bar("Flooded / debris", m.flood_km2, C.flood_km2, " km²", 2),
      bar("Buildings hit", m.buildings_hit, C.buildings_hit),
      bar("Bridges reached", m.bridges_hit, C.bridges_hit),
      bar("Villages cut off", m.cut_off, C.cut_off),
    ].join("");

    // other floods
    $("evFloods").innerHTML = E.floods.map((f) => {
      const open = haveRuns.has(f.run_id);
      return `<${open ? `a href="/app?run=${f.run_id}" data-run="${f.run_id}"` : "div"} class="ev-flood">
        <img src="/static/evidence/${f.img}" alt="Flood map of ${esc(f.place)}" width="420" height="280" loading="lazy">
        <div><b>${esc(f.place)}</b><small>${esc(f.kind)} · ${dayY(f.date)}</small>
        <p>${num(f.flood_km2, 2)} km² flooded · ${num(f.buildings_hit)} buildings · ${f.bridges_hit} bridges · <span class="${f.cut_off ? "c-red" : ""}">${f.cut_off} cut off</span>${f.detour ? ` · ${f.detour} long detour` : ""}</p>
        ${open ? `<span class="ev-link">Open on the map ↑</span>` : ""}</div></${open ? "a" : "div"}>`;
    }).join("");
    $("evFloods").querySelectorAll("a[data-run]").forEach((a) => a.addEventListener("click", (e) => {
      if (typeof openRun !== "function") return;
      e.preventDefault();
      scrollTo({ top: 0, behavior: "smooth" });
      $("runs").value = a.dataset.run;
      openRun(a.dataset.run);
    }));

    // the AI's own words, then the answer after our code filled the blanks
    if (E.ai) {
      const blanks = esc(E.ai.model_text).replace(/\{([a-z0-9_]+)\}/gi, '<mark>{$1}</mark>');
      const filled = esc(E.ai.answer).replace(/(\d[\d,.]*)/g, '<b class="num">$1</b>');
      $("evAi").innerHTML = `<p class="q">Question: “${esc(E.ai.question)}”</p>
        <div class="ev-ai-row">
          <div class="pane"><h4>1 · What the AI wrote <small>blanks only, no digits</small></h4><p>${blanks}</p></div>
          <div class="arrow" aria-hidden="true">→</div>
          <div class="pane ok"><h4>2 · After our code filled the blanks <small>numbers from the maps</small></h4><p>${filled}</p></div>
        </div>
        <p class="ev-meta">✓ ${E.ai.values} values inserted by our code · 0 digits written by the AI · checked by a rule that rejects any digit the model writes</p>`;
    } else {
      $("evAi").innerHTML = `<p class="ev-meta">Ask the copilot on the dashboard above and press “show the AI's own words” to see the blanks it wrote.</p>`;
    }

    // rescue priority
    const W = E.priority.weights;
    const part = (cls, label, v, w) => `<div class="part ${cls}"><span>${label}</span><i><em style="width:${v}%"></em></i><b>${v}</b><small>× ${w}%</small></div>`;
    $("evPrio").innerHTML = E.priority.top.map((a, i) => `<div class="ev-pcard">
        <div class="top"><span class="rank">${i + 1}</span><b>${esc(a.name)}</b><span class="ne">${esc(a.name_ne || "")}</span><span class="score">${a.priority}</span></div>
        ${a.parts ? part("p-size", "Size", a.parts.size, W.size) + part("p-damage", "Damage", a.parts.damage, W.damage) + part("p-iso", "Isolation", a.parts.isolation, W.isolation) + part("p-far", "Far from help", a.parts.remote, W.remoteness) : ""}
        <small class="facts">${a.buildings} buildings nearby · ${a.hit} hit · ${a.breaks} broken stretches · ${num(a.km_before, 1)} km to help before</small>
      </div>`).join("");
  }
})();
