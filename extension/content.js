// Safari Permit Assist: at the release time refresh availability, open the best gate, replay a saved form.
// Never reads or fills passwords, captcha or OTP, and never clicks payment: a person finishes those.
(() => {
  const MP = location.host === "forest.mponline.gov.in";
  const SKIP = /pass|captcha|otp|cvv|card|txtusername|email_newsletter/i;
  const TICK = /declar|agree|term|accept|undertak/i;
  const formKey = "form:" + location.host + location.pathname.toLowerCase();
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const FULL = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const RETRY_MS = 1500, WINDOW_MS = 90000; // ~60 refreshes max, one every 1.5 s
  let cfg = {}, offset = 0; // offset = portal clock - local clock (ms)

  const portalNow = () => Date.now() + offset;
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
        } else if (el.value !== v) {
          el.value = v;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
          n++;
        }
      });
    }
    for (const cb of document.querySelectorAll("input[type=checkbox]"))
      if (!cb.checked && TICK.test(`${cb.id} ${cb.name} ${cb.closest("label, td, div")?.textContent || ""}`)) { cb.click(); n++; }
    const manual = [...document.querySelectorAll("input")].find((el) => /captcha|otp/i.test(el.name + el.id) && el.offsetParent);
    if (manual) { manual.scrollIntoView({ block: "center" }); manual.focus(); }
    return `${data ? "Filled" : "No saved form here; ticked"} ${n} field(s)${manual ? " — enter captcha/OTP" : ""}`;
  }

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

  function refresh() {
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
    btn?.click();
    return !!btn;
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
    if (sessionStorage.getItem(tag) === "done") return;
    while (cfg.armed && portalNow() - releaseAt() < WINDOW_MS) {
      const c = best();
      if (c) {
        sessionStorage.setItem(tag, "done");
        banner(`Opening ${c.gate} ${c.shift} ${c.date}`);
        c.el.click();
        return;
      }
      if (!refresh()) return banner("No search button on this page — open the availability page");
      await sleep(RETRY_MS);
    }
  }

  // ---------- banner + clock ----------
  const box = document.createElement("div");
  box.style.cssText = "position:fixed;top:8px;right:8px;z-index:2147483647;background:#14532d;color:#fff;" +
    "font:13px/1.4 system-ui,sans-serif;padding:6px 10px;border-radius:8px;box-shadow:0 2px 8px #0004;pointer-events:none";
  let note = "";
  const banner = (t) => { note = t; };

  async function syncClock() {
    try { // Date header has 1 s resolution; +500 ms centres the error
      const r = await fetch(location.href, { method: "HEAD", cache: "no-store", credentials: "include" });
      offset = Date.parse(r.headers.get("date")) + 500 - Date.now();
    } catch { /* keep last offset */ }
  }

  function render() {
    if (!cfg.armed) return box.remove();
    if (!box.isConnected) document.body.append(box);
    const left = Math.round((releaseAt() - portalNow()) / 1000);
    const clock = new Date(portalNow()).toTimeString().slice(0, 8);
    box.textContent = `Portal ${clock} · ${left > 0 ? `T-${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "LIVE"}` +
      ` · ${cfg.date || "any date"} ${cfg.gates || "any gate"}${note ? " · " + note : ""}`;
  }

  let firing = false;
  setInterval(() => {
    render();
    const dt = portalNow() - releaseAt();
    if (cfg.armed && !firing && dt >= 0 && dt < WINDOW_MS) { firing = true; attempt().finally(() => (firing = false)); }
  }, 200);
  setInterval(() => cfg.armed && syncClock(), 4 * 60000); // also keeps the logged-in session alive

  chrome.storage.onChanged.addListener((ch) => { if (ch.cfg) cfg = ch.cfg.newValue || {}; });
  chrome.runtime.onMessage.addListener((m, _, reply) => {
    (m.type === "save" ? save() : fill()).then(reply);
    return true;
  });

  chrome.storage.local.get("cfg").then(async ({ cfg: c = {} }) => {
    cfg = c;
    await syncClock();
    if (cfg.armed) setTimeout(() => fill().then(banner), 300); // booking form pages: replay visitors right away
  });
})();
