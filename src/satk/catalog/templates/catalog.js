/* satk catalog: offline browser of a GTA:SA index (built by `satk catalog build`, owner M2-09).
   Data comes as script chunks data/<name>.js that call CAT.add(name, payload): this works from file://
   where fetch()/XHR of local files is blocked. The search index (models, textures, TXDs, zones) loads
   at start; model/texture/zone details load on demand; thumbnails are lazy <img loading="lazy">. */
(function () {
  "use strict";
  const view = document.getElementById("view");
  const qEl = document.getElementById("q");
  const kindsEl = document.getElementById("kinds");
  const footEl = document.getElementById("foot");

  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ESC[c]);
  const num = (n) => Number(n).toLocaleString("en-US");
  const enc = encodeURIComponent;
  const r2 = (v) => (Math.round(v * 100) / 100).toString();

  // ------------------------------------------------------------------ chunk loader
  const parts = Object.create(null);
  const waiting = Object.create(null);
  window.CAT = {
    add(name, data) {
      parts[name] = data;
      const w = waiting[name];
      if (w) { delete waiting[name]; w.ok(data); }
    },
  };
  function load(name) {
    if (name in parts) return Promise.resolve(parts[name]);
    if (waiting[name]) return waiting[name].p;
    const w = {};
    w.p = new Promise((ok, fail) => { w.ok = ok; w.fail = fail; });
    waiting[name] = w;
    const s = document.createElement("script");
    s.src = "data/" + name + ".js";
    const gone = (why) => () => {
      if (waiting[name] === w) { delete waiting[name]; w.fail(new Error(why + ": data/" + name + ".js")); }
    };
    s.onerror = gone("file missing");
    s.onload = gone("file did not call CAT.add");
    document.head.appendChild(s);
    return w.p;
  }

  // ------------------------------------------------------------------ data
  let META, M, T, X, Z, IMG, EXT;
  const NOTHUMB = new Set();
  const ORDER = ["models", "textures", "txds", "zones"];
  const KIND = { models: "Models", textures: "Textures", txds: "TXDs", zones: "Zones" };
  const DFF_FLAGS = ["skin", "hanim", "prelit", "night", "normals", "2dfx", "embedded col", "uvanim", "matfx",
    "reflection", "specular", "breakable", "multi clump"];
  let modelById, texByKey, txdByName, zoneByName, imgByHex;
  const lazy = {};
  let keys = null;

  function txdTex(xi) {
    if (!lazy.txdTex) {
      lazy.txdTex = X.map(() => []);
      T.forEach((r, i) => lazy.txdTex[r[0]].push(i));
    }
    return lazy.txdTex[xi];
  }
  function txdModels(xi) {
    if (!lazy.txdModels) {
      lazy.txdModels = X.map(() => []);
      M.forEach((r, i) => { if (r[3] >= 0) lazy.txdModels[r[3]].push(i); });
    }
    return lazy.txdModels[xi];
  }
  function txdKids(xi) {
    if (!lazy.txdKids) {
      lazy.txdKids = X.map(() => []);
      X.forEach((r, i) => { if (r[2] >= 0 && r[2] !== i) lazy.txdKids[r[2]].push(i); });
    }
    return lazy.txdKids[xi];
  }
  function imgTex(img) {
    if (!lazy.imgTex) {
      lazy.imgTex = new Map();
      T.forEach((r, i) => {
        if (r[6] < 0) return;
        const a = lazy.imgTex.get(r[6]);
        if (a) a.push(i); else lazy.imgTex.set(r[6], [i]);
      });
    }
    return lazy.imgTex.get(img) || [];
  }
  function zonesByArea() {
    if (!lazy.zArea) {
      const area = (r) => (r[8] - r[5]) * (r[9] - r[6]);
      lazy.zArea = Z.map((_, i) => i).sort((a, b) => area(Z[a]) - area(Z[b]) || a - b);
    }
    return lazy.zArea;
  }
  function zoneAt(x, y, z) {  // the smallest navigation zone (info.zon, type 0) containing the point
    for (const i of zonesByArea()) {
      const r = Z[i];
      if (r[3] === 0 && x >= r[5] && x <= r[8] && y >= r[6] && y <= r[9] && z >= r[7] && z <= r[10]) return i;
    }
    return -1;
  }
  function txdChain(xi) {
    const out = [];
    while (xi >= 0 && !out.includes(xi) && out.length < 16) { out.push(xi); xi = X[xi][2]; }
    return out;
  }

  // ------------------------------------------------------------------ thumbnails and cards
  const mThumb = (id) => `t/m/${id >> 7}/${id}.${EXT}`;
  const xThumb = (img) => `t/x/${IMG[img].slice(0, 2)}/${IMG[img]}.${EXT}`;
  const hasTexThumb = (r) => r[6] >= 0 && !NOTHUMB.has(r[6]);
  const lazyImg = (src) => `<img loading="lazy" decoding="async" src="${src}" alt="">`;

  function thumbModel(r) {
    if (r[4]) return `<div class="th">${lazyImg(mThumb(r[0]))}</div>`;
    return `<div class="th"><span class="no">${r[8] == null ? "no DFF" : "no preview"}</span></div>`;
  }
  function thumbTex(r) {
    if (!hasTexThumb(r)) return `<div class="th"><span class="no">no pixels</span></div>`;
    const cls = (r[5] ? " alpha" : "") + (Math.max(r[2], r[3]) < 128 ? " px" : "");
    return `<div class="th${cls}">${lazyImg(xThumb(r[6]))}</div>`;
  }
  function thumbTxd(xi) {
    const list = txdTex(xi).filter((ti) => hasTexThumb(T[ti])).slice(0, 4);
    if (!list.length) return `<div class="th"><span class="no">no textures</span></div>`;
    if (list.length === 1) return thumbTex(T[list[0]]);
    return `<div class="th mos">${list.map((ti) => lazyImg(xThumb(T[ti][6]))).join("")}</div>`;
  }
  function zoneView(r, pad) {  // square world window around a zone: [x0, ytop, span]
    const w = r[8] - r[5], h = r[9] - r[6];
    const span = Math.min(6000, Math.max(250, Math.max(w, h) * pad));
    const cx = (r[5] + r[8]) / 2, cy = (r[6] + r[9]) / 2;
    const x0 = Math.min(3000 - span, Math.max(-3000, cx - span / 2));
    const yt = Math.max(-3000 + span, Math.min(3000, cy + span / 2));
    return [x0, yt, span];
  }
  function thumbZone(r) {
    const [x0, yt, span] = zoneView(r, 1.6);
    const k = 6000 / span;
    let style = "";
    if (META.map) {
      const px = k > 1.0001 ? ((x0 + 3000) / 6000 * k) / (k - 1) * 100 : 0;
      const py = k > 1.0001 ? ((3000 - yt) / 6000 * k) / (k - 1) * 100 : 0;
      style = `background-image:url('${META.map}');background-size:${(k * 100).toFixed(2)}%;` +
        `background-position:${px.toFixed(2)}% ${py.toFixed(2)}%`;
    }
    const L = (r[5] - x0) / span * 100, Tp = (yt - r[9]) / span * 100;
    const W = (r[8] - r[5]) / span * 100, H = (r[9] - r[6]) / span * 100;
    return `<div class="th zone" style="${style}"><i style="left:${L.toFixed(2)}%;top:${Tp.toFixed(2)}%;` +
      `width:${W.toFixed(2)}%;height:${H.toFixed(2)}%"></i></div>`;
  }
  const badge = (b) => (b ? `<span class="badge">${esc(b)}</span>` : "");
  const texHref = (ti) => `#tex/${enc(X[T[ti][0]][0])}/${enc(T[ti][1])}`;
  const texSid = (ti) => `tex:${X[T[ti][0]][0].toLowerCase()}/${T[ti][1].toLowerCase()}`;

  function cardModel(i, b) {
    const r = M[i];
    return `<a class="card" href="#model/${r[0]}">${thumbModel(r)}${badge(b)}<b title="${esc(r[1])}">${esc(r[1])}</b>` +
      `<small>model:${r[0]} · ${esc(META.secs[r[2]])}${r[5] ? " · " + num(r[5]) + "×" : ""}</small></a>`;
  }
  function cardTex(ti, b) {
    const r = T[ti];
    return `<a class="card" href="${texHref(ti)}">${thumbTex(r)}${badge(b)}<b title="${esc(r[1])}">${esc(r[1])}</b>` +
      `<small title="${esc(X[r[0]][0])}">${esc(X[r[0]][0])} · ${r[2]}×${r[3]} ${esc(META.fmts[r[4]])}</small></a>`;
  }
  function cardTxd(xi) {
    const r = X[xi];
    return `<a class="card" href="#txd/${enc(r[0])}">${thumbTxd(xi)}<b title="${esc(r[0])}">${esc(r[0])}</b>` +
      `<small>${num(r[1])} tex · ${num(r[4])} models · ${esc(META.archives[r[3]])}</small></a>`;
  }
  function cardZone(zi, b) {
    const r = Z[zi];
    return `<a class="card" href="#zone/${enc(r[0])}">${thumbZone(r)}${badge(b)}<b title="${esc(r[2] || r[0])}">` +
      `${esc(r[2] || r[0])}</b><small>zone:${esc(r[0].toLowerCase())} · ${num(r[11])} objects</small></a>`;
  }
  const CARD = { models: cardModel, textures: cardTex, txds: cardTxd, zones: cardZone };

  // ------------------------------------------------------------------ search
  function buildKeys() {
    keys = {
      models: M.map((r) => r[1].toLowerCase() + " " + r[0]),
      mname: M.map((r) => r[1].toLowerCase()),
      textures: T.map((r) => (X[r[0]][0] + "/" + r[1]).toLowerCase()),
      tname: T.map((r) => r[1].toLowerCase()),
      txds: X.map((r) => r[0].toLowerCase()),
      zones: Z.map((r) => [r[0], r[1] || "", r[2] || ""].join(" ").toLowerCase()),
      zparts: Z.map((r) => [r[0], r[1] || "", r[2] || ""].map((s) => s.toLowerCase())),
      zorder: Z.map((_, i) => i).sort((a, b) => (Z[a][2] || Z[a][0]).localeCompare(Z[b][2] || Z[b][0]) || a - b),
    };
  }
  const rank = (name, q) => (name === q ? 0 : name.startsWith(q) ? 1 : name.includes(q) ? 2 : 3);
  function matchAll(key, n, toks, keep) {
    const hits = [];
    for (let i = 0; i < n; i++) {
      if (keep && !keep(i)) continue;
      const k = key[i];
      let ok = true;
      for (const t of toks) if (k.indexOf(t) < 0) { ok = false; break; }
      if (ok) hits.push(i);
    }
    return hits;
  }
  function sortBy(hits, score) {
    const sc = new Map(hits.map((i) => [i, score(i)]));
    return hits.sort((a, b) => sc.get(a) - sc.get(b) || a - b);
  }
  function runSearch(q, sec) {
    if (!keys) buildKeys();
    const toks = q.toLowerCase().split(/\s+/).filter(Boolean);
    const q0 = toks[0] || "";
    const keepM = sec >= 0 ? (i) => M[i][2] === sec : null;
    if (!toks.length) {
      return {
        models: M.map((_, i) => i).filter((i) => !keepM || keepM(i)),
        textures: T.map((_, i) => i), txds: X.map((_, i) => i), zones: keys.zorder.slice(),
      };
    }
    const id = /^\d+$/.test(q0) && toks.length === 1 ? +q0 : null;
    const models = sortBy(matchAll(keys.models, M.length, toks, keepM),
      (i) => (id !== null && M[i][0] === id ? -1 : rank(keys.mname[i], q0)) * 1000 + Math.min(999, keys.mname[i].length));
    const textures = sortBy(matchAll(keys.textures, T.length, toks),
      (i) => rank(keys.tname[i], q0) * 1000 + Math.min(999, keys.tname[i].length));
    const txds = sortBy(matchAll(keys.txds, X.length, toks), (i) => rank(keys.txds[i], q0) * 1000 + keys.txds[i].length);
    const zones = sortBy(matchAll(keys.zones, Z.length, toks),
      (i) => Math.min(...keys.zparts[i].map((p) => rank(p, q0))) * 1000 + keys.zones[i].length);
    return { models, textures, txds, zones };
  }

  // ------------------------------------------------------------------ routing and state
  let st = { q: "", k: "all", s: -1 };
  let lastCounts = null;
  let token = 0;
  let observers = [];
  let onSearchPage = true;

  function parseState(h) {
    const p = new URLSearchParams(h);
    const k = p.get("k");
    return { q: p.get("q") || "", k: ORDER.includes(k) ? k : "all", s: p.has("s") ? +p.get("s") : -1 };
  }
  function stateHash(s) {
    const p = new URLSearchParams();
    if (s.q) p.set("q", s.q);
    if (s.k !== "all") p.set("k", s.k);
    if (s.k === "models" && s.s >= 0) p.set("s", String(s.s));
    const x = p.toString();
    return "#" + x;
  }
  function navigate(h, replace) {
    if (replace) { history.replaceState(null, "", h); route(); return; }
    if (location.hash === h || (h === "#" && !location.hash)) route(); else location.hash = h;
  }
  function route() {
    token++;
    observers.forEach((o) => o.disconnect());
    observers = [];
    const h = location.hash.replace(/^#/, "");
    const m = /^(model|tex|txd|zone)\/(.*)$/.exec(h);
    if (!m) { onSearchPage = true; showSearch(parseState(h)); return; }
    onSearchPage = false;
    renderKinds();
    window.scrollTo(0, 0);
    const segs = m[2].split("/").map((s) => { try { return decodeURIComponent(s); } catch (e) { return s; } });
    const t = token;
    const fail = (e) => { if (t === token) view.innerHTML = `<div class="empty err">${esc(e.message || e)}</div>`; };
    try {
      if (m[1] === "model") showModel(+segs[0], t).catch(fail);
      else if (m[1] === "tex") showTex(texByKey.get(segs.join("/").toLowerCase()), segs.join("/"), t).catch(fail);
      else if (m[1] === "txd") showTxd(txdByName.get(segs[0].toLowerCase()), segs[0]);
      else showZone(zoneByName.get(segs[0].toLowerCase()), segs[0], t).catch(fail);
    } catch (e) { fail(e); }
  }

  function renderKinds(counts) {
    if (counts) lastCounts = counts;
    const c = lastCounts;
    const on = (k) => (onSearchPage && st.k === k ? " on" : "");
    let h = `<span class="chip${on("all")}" data-k="all">All</span>`;
    for (const k of ORDER) h += `<span class="chip${on(k)}" data-k="${k}">${KIND[k]}<b>${c ? num(c[k]) : ""}</b></span>`;
    if (onSearchPage && st.k === "models") {
      h += `<select class="chip" id="sec" title="IDE section"><option value="-1">all sections</option>` +
        META.secs.map((s, i) => `<option value="${i}"${st.s === i ? " selected" : ""}>${esc(s)}</option>`).join("") + `</select>`;
    }
    kindsEl.innerHTML = h;
  }

  function paged(grid, list, card) {
    const more = grid.nextElementSibling;
    let n = 0;
    const step = () => {
      const end = Math.min(list.length, n + 120);
      let h = "";
      for (; n < end; n++) h += card(list[n]);
      grid.insertAdjacentHTML("beforeend", h);
      more.textContent = n < list.length ? `showing ${num(n)} of ${num(list.length)}` : "";
    };
    step();
    if (n < list.length) {
      const ob = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting) && n < list.length) step(); },
        { rootMargin: "900px" });
      ob.observe(more);
      observers.push(ob);
    }
  }
  const gridHTML = () => `<div class="grid"></div><div class="more"></div>`;

  function statsHTML() {
    const c = META.counts;
    const tile = (n, l) => `<div class="stat"><b>${num(n)}</b><span>${l}</span></div>`;
    return `<div class="stats">${tile(c.models, "models")}${tile(c.textures, "textures")}${tile(c.images, "unique images")}` +
      `${tile(c.txds, "TXDs")}${tile(c.zones, "zones")}${tile(c.placements, "placements")}</div>` +
      (META.limited ? `<p class="hint">Sample catalog (--limit): only the first models and their textures.</p>` : "");
  }

  function showSearch(s) {
    st = s;
    if (qEl.value !== s.q && document.activeElement !== qEl) qEl.value = s.q;
    const res = runSearch(s.q, s.s);
    renderKinds({ models: res.models.length, textures: res.textures.length, txds: res.txds.length, zones: res.zones.length });
    if (s.k === "all") {
      let h = s.q ? "" : statsHTML();
      for (const k of ORDER) {
        const list = res[k];
        if (!list.length) continue;
        const all = list.length > 18 ? ` <a href="${stateHash({ ...s, k })}">all ${num(list.length)} →</a>` : "";
        h += `<h2>${KIND[k]} <span class="hint">${num(list.length)}</span>${all}</h2>` +
          `<div class="grid">${list.slice(0, 18).map((i) => CARD[k](i)).join("")}</div>`;
      }
      view.innerHTML = h || `<div class="empty">Nothing found for &quot;${esc(s.q)}&quot;</div>`;
      return;
    }
    const list = res[s.k];
    if (!list.length) { view.innerHTML = `<div class="empty">Nothing found for &quot;${esc(s.q)}&quot;</div>`; return; }
    view.innerHTML = gridHTML();
    paged(view.querySelector(".grid"), list, (i) => CARD[s.k](i));
  }

  // ------------------------------------------------------------------ helpers of detail pages
  const back = `<a class="back" href="#" onclick="history.length>1?history.back():location.hash='';return false">← back</a>`;
  const kv = (rows) => `<dl class="kv">${rows.filter((r) => r && r[1] !== "" && r[1] != null)
    .map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
  const tags = (list) => list.map((t) => `<span class="tag">${esc(t)}</span>`).join("");
  function cmds(list) {
    return `<div class="cmds">${list.map((c) => `<div class="cmd"><code>${esc(c)}</code>` +
      `<button class="copy" data-c="${esc(c)}">copy</button></div>`).join("")}</div>`;
  }
  const notFound = (what) => { view.innerHTML = `${back}<div class="empty">Not found: ${esc(what)}</div>`; };
  const txdLink = (xi) => `<a href="#txd/${enc(X[xi][0])}">${esc(X[xi][0])}</a>`;
  const zoneLink = (zi) => `<a href="#zone/${enc(Z[zi][0])}">${esc(Z[zi][2] || Z[zi][0])}</a>`;

  function copy(text, btn) {
    const done = () => { btn.textContent = "copied"; setTimeout(() => { btn.textContent = "copy"; }, 1200); };
    const fallback = () => {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); done(); } catch (e) { /* ignore */ }
      ta.remove();
    };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback();
  }

  // ------------------------------------------------------------------ map widget
  function mapHTML() {
    return `<div class="map"><div class="mapin">${META.map ? `<img src="${META.map}" alt="" draggable="false">` : ""}` +
      `<svg viewBox="-3000 -3000 6000 6000" preserveAspectRatio="none"><g transform="scale(1,-1)">` +
      `<g class="gl"></g><g class="zs"></g><g class="pts"></g></g></svg></div>` +
      `<div class="mapui"><button data-z="1" title="Zoom in">+</button><button data-z="-1" title="Zoom out">−</button>` +
      `<button data-z="0" title="Fit">⤢</button></div><div class="mapxy"></div></div>`;
  }
  // pts: [[x, y, title, cls]], rects: [[x0, y0, x1, y1]]. View state = world centre (cx, cy) + scale s
  // (1 = the whole 6 km map fits the square), so a resize keeps the same place in view.
  function makeMap(root, pts, rects, onPick) {
    const inner = root.querySelector(".mapin");
    const gGrid = root.querySelector(".gl"), gZ = root.querySelector(".zs"), gP = root.querySelector(".pts");
    const xy = root.querySelector(".mapxy");
    const SMAX = 24;
    let s = 1, cx = 0, cy = 0, home = null, sel = -1;
    let g = "";
    for (let v = -2000; v <= 2000; v += 1000) g += `<line x1="${v}" y1="-3000" x2="${v}" y2="3000"/><line x1="-3000" y1="${v}" x2="3000" y2="${v}"/>`;
    gGrid.innerHTML = g;
    gZ.innerHTML = rects.map((r) => `<rect class="zr" x="${r[0]}" y="${r[1]}" width="${r[2] - r[0]}" height="${r[3] - r[1]}"/>`).join("");
    gP.innerHTML = pts.map((p, i) => `<circle class="pt ${p[3] || ""}" data-i="${i}" cx="${p[0]}" cy="${p[1]}"><title>${esc(p[2])}</title></circle>`).join("");
    const circles = Array.from(gP.children);
    const W = () => root.clientWidth || 600;
    const origin = (w) => [w / 2 - s * (cx + 3000) / 6000 * w, w / 2 - s * (3000 - cy) / 6000 * w];
    function apply() {
      const w = W();
      const half = 3000 / s;
      cx = Math.max(-3000 + half, Math.min(3000 - half, cx));
      cy = Math.max(-3000 + half, Math.min(3000 - half, cy));
      const [tx, ty] = origin(w);
      inner.style.transform = `translate(${tx}px,${ty}px) scale(${s})`;
      const u = 6000 / (w * s);
      gGrid.setAttribute("stroke-width", u);
      gZ.setAttribute("stroke-width", 2 * u);
      gP.setAttribute("stroke-width", u);
      const rr = Math.max(pts.length > 200 ? 2.2 : 3.5, Math.min(5, 1.5 + s / 2)) * u;
      circles.forEach((c, i) => c.setAttribute("r", i === sel ? 2 * rr : rr));
    }
    function toWorld(px, py) {
      const w = W();
      const [tx, ty] = origin(w);
      return [((px - tx) / s) / w * 6000 - 3000, 3000 - ((py - ty) / s) / w * 6000];
    }
    function center(x, y, ns) {
      s = Math.max(1, Math.min(SMAX, ns));
      cx = x;
      cy = y;
      apply();
    }
    function fit(b) {
      home = b || null;
      if (!b) { center(0, 0, 1); return; }
      const span = Math.min(6000, Math.max(700, Math.max(b[2] - b[0], b[3] - b[1]) * 1.3 + 80));
      center((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, 6000 / span);
    }
    function zoomAt(f, px, py) {
      const [wx, wy] = toWorld(px, py);
      const ns = Math.max(1, Math.min(SMAX, s * f));
      cx = wx + (cx - wx) * s / ns;
      cy = wy + (cy - wy) * s / ns;
      s = ns;
      apply();
    }
    root.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = root.getBoundingClientRect();
      zoomAt(e.deltaY < 0 ? 1.25 : 0.8, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    let drag = null;
    root.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".mapui")) return;
      drag = { x: e.clientX, y: e.clientY, cx, cy, moved: false };
      root.setPointerCapture(e.pointerId);
    });
    root.addEventListener("pointermove", (e) => {
      const r = root.getBoundingClientRect();
      const [wx, wy] = toWorld(e.clientX - r.left, e.clientY - r.top);
      xy.textContent = `x ${wx.toFixed(0)}, y ${wy.toFixed(0)}`;
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) { drag.moved = true; root.classList.add("drag"); }
      const k = 6000 / (W() * s);
      cx = drag.cx - dx * k;
      cy = drag.cy + dy * k;
      apply();
    });
    const end = (e) => {
      if (!drag) return;
      const moved = drag.moved;
      drag = null;
      root.classList.remove("drag");
      if (!moved && onPick) {
        const c = document.elementFromPoint(e.clientX, e.clientY);
        if (c && c.matches && c.matches("circle.pt")) onPick(+c.dataset.i);
      }
    };
    root.addEventListener("pointerup", end);
    root.addEventListener("pointercancel", () => { drag = null; root.classList.remove("drag"); });
    root.addEventListener("pointerleave", () => { if (!drag) xy.textContent = ""; });
    root.querySelector(".mapui").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      const z = +b.dataset.z;
      if (z === 0) fit(home); else zoomAt(z > 0 ? 1.6 : 1 / 1.6, W() / 2, W() / 2);
    });
    if (window.ResizeObserver) new ResizeObserver(() => apply()).observe(root);
    return {
      fit,
      view: () => ({ s, cx, cy }),
      select(i) {
        if (sel >= 0 && circles[sel]) circles[sel].classList.remove("sel");
        sel = i;
        const c = circles[i];
        if (!c) { apply(); return; }
        c.classList.add("sel");
        gP.appendChild(c);  // draw on top
        center(pts[i][0], pts[i][1], Math.max(s, 8));
      },
    };
  }
  function bounds(pts) {
    if (!pts.length) return null;
    let b = [Infinity, Infinity, -Infinity, -Infinity];
    for (const p of pts) b = [Math.min(b[0], p[0]), Math.min(b[1], p[1]), Math.max(b[2], p[0]), Math.max(b[3], p[1])];
    return b;
  }

  // ------------------------------------------------------------------ model page
  async function showModel(id, t) {
    const i = modelById.get(id);
    if (i === undefined) { notFound("model:" + id); return; }
    const r = M[i];
    view.innerHTML = `<div class="loading">Loading model:${id}…</div>`;
    const ch = await load("m/" + (id >> META.mchunk));
    if (t !== token) return;
    const d = ch[id] || {};
    const sec = META.secs[r[2]];
    const rows = [["Section", `${esc(sec)} · <span class="mono">${esc(d.i || "")}</span>${d.l && d.l !== "vanilla" ? " · layer " + esc(d.l) : ""}`]];
    if (r[3] >= 0) rows.push(["TXD", txdChain(r[3]).map(txdLink).join(" → ")]);
    else if (d.tn) rows.push(["TXD", `${esc(d.tn)} <span class="hint">(not in the archives)</span>`]);
    if (d.d) {
      const [dn, tris, verts, geoms, mats, fl, bsr, bmin, bmax] = d.d;
      const flags = DFF_FLAGS.filter((_, b) => fl & (1 << b));
      rows.push(["DFF", `<span class="mono">${esc(dn)}.dff</span> · ${num(tris || 0)} tris · ${num(verts || 0)} verts · ` +
        `${geoms} geometries · ${mats} materials${bsr != null ? " · radius " + bsr + " m" : ""}<br>${tags(flags)}`]);
      if (bmin && bmax && bmin[0] != null) {
        rows.push(["Size", `${r2(bmax[0] - bmin[0])} × ${r2(bmax[1] - bmin[1])} × ${r2(bmax[2] - bmin[2])} m ` +
          `<span class="hint">(min ${bmin.join(", ")}; max ${bmax.join(", ")})</span>`]);
      }
    } else rows.push(["DFF", `<span class="hint">not in the game archives</span>`]);
    if (d.c) {
      const [cn, via, ver, sph, box, faces] = d.c;
      rows.push(["COL", `${esc(cn || "")} <span class="hint">${esc(via || "")}${ver ? " · COL" + ver : ""}</span>` +
        `${sph != null ? ` · ${sph} spheres · ${box} boxes · ${faces} faces` : ""}`]);
    }
    if (d.dr != null) rows.push(["Draw distance", `${d.dr} m`]);
    if (d.f != null) rows.push(["IDE flags", `${d.f} <span class="hint mono">0x${Number(d.f).toString(16)}</span>`]);
    if (d.tm) rows.push(["Time", `${d.tm[0]}–${d.tm[1]} h`]);
    if (d.an) rows.push(["Animations", esc(d.an)]);
    if (d.ex && typeof d.ex === "object") rows.push(["Parameters", tags(Object.entries(d.ex).map(([k, v]) => `${k}: ${v}`))]);
    rows.push(["Placements", r[5] ? num(r[5]) : "none"]);

    const pts = (d.p || []).map((p) => {
      const sid = `inst:${META.ipls[p[0]]}#${p[1]}`;
      return [p[2], p[3], sid + (p[5] ? ` · interior ${p[5]}` : "") + (p[6] ? " · LOD" : ""), p[5] ? "int" : (p[6] ? "lod" : ""), p, sid];
    });
    const c = [`satk asset get model:${id}`, `satk model image model:${id}`, `satk asset export model:${id} --format glb`];
    if (pts.length) c.push(`satk view goto --id ${pts[0][5]}`);
    const big = r[4] ? `<img src="${mThumb(id)}" alt="">` : `<span class="hint">${d.d ? "preview failed" : "no DFF"}</span>`;
    let h = `${back}<div class="hero"><div class="big">${big}</div><div class="info"><h1>${esc(r[1])}</h1>` +
      `<div class="sid mono">model:${id}</div>${kv(rows)}</div></div><h2>satk commands</h2>${cmds(c)}`;
    const tx = d.tx || [];
    h += `<h2>Textures <span class="hint">${num(tx.length)}${r[7] ? `, missing: ${r[7]}` : ""}</span></h2>`;
    h += tx.length ? `<div class="grid" id="mtex"></div>` : `<p class="hint">Materials without textures.</p>`;
    if (pts.length) {
      h += `<h2>On the map <span class="hint">${num(r[5])}${pts.length < r[5] ? `, showing ${num(pts.length)}` : ""}</span></h2>` +
        `<div class="split"><div>${mapHTML()}<p class="hint">Wheel: zoom; drag: pan. ` +
        `Cyan: exterior, violet: interiors, blue: LOD.</p></div><div class="tablewrap"><table class="t"><thead><tr>` +
        `<th>SID</th><th>x, y, z</th><th>zone</th><th></th></tr></thead><tbody id="ptab"></tbody></table></div></div>`;
    }
    view.innerHTML = h;
    if (tx.length) {
      view.querySelector("#mtex").innerHTML = tx.map(([name, via, uses, ti]) => {
        const b = `×${uses}` + (via !== "own" ? ` · ${via}` : "");
        if (ti >= 0) return cardTex(ti, b);
        return `<div class="card"><div class="th"><span class="no">not in TXD</span></div><b>${esc(name)}</b>` +
          `<small>${esc(via)} · ×${uses}</small></div>`;
      }).join("");
    }
    if (pts.length) {
      const tb = view.querySelector("#ptab");
      const rowsH = pts.map((p, k) => {
        const zi = zoneAt(p[4][2], p[4][3], p[4][4]);
        return `<tr class="row" data-i="${k}"><td class="mono">${esc(p[5])}</td><td class="mono">${p[4][2]}, ${p[4][3]}, ${p[4][4]}</td>` +
          `<td>${zi >= 0 ? esc(Z[zi][2] || Z[zi][0]) : ""}</td><td class="hint">${p[4][5] ? "int. " + p[4][5] : ""}${p[4][6] ? " LOD" : ""}</td></tr>`;
      });
      tb.innerHTML = rowsH.join("");
      const ext = pts.filter((p) => !p[4][5]);
      const mp = makeMap(view.querySelector(".map"), pts, [], (k) => pick(k, true));
      let selRow = null;
      const pick = (k, fromMap) => {
        if (selRow) selRow.classList.remove("sel");
        selRow = tb.querySelector(`tr[data-i="${k}"]`);
        if (selRow) {
          selRow.classList.add("sel");
          if (fromMap) selRow.scrollIntoView({ block: "nearest" });
        }
        mp.select(k);
      };
      tb.addEventListener("click", (e) => { const tr = e.target.closest("tr.row"); if (tr) pick(+tr.dataset.i, false); });
      mp.fit(bounds(ext.length ? ext : pts));
    }
  }

  // ------------------------------------------------------------------ texture page
  async function showTex(ti, what, t) {
    if (ti === undefined) { notFound("tex:" + what); return; }
    const r = T[ti];
    const sid = texSid(ti);
    const img = r[6];
    const hex = img >= 0 ? IMG[img] : null;
    const small = Math.max(r[2], r[3]) < 256;
    let big = `<span class="hint">no pixels</span>`;
    if (hasTexThumb(r)) {
      big = `<img src="${xThumb(img)}" alt="">`;
      if (META.full && hex) {
        big += `<img class="full" src="${META.full}${hex.slice(0, 2)}/${hex}.png" alt="" style="display:none" ` +
          `onload="this.style.display='';this.previousElementSibling.remove()" onerror="this.remove()">`;
      }
    }
    const xr = X[r[0]];
    const rows = [
      ["TXD", txdChain(r[0]).map(txdLink).join(" → ") + ` <span class="hint">${esc(META.archives[xr[3]])}</span>`],
      ["Size", `${r[2]} × ${r[3]}`], ["Format", esc(META.fmts[r[4]])], ["Alpha", r[5] ? "yes" : "no"],
      r[8] ? ["Mask", esc(r[8])] : null,
      hex ? ["Pixels", `<span class="mono">pix:${hex}</span>`] : null,
      ["Models", num(r[7])],
    ];
    const c = [`satk texture image ${sid} --size 0`, `satk asset refs ${sid} --rel models`];
    const same = img >= 0 ? imgTex(img).filter((k) => k !== ti) : [];
    let h = `${back}<div class="hero"><div class="big${r[5] ? " alpha" : ""}${small ? " px" : ""}">${big}</div>` +
      `<div class="info"><h1>${esc(r[1])}</h1><div class="sid mono">${esc(sid)}</div>${kv(rows)}</div></div>` +
      `<h2>satk commands</h2>${cmds(c)}`;
    if (r[7]) h += `<h2>Models <span class="hint">${num(r[7])}</span></h2><div class="grid" id="tm"></div><div class="more"></div>`;
    if (same.length) h += `<h2>Same pixels <span class="hint">${num(same.length)}</span></h2><div class="grid" id="ts"></div><div class="more"></div>`;
    view.innerHTML = h;
    if (same.length) paged(view.querySelector("#ts"), same, (k) => cardTex(k));
    if (r[7]) {
      const ch = await load("t/" + (ti >> META.tchunk));
      if (t !== token) return;
      const list = (ch[ti] || []).filter((e) => modelById.has(e[0]));
      paged(view.querySelector("#tm"), list, (e) => cardModel(modelById.get(e[0]), `×${e[1]}`));
    }
  }

  // ------------------------------------------------------------------ TXD page
  function showTxd(xi, what) {
    if (xi === undefined) { notFound("txd:" + what); return; }
    const r = X[xi];
    const tex = txdTex(xi);
    const models = txdModels(xi);
    const kids = txdKids(xi);
    const rows = [
      ["Archive", `<span class="mono">${esc(META.archives[r[3]])}</span>${META.nss[r[5]] !== "main" ? ` <span class="hint">${esc(META.nss[r[5]])}</span>` : ""}`],
      r[2] >= 0 ? ["Parent", txdChain(r[2]).map(txdLink).join(" → ")] : null,
      kids.length ? ["Children", kids.slice(0, 60).map(txdLink).join(", ") + (kids.length > 60 ? ` … ${num(kids.length - 60)} more` : "")] : null,
      ["Textures", num(tex.length)], ["Models", num(models.length)],
    ];
    const name = r[0].toLowerCase();
    const c = [`satk texture image txd:${name} --mode sheet`, `satk asset refs txd:${name} --rel models`, `satk asset get txd:${name}`];
    let h = `${back}<div class="hero"><div class="big">${thumbTxd(xi)}</div>` +
      `<div class="info"><h1>${esc(r[0])}</h1><div class="sid mono">txd:${esc(name)}</div>${kv(rows)}</div></div>` +
      `<h2>satk commands</h2>${cmds(c)}`;
    if (tex.length) h += `<h2>Textures <span class="hint">${num(tex.length)}</span></h2><div class="grid" id="xt"></div><div class="more"></div>`;
    if (models.length) h += `<h2>Models using this TXD <span class="hint">${num(models.length)}</span></h2><div class="grid" id="xm"></div><div class="more"></div>`;
    view.innerHTML = h;
    if (tex.length) paged(view.querySelector("#xt"), tex, (k) => cardTex(k));
    if (models.length) paged(view.querySelector("#xm"), models, (k) => cardModel(k));
  }

  // ------------------------------------------------------------------ zone page
  async function showZone(zi, what, t) {
    if (zi === undefined) { notFound("zone:" + what); return; }
    const r = Z[zi];
    const name = r[0].toLowerCase();
    const cx = (r[5] + r[8]) / 2, cy = (r[6] + r[9]) / 2;
    const area = (z) => (z[8] - z[5]) * (z[9] - z[6]);
    const parents = Z.map((_, i) => i).filter((i) => i !== zi && area(Z[i]) > area(r) &&
      cx >= Z[i][5] && cx <= Z[i][8] && cy >= Z[i][6] && cy <= Z[i][9]).sort((a, b) => area(Z[a]) - area(Z[b]));
    const inner = Z.map((_, i) => i).filter((i) => i !== zi && area(Z[i]) < area(r) &&
      Z[i][5] >= r[5] && Z[i][8] <= r[8] && Z[i][6] >= r[6] && Z[i][9] <= r[9]);
    const span = Math.round(Math.max(r[8] - r[5], r[9] - r[6]) * 1.3);
    const rows = [
      ["Name", esc(r[2] || "—")], ["GXT label", `<span class="mono">${esc(r[1] || "")}</span>`],
      ["Type", `${r[3]}${r[3] === 0 ? " · navigation (info.zon)" : r[3] === 3 ? " · map (map.zon)" : ""} · level ${r[4]}`],
      ["Bounds", `<span class="mono">${r2(r[5])}, ${r2(r[6])}, ${r2(r[7])} … ${r2(r[8])}, ${r2(r[9])}, ${r2(r[10])}</span>`],
      ["Size", `${num(Math.round(r[8] - r[5]))} × ${num(Math.round(r[9] - r[6]))} m`],
      ["Objects", `${num(r[11])} <span class="hint">(${num(r[12])} models; exterior and HD only)</span>`],
      parents.length ? ["Part of", parents.slice(0, 6).map(zoneLink).join(" · ")] : null,
      inner.length ? ["Contains", inner.slice(0, 40).map(zoneLink).join(" · ") + (inner.length > 40 ? " …" : "")] : null,
    ];
    const c = [`satk view goto --id zone:${name}`, `satk map image --center ${Math.round(cx)},${Math.round(cy)} --span ${Math.min(8000, Math.max(100, span))} --layers inst,zone`,
      `satk asset get zone:${name}`];
    let h = `${back}<div class="hero"><div class="big">${thumbZone(r)}</div>` +
      `<div class="info"><h1>${esc(r[2] || r[0])}</h1><div class="sid mono">zone:${esc(name)}</div>${kv(rows)}</div></div>` +
      `<h2>satk commands</h2>${cmds(c)}<h2>On the map</h2><div class="split"><div>${mapHTML()}</div><div id="zm"></div></div>`;
    view.innerHTML = h;
    const mp = makeMap(view.querySelector(".map"), [], [[r[5], r[6], r[8], r[9]]], null);
    mp.fit([r[5], r[6], r[8], r[9]]);
    if (r[11]) {
      const zm = view.querySelector("#zm");
      zm.innerHTML = `<p class="hint">Most placed models:</p>${gridHTML()}`;
      const ch = await load("z");
      if (t !== token) return;
      const list = (ch[zi] || []).filter((e) => modelById.has(e[0]));
      paged(zm.querySelector(".grid"), list, (e) => cardModel(modelById.get(e[0]), `×${e[1]}`));
    }
  }

  // ------------------------------------------------------------------ input
  let timer = 0;
  qEl.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (!M) return;
      navigate(stateHash({ ...st, q: qEl.value.trim() }), onSearchPage);
    }, 120);
  });
  function jump(v) {
    const m = /^(model|dff|tex|pix|txd|zone):(.+)$/i.exec(v.trim());
    if (!m) {
      if (/^\d+$/.test(v.trim()) && modelById.has(+v.trim())) return "#model/" + (+v.trim());
      return null;
    }
    const kind = m[1].toLowerCase(), key = m[2].toLowerCase();
    if (kind === "model" || kind === "dff") {
      if (/^\d+$/.test(key) && modelById.has(+key)) return "#model/" + (+key);
      const i = M.findIndex((r) => r[1].toLowerCase() === key);
      return i >= 0 ? "#model/" + M[i][0] : null;
    }
    if (kind === "tex") return texByKey.has(key) ? texHref(texByKey.get(key)) : null;
    if (kind === "pix") {
      const img = imgByHex.get(key);
      const list = img === undefined ? [] : imgTex(img);
      return list.length ? texHref(list[0]) : null;
    }
    if (kind === "txd") return txdByName.has(key) ? "#txd/" + enc(X[txdByName.get(key)][0]) : null;
    return zoneByName.has(key) ? "#zone/" + enc(Z[zoneByName.get(key)][0]) : null;
  }
  qEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && M) {
      const h = jump(qEl.value);
      if (h) { clearTimeout(timer); navigate(h, false); }
    } else if (e.key === "Escape") { qEl.value = ""; qEl.dispatchEvent(new Event("input")); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== qEl && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      e.preventDefault();
      qEl.focus();
      qEl.select();
    }
  });
  kindsEl.addEventListener("click", (e) => {
    const c = e.target.closest("[data-k]");
    if (c && M) navigate(stateHash({ ...st, k: c.dataset.k }), false);
  });
  kindsEl.addEventListener("change", (e) => {
    if (e.target.id === "sec") navigate(stateHash({ ...st, s: +e.target.value }), true);
  });
  document.addEventListener("click", (e) => {
    const b = e.target.closest(".copy");
    if (b) copy(b.dataset.c, b);
  });
  window.addEventListener("hashchange", () => { if (M) route(); });

  // ------------------------------------------------------------------ boot
  load("meta").then((m) => {
    META = m;
    EXT = m.ext;
    document.title = "satk catalog · " + m.profile;
    return Promise.all(ORDER.map(load));
  }).then(([mm, tt, xx, zz]) => {
    M = mm; T = tt.rows; IMG = tt.img; X = xx; Z = zz;
    (tt.nothumb || []).forEach((i) => NOTHUMB.add(i));
    modelById = new Map(M.map((r, i) => [r[0], i]));
    imgByHex = new Map(IMG.map((h, i) => [h, i]));
    txdByName = new Map();
    X.forEach((r, i) => { const k = r[0].toLowerCase(); if (!txdByName.has(k)) txdByName.set(k, i); });
    texByKey = new Map();
    T.forEach((r, i) => { const k = (X[r[0]][0] + "/" + r[1]).toLowerCase(); if (!texByKey.has(k)) texByKey.set(k, i); });
    zoneByName = new Map();
    Z.forEach((r, i) => { const k = r[0].toLowerCase(); if (!zoneByName.has(k)) zoneByName.set(k, i); });
    const c = META.counts;
    footEl.textContent = `satk catalog · profile ${META.profile} · index ${META.index || "?"} · satk ${META.satk} · ` +
      `${num(c.models)} models, ${num(c.textures)} textures, ${num(c.zones)} zones · rebuild: satk catalog build`;
    qEl.disabled = false;
    route();
  }).catch((e) => {
    view.innerHTML = `<div class="empty err">The catalog failed to load: ${esc(e.message || e)}.<br>` +
      `Rebuild it: <code>satk catalog build</code></div>`;
  });
})();
