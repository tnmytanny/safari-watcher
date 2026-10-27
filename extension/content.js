// Safari Permit Assist: at the release time refresh availability, open the best gate, replay a saved form.
// Never reads or fills passwords, captcha or OTP, and never clicks payment: a person finishes those.
(() => {
  const MP = location.host === "forest.mponline.gov.in";
  const SKIP = /pass|captcha|otp|cvv|card|bank|ifsc|account|txtusername|email_newsletter/i; // never saved or filled
  const TICK = /declar|agree|term|accept|undertak/i;
  const formKey = "form:" + location.host + location.pathname.toLowerCase();
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const FULL = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const RETRY_MS = 1500, WINDOW_MS = 90000; // ~60 refreshes max, one every 1.5 s
  let cfg = {}, offset = 0; // offset = portal clock - local clock (ms)

  offset = Number(sessionStorage.getItem("spa-offset")) || 0; // last sync, so page-load marks use the portal clock
  const portalNow = () => Date.now() + offset;

  // ---------- timing log: each step relative to the release time, kept for the last run ----------
  let logChain = Promise.resolve(); // serialise read-modify-write of runLog
  const mark = (label, at = portalNow()) => (logChain = logChain.then(() => writeMark(label, at)).catch(() => {}));
  async function writeMark(label, at) {
    if (!cfg.armId) return;
    const { runLog = {} } = await chrome.storage.local.get("runLog");
    const log = runLog.armId === cfg.armId ? runLog : { armId: cfg.armId, release: `${cfg.date} ${cfg.at}`, steps: [] };
    log.steps.push(`${((at - releaseAt()) / 1000).toFixed(2)}s ${label}`);
    await chrome.storage.local.set({ runLog: log });
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---------- form record / replay ----------
  const fields = () => [...document.querySelectorAll("input, select, textarea")].filter((el) =>
    !["hidden", "submit", "button", "image", "password", "file"].includes(el.type) &&
    !SKIP.test(`${el.name} ${el.id} ${el.placeholder || ""}`));
  const keysOf = (els) => { // name/id plus occurrence index, so repeated visitor rows stay distinct
    const seen = {};
    return els.map((el) => { const k = el.name || el.id; seen[k] = (seen[k] || 0) + 1; return `${k}#${seen[k]}`; });
  };

  async function save() {
    const els = fields(), keys = keysOf(els), data = {};
    els.forEach((el, i) => {
      if (el.type === "checkbox" || el.type === "radio") data[keys[i]] = el.checked;
      else if (el.value) data[keys[i]] = el.value;
    });
    await chrome.storage.local.set({ [formKey]: data });
    return `Saved ${Object.keys(data).length} fields for ${location.pathname}`;
  }

  async function fill() {
    const data = (await chrome.storage.local.get(formKey))[formKey];
    let n = 0;
    if (data) {
      const els = fields(), keys = keysOf(els);
      els.forEach((el, i) => {
        if (!(keys[i] in data)) return;
        const v = data[keys[i]];
        if (el.type === "checkbox" || el.type === "radio") {
          if (v && !el.checked) { el.click(); n++; }
        } else if (el.tagName === "SELECT" && ![...el.options].some((o) => o.value === v)) {
          return; // e.g. saved entry gate from another zone: leave the portal's choice
        } else if (el.value !== v) {
          el.value = v;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
          n++;
        }
      });
    }
    for (const sel of fields().filter((el) => el.tagName === "SELECT" && el.selectedIndex === 0 && el.options.length === 2 &&
      /select/i.test(el.options[0].text))) {
      sel.selectedIndex = 1; // only one real choice (MP entry gate): take it
      sel.dispatchEvent(new Event("change", { bubbles: true }));
      n++;
    }
    for (const cb of document.querySelectorAll("input[type=checkbox]"))
      if (!cb.checked && TICK.test(`${cb.id} ${cb.name} ${cb.closest("label, td, div")?.textContent || ""}`)) { cb.click(); n++; }
    const manual = [...document.querySelectorAll("input")].find((el) => /captcha|otp/i.test(el.name + el.id) && el.offsetParent);
    if (manual) { manual.scrollIntoView({ block: "center" }); manual.focus(); }
    return `${data ? "Filled" : "No saved form here; ticked"} ${n} field(s)${manual ? " — enter captcha/OTP" : ""}`;
  }

  // Enter in the captcha/OTP box clicks the step's own button (the form's default would hit a row-reset image button)
  const NEXT = [/book your permit/i, /verify/i, /log ?in|sign in/i, /submit|continue/i]; // first match wins
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || !/captcha|otp/i.test(e.target.name + e.target.id)) return;
    const btns = [...document.querySelectorAll("input[type=submit], input[type=button], button")]
      .filter((b) => b.offsetParent && !/pay/i.test(b.value || b.textContent));
    const btn = NEXT.map((re) => btns.find((b) => re.test(b.value || b.textContent))).find(Boolean);
    if (!btn) return;
    e.preventDefault();
    mark(`enter -> ${(btn.value || btn.textContent).trim()}`);
    btn.click();
  }, true);

  // ---------- availability: find the best open gate ----------
  function candidates() {
    const out = [];
    if (MP) {
      for (const a of document.querySelectorAll('a[href*="LoginUserProfilePage.aspx"]')) {
        if (!(parseInt(a.textContent, 10) > 0)) continue;
        const q = new URLSearchParams(a.getAttribute("href").split("?")[1]);
        out.push({ el: a, gate: q.get("Zone") || "", shift: q.get("TimeSlot") || "", date: q.get("Date") || "" });
      }
    } else {
      for (const a of document.querySelectorAll("a.badge-success")) {
        const td = a.closest("td"), tr = td?.parentElement, table = tr?.closest("table");
        if (!table) continue;
        const cols = []; // same layout as parse_mh in check.py: date header with colspan, then shift row
        for (const c of table.rows[0].cells) {
          const d = c.textContent.match(/\d\d\/\d\d\/\d{4}/);
          if (d) for (let i = 0; i < (parseInt(c.getAttribute("colspan"), 10) || 1); i++) cols.push({ date: d[0] });
        }
        [...table.rows[1].cells].forEach((c, i) => cols[i] && (cols[i].shift = c.textContent.trim()));
        const col = cols[td.cellIndex - 1] || {};
        out.push({ el: a, gate: tr.cells[0].textContent.trim(), shift: col.shift || "", date: col.date || "" });
      }
    }
    return out;
  }

  function best() {
    const [y, m, d] = (cfg.date || "").split("-").map(Number);
    const want = !y ? "" : MP ? `${String(d).padStart(2, "0")} ${MONTHS[m - 1]} ${y}`
      : `${String(d).padStart(2, "0")}/${String(m).padStart(2, "0")}/${y}`;
    const gates = (cfg.gates || "").split(",").map((g) => g.trim().toLowerCase()).filter(Boolean);
    const rank = (c) => gates.length ? gates.findIndex((g) => c.gate.toLowerCase().includes(g)) : 0;
    return candidates()
      .filter((c) => (!want || c.date === want) && (!cfg.shift || c.shift === cfg.shift) && rank(c) >= 0)
      .sort((a, b) => rank(a) - rank(b))[0];
  }

  async function refresh() {
    if (MP) {
      const btn = document.querySelector('input[name$="btnshow"]');
      const date = document.querySelector('input[name$="txtdate"]');
      if (date && cfg.date) {
        const t = new Date(cfg.date + "T00:00:00");
        date.value = `${DAYS[t.getDay()]}, ${String(t.getDate()).padStart(2, "0")} ${FULL[t.getMonth()]} ${t.getFullYear()}`;
      }
      const seats = [...document.querySelectorAll('input[type=radio][name$="$Seat"]')]; // portal won't search without one
      if (seats.length && !seats.some((r) => r.checked)) seats.find((r) => r.value === "N")?.click();
      btn?.click(); // ASP.NET postback reloads the page; the next load continues the attempt
      return !!btn;
    }
    const btn = document.getElementById("btnSubmit"); // MH: AJAX grid refresh, same page
    if (!btn) return false;
    const old = document.querySelector("#bookingHtml table");
    btn.click();
    // a response takes ~2 s; clicking again before it lands restarts it, so wait for the new grid
    for (let t = 0; t < 10000 && document.querySelector("#bookingHtml table") === old; t += 100) await sleep(100);
    return true;
  }

  // ---------- release-time loop ----------
  function releaseAt() {
    const [h, mi, s] = (cfg.at || "00:00:00").split(":").map(Number);
    const t = new Date(portalNow());
    t.setHours(h, mi, s || 0, 0);
    return t.getTime();
  }

  async function attempt() {
    const tag = `spa-${cfg.armId}`;
    if (!sessionStorage.getItem(tag)) await mark("fire");
    if (sessionStorage.getItem(tag) === "done") return;
    if (!sessionStorage.getItem(tag)) sessionStorage.removeItem("spa-picked"); // note from an earlier arm
    sessionStorage.setItem(tag, "running");
    while (cfg.armed && portalNow() - releaseAt() < WINDOW_MS) {
      const c = best();
      if (c) {
        sessionStorage.setItem(tag, "done");
        sessionStorage.setItem("spa-picked", `Opened ${c.gate} ${c.shift} ${c.date}`); // shown on the next page too
        await mark(`pick ${c.gate} ${c.shift} ${c.date}`);
        c.el.click();
        return;
      }
      mark("refresh");
      if (!(await refresh())) return banner("No search button on this page — open the availability page");
      if (MP) await sleep(RETRY_MS);
    }
  }

  // ---------- banner + clock ----------
  const box = document.createElement("div");
  box.style.cssText = "position:fixed;top:8px;right:8px;z-index:2147483647;background:#14532d;color:#fff;" +
    "font:13px/1.4 system-ui,sans-serif;padding:6px 10px;border-radius:8px;box-shadow:0 2px 8px #0004;pointer-events:none";
  let note = "";
  const banner = (t) => { note = t; };

  async function syncClock() {
    // The Date header only has 1 s resolution. Each sample bounds the offset to
    // [D - received, D + 1000 - sent]; intersecting 6 samples ~230 ms apart narrows it to ~0.2 s.
    let lo = -Infinity, hi = Infinity;
    for (let i = 0; i < 6; i++) {
      try { // site root, never the current page: re-requesting MP booking pages trips "Illegal Operation" and logs out
        const sent = Date.now();
        const r = await fetch(location.origin + "/", { method: "HEAD", cache: "no-store", credentials: "include" });
        const got = Date.now(), d = Date.parse(r.headers.get("date"));
        lo = Math.max(lo, d - got);
        hi = Math.min(hi, d + 1000 - sent);
      } catch { /* skip sample */ }
      await sleep(230);
    }
    if (lo <= hi) {
      offset = Math.round((lo + hi) / 2);
      sessionStorage.setItem("spa-offset", offset);
    }
  }

  function render() {
    if (!cfg.armed) return box.remove();
    if (!box.isConnected) document.body.append(box);
    const left = Math.round((releaseAt() - portalNow()) / 1000);
    const clock = new Date(portalNow()).toTimeString().slice(0, 8);
    const picked = sessionStorage.getItem("spa-picked");
    box.textContent = `${picked ? picked + " · " : ""}Portal ${clock} · ${left > 0 ? `T-${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "LIVE"}` +
      ` · ${cfg.date || "any date"} ${cfg.gates || "any gate"}${note ? " · " + note : ""}`;
  }

  let firing = false;
  setInterval(() => {
    render();
    const dt = portalNow() - releaseAt();
    const lead = Number(cfg.lead) || 0; // start early to cover network delay; too early just shows no seats
    if (cfg.armed && !firing && dt >= -lead && dt < WINDOW_MS) { firing = true; attempt().finally(() => (firing = false)); }
  }, 200);
  setInterval(() => cfg.armed && syncClock(), 4 * 60000); // also keeps the logged-in session alive

  // ---------- settings panel (in page, collapsed; shadow DOM keeps portal CSS out) ----------
  const host = document.createElement("div");
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>
      .panel { position:fixed; bottom:8px; right:8px; z-index:2147483647; background:#fff; color:#111;
        font:13px/1.4 system-ui,sans-serif; border:1px solid #14532d; border-radius:8px; padding:6px 10px;
        width:260px; box-shadow:0 2px 8px #0003 }
      summary { cursor:pointer; font-weight:600; color:#14532d }
      label { display:block; margin-top:6px } input:not([type=checkbox]), select { width:100%; box-sizing:border-box }
      small { color:#666 } button { margin:6px 4px 0 0 } [data-msg] { margin-top:6px; color:#166534 }
    </style>
    <details class="panel"><summary>Safari Assist</summary>
      <label>Safari date <input data-k="date" type="date"></label>
      <label>Release time (portal clock) <input data-k="at" type="time" step="1" value="11:00:00"></label>
      <small>MP Tatkal 11:00:00 · Single seat 14:00:00 · Re-ticket 07:10 / 15:40</small>
      <label>Start early by (ms) <input data-k="lead" type="number" min="0" max="3000" step="100" value="500"></label>
      <label>Gate priority <input data-k="gates" placeholder="Mukki, Kisli, Kanha"></label>
      <label>Shift <select data-k="shift"><option value="">Any</option><option>Morning</option>
        <option>Afternoon</option><option>Evening</option></select></label>
      <label><input data-k="armed" type="checkbox"> Armed</label>
      <button data-a="save">Save form on this page</button><button data-a="fill">Fill now</button>
      <button data-a="clear">Forget saved forms</button>
      <div data-msg></div>
      <details><summary>Last run timings</summary><pre data-log style="white-space:pre-wrap;margin:4px 0;font-size:11px"></pre></details>
    </details>`;
  const inputs = [...root.querySelectorAll("[data-k]")];
  const say = (t) => (root.querySelector("[data-msg]").textContent = t);
  const show = () => inputs.forEach((el) => {
    const v = cfg[el.dataset.k];
    if (v !== undefined) el[el.type === "checkbox" ? "checked" : "value"] = v;
  });
  inputs.forEach((el) => el.addEventListener("change", () => {
    const next = Object.fromEntries(inputs.map((i) => [i.dataset.k, i.type === "checkbox" ? i.checked : i.value]));
    // a new arm starts a fresh attempt in every tab
    chrome.storage.local.set({ cfg: { ...next, armId: next.armed ? Date.now() : 0 } }).then(() => say("Saved"));
  }));
  root.addEventListener("click", async (e) => {
    const a = e.target.dataset?.a;
    if (a === "save") say(await save());
    if (a === "fill") say(await fill());
    if (a === "clear") {
      const all = await chrome.storage.local.get(null);
      await chrome.storage.local.remove(Object.keys(all).filter((k) => k.startsWith("form:")));
      say("Saved forms cleared");
    }
  });
  document.body.append(host);

  const showLog = (log) => (root.querySelector("[data-log]").textContent =
    log?.steps ? `Release ${log.release}\n${log.steps.join("\n")}` : "No run yet");
  chrome.storage.local.get("runLog").then(({ runLog }) => showLog(runLog));
  chrome.storage.onChanged.addListener((ch) => {
    if (ch.cfg) { cfg = ch.cfg.newValue || {}; show(); }
    if (ch.runLog) showLog(ch.runLog.newValue);
  });

  chrome.storage.local.get("cfg").then(async ({ cfg: c = {} }) => {
    cfg = c;
    show();
    if (!sessionStorage.getItem("spa-offset")) await syncClock(); // once per tab; re-syncing per page adds jitter
    if (cfg.armed && sessionStorage.getItem(`spa-${cfg.armId}`)) { // pages reached during this run
      await mark(`ready ${location.pathname}`);
    }
    if (cfg.armed) setTimeout(async () => { const r = await fill(); banner(r); if (/Filled/.test(r)) mark(r); }, 300);
  });
})();
