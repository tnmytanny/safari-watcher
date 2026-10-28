"""Safari slot watcher: alerts (email + WhatsApp) when new slots open on MP / MH forest portals."""
import datetime as dt
import html
import json
import os
import re
import smtplib
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from http.cookiejar import CookieJar

# local runs: read secrets from .env (gitignored); on GitHub they come from repo secrets
_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env):
    for line in open(_env):
        k, sep, v = line.strip().partition("=")
        if sep and not k.startswith("#") and v.strip():
            os.environ.setdefault(k.strip(), v.strip().strip('"\''))

DAYS_AHEAD = int(os.environ.get("DAYS_AHEAD", "30"))
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36"

MP_BASE = "https://forest.mponline.gov.in"
MP_PARKS = [("Bandhavgarh", 1), ("Kanha", 2), ("Pench MP", 4)]
MH_BASE = "https://safaribooking.mahaforest.gov.in"
# (label, park id, vehicle id) — vehicle ids from Home/VehicleDropDown
MH_PARKS = [("Tadoba Core", 1, 1), ("Tadoba Buffer", 2, 4), ("Pench MH", 7, 1)]
P = "ctl00$ContentPlaceHolder1$"
# gates the client works with (substring of portal gate name); "<park> Single"/"<park> Tatkal" share the park's list
GATES = {
    "Bandhavgarh": ("Tala", "Khitauli", "Magadhi"),
    "Kanha": ("Kanha", "Kisli", "Mukki"),
    "Pench MP": ("Touria", "Karmajhiri", "Jhamtara"),
    "Tadoba Core": ("Moharli", "Khutwanda"),
    "Tadoba Buffer": ("Adegaon", "Agarzari", "Junona", "Dewada", "Mamla", "Nimdhela"),
    "Pench MH": ("Sillari", "Khursapar"),
}
OBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "observations.csv")


def wanted(key):
    park, _, gate, _ = key.split("|")
    gates = GATES.get(park.removesuffix(" Single").removesuffix(" Tatkal"))
    return gates is None or any(g.lower() in gate.lower() for g in gates)


def book_link(park):
    return f"{MH_BASE}/Userlogin/index/" if park in {p[0] for p in MH_PARKS} else MP_BASE


def fetch(opener, url, data=None, headers=None, tries=2):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, body, {"User-Agent": UA, **(headers or {})})
    for i in range(tries):
        try:
            with opener.open(req, timeout=60) as r:
                return r.read().decode("utf-8-sig", "ignore")
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5)


def parse_mp(page, label="Pench MP"):
    """Available cells are booking links; link text is seats left."""
    out = {}
    for href, seats in re.findall(r'<a href="(LoginUserProfilePage\.aspx\?[^"]+)"[^>]*>\s*(\d+)\s*</a>', page):
        q = urllib.parse.parse_qs(html.unescape(href))
        d = dt.datetime.strptime(q["Date"][0], "%d %b %Y").date().isoformat()
        out[f"{label}|{d}|{q['Zone'][0]}|{q['TimeSlot'][0]}"] = int(seats)
    return out


def mp_slots(label, park, dates, seat="N", page="Search.aspx"):
    """page=searchNew.aspx is the Premium Tatkal grid; it rejects requests without a session + Referer."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    search = f"{MP_BASE}/Search.aspx?park={park}"
    url = f"{MP_BASE}/{page}?park={park}"
    first = fetch(opener, search)
    if url != search:
        first = fetch(opener, url, headers={"Referer": search})
    hidden = dict(re.findall(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"', first))
    out = {}
    for d in dates:
        form = {**{k: html.unescape(v) for k, v in hidden.items()},
                P + "Seat": seat, P + "txtdate": d.strftime("%a, %d %B %Y"),
                P + "btnshow.x": "10", P + "btnshow.y": "10"}
        out.update(parse_mp(fetch(opener, url, form, {"Referer": url}), label))
    return out


def parse_mh(label, table):
    """Header: date cells with colspan, then shift row; each gate row has one cell per (date, shift).
    Value: seats left (int) or "WL" (sold out, waitlist open) / "Booked" / "Closed"."""
    rows = re.split(r"<tr[^>]*>", table)
    dates = re.findall(r"colspan='(\d+)'[^>]*>\s*<strong>(\d\d/\d\d/\d{4})", table)
    shifts = re.findall(r"<strong>(Morning|Afternoon|Evening)</strong>", table)
    cols = []
    for span, d in dates:
        iso = dt.datetime.strptime(d, "%d/%m/%Y").date().isoformat()
        cols += [iso] * int(span)
    cols = list(zip(cols, shifts))
    out = {}
    for row in rows:
        cells = row.split("</td>")
        m = re.match(r"\s*<td><strong>([^<]+)</strong>", cells[0])  # header/shift rows differ
        if not m or m.group(1) in ("Morning", "Afternoon", "Evening"):
            continue
        for (d, shift), cell in zip(cols, cells[1:]):
            s = re.search(r"badge-success'>\s*(\d+)\s*<", cell)
            state = int(s.group(1)) if s else next(
                (v for t, v in (("Waitlist", "WL"), ("Booked", "Booked"), ("Gate Closed", "Closed")) if t in cell), None)
            if state is not None:
                out[f"{label}|{d}|{m.group(1).strip()}|{shift}"] = state
    return out


def mh_slots(label, park, vehicle, dates):
    opener = urllib.request.build_opener()
    out = {}
    for d in dates:
        iso = d.isoformat()
        proxy = os.environ.get("MH_PROXY_URL")  # Mumbai relay: MH portal blocks non-Indian IPs
        if proxy:
            q = urllib.parse.urlencode({"token": os.environ["MH_PROXY_TOKEN"], "park": park, "vehicle": vehicle, "date": iso})
            out.update(parse_mh(label, json.loads(fetch(opener, f"{proxy}?{q}"))[0]))
            continue
        raw = fetch(opener, f"{MH_BASE}/Home/viewList/{park}/{vehicle}/{iso}/R",
                    {"cmbSanctuary": park, "cmbVehicle": vehicle, "txtDateBooking": iso, "booking_type": "R"},
                    {"X-Requested-With": "XMLHttpRequest", "Referer": MH_BASE + "/"})
        out.update(parse_mh(label, json.loads(raw)[0]))
    return out


def send_email(text, html_body=None, subject="Safari slots open"):
    user, pw, to = (os.environ.get(k) for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "ALERT_EMAIL"))
    if not (user and pw and to):
        return print("email not configured, skipping")  # None: not configured, state untouched
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, pw)
        s.send_message(msg)
    return True


def send_whatsapp(text):
    phone, key = os.environ.get("WA_PHONE"), os.environ.get("WA_APIKEY")
    if not (phone and key):
        return print("whatsapp not configured, skipping")
    for i in range(0, len(text), 1500):  # CallMeBot message size limit
        q = urllib.parse.urlencode({"phone": phone, "text": text[i:i + 1500], "apikey": key})
        urllib.request.urlopen(f"https://api.callmebot.com/whatsapp.php?{q}", timeout=60).read()
        time.sleep(3)
    return True


def send_telegram(text):
    token, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not (token and chat):
        return print("telegram not configured, skipping")
    chunks, cur = [], ""
    for line in text.split("\n"):  # Telegram caps messages at 4096 chars; split on line boundaries
        if len(cur) + len(line) > 3800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    for c in chunks + [cur]:
        body = urllib.parse.urlencode({"chat_id": chat, "text": c, "parse_mode": "HTML",
                                       "disable_web_page_preview": "true"}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", body, timeout=60).read()
    return True


def format_alert(new):
    lines = ["New safari slots available:"]
    for k in sorted(new, key=lambda k: (k.split("|")[1], k)):
        park, d, gate, shift = k.split("|")
        lines.append(f"- {park} | {d} | {gate} | {shift} | {new[k]} seats")
    lines += ["", f"MP: {MP_BASE}", f"MH: {MH_BASE}/Userlogin/index/"]
    return "\n".join(lines)


def format_html(slots, heading, note=""):
    """One card per park; a row per date+gate with a seats column per shift. Inline styles for email clients."""
    e = html.escape
    parks = {}
    for k, seats in slots.items():
        park, d, gate, shift = k.split("|")
        parks.setdefault(park, {}).setdefault((d, gate), {})[shift] = seats
    td = "padding:9px 12px;border-bottom:1px solid #eef0ee;font-size:14px;"
    cards = []
    for park in sorted(parks):
        rows = parks[park]
        shifts = [s for s in ("Morning", "Afternoon", "Evening") if any(s in r for r in rows.values())]
        link = book_link(park)
        head = "".join(f'<th style="{td}text-align:center;color:#6b7280;font-weight:600;font-size:12px;'
                       f'text-transform:uppercase;letter-spacing:.04em">{s}</th>' for s in shifts)
        body, last = [], None
        for d, gate in sorted(rows):
            day = dt.date.fromisoformat(d).strftime("%a, %d %b")
            label = f'<b style="color:#111827">{day}</b>' if d != last else ""
            last = d
            cells = "".join(
                f'<td style="{td}text-align:center">'
                + (f'<span style="background:#e7f6ec;color:#166534;border-radius:12px;padding:3px 10px;'
                   f'font-weight:600">{rows[(d, gate)][s]}</span>' if s in rows[(d, gate)]
                   else '<span style="color:#d1d5db">&ndash;</span>')
                + "</td>" for s in shifts)
            body.append(f'<tr><td style="{td}white-space:nowrap">{label}</td>'
                        f'<td style="{td}color:#374151">{e(gate)}</td>{cells}</tr>')
        n = sum(len(r) for r in rows.values())
        cards.append(f'''
<table width="100%" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;margin:0 0 20px;border:1px solid #e5e7eb">
 <tr><td style="padding:18px 20px 6px">
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
   <td style="font-size:18px;font-weight:700;color:#14532d">{e(park)}</td>
   <td align="right"><a href="{link}" style="background:#15803d;color:#fff;text-decoration:none;padding:8px 16px;border-radius:8px;font-size:13px;font-weight:600">Book now</a></td>
  </tr></table>
  <div style="color:#6b7280;font-size:13px;margin-top:2px">{n} slot{"s" * (n != 1)} &middot; seats left per shift</div>
 </td></tr>
 <tr><td style="padding:6px 8px 12px">
  <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">
   <tr><th style="{td}text-align:left;color:#6b7280;font-size:12px;text-transform:uppercase">Date</th>
       <th style="{td}text-align:left;color:#6b7280;font-size:12px;text-transform:uppercase">Gate</th>{head}</tr>
   {"".join(body)}
  </table>
 </td></tr>
</table>''')
    return f'''<!doctype html><html><body style="margin:0;background:#f4f6f4;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:640px">
 <tr><td style="padding:0 4px 18px">
  <div style="font-size:22px;font-weight:700;color:#111827">{e(heading)}</div>
  {f'<div style="color:#4b5563;font-size:14px;margin-top:6px">{e(note)}</div>' if note else ""}
 </td></tr>
 <tr><td>{"".join(cards)}</td></tr>
 <tr><td style="color:#9ca3af;font-size:12px;padding:4px;text-align:center">Safari slot watcher &middot; checks every 2 minutes</td></tr>
</table></td></tr></table></body></html>'''


def email_alert(new, first):
    n = len(new)
    if first:
        heading = f"All open safari slots ({n})"
        note = f"Full snapshot for the next {DAYS_AHEAD} days. From now on you'll only get newly opened slots."
    else:
        heading, note = f"{n} new safari slot{'s' * (n != 1)} open", "Booked slots that freed up or newly added dates."
    return send_email(format_alert(new), format_html(new, heading, note), heading)


def whatsapp_alert(new, first):
    if len(new) <= 30:
        return send_whatsapp(format_alert(new))
    counts = {}  # big batches (e.g. first run) as a per-park summary instead of dozens of messages
    for k in new:
        counts[k.split("|")[0]] = counts.get(k.split("|")[0], 0) + 1
    return send_whatsapp(f"{len(new)} safari slots open: " + ", ".join(f"{p} {n}" for p, n in sorted(counts.items()))
                         + ". Full list in your email.")


def telegram_alert(new, first):
    e = html.escape
    n = len(new)
    title = f"All open safari slots ({n})" if first else f"{n} new safari slot{'s' * (n != 1)} open"
    rows = {}
    for k, seats in new.items():
        park, d, gate, shift = k.split("|")
        rows.setdefault(park, {}).setdefault(d, {}).setdefault(gate, []).append(f"{shift} {seats}")
    lines = [f"<b>{e(title)}</b>"]
    for park in sorted(rows):
        link = book_link(park)
        lines += ["", f'<b>{e(park)}</b>  <a href="{link}">Book</a>']
        for d in sorted(rows[park]):
            day = dt.date.fromisoformat(d).strftime("%a %d %b")
            if n > 30:  # big batch: one line per date instead of every gate
                lines.append(f"{day}: {sum(len(v) for v in rows[park][d].values())} slots")
            else:
                for gate, shifts in sorted(rows[park][d].items()):
                    lines.append(f"{day} · {e(gate)}: {', '.join(sorted(shifts, key=lambda x: x[0] != 'M'))}")
    return send_telegram("\n".join(lines))


def log_changes(old, new):
    """Append MH cell state changes (seats/WL/Booked) to observations.csv, to learn how cancellations flow."""
    rows = [(k, old[k], v) for k, v in new.items() if k in old and old[k] != v and k.split("|")[0] in {p[0] for p in MH_PARKS}]
    if not rows:
        return
    first = not os.path.exists(OBS_FILE)
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M")
    with open(OBS_FILE, "a") as f:
        if first:
            f.write("seen_ist,park,date,gate,shift,old,new\n")
        for k, o, v in rows:
            park, d, gate, shift = k.split("|")
            f.write(f'{now},{park},{d},"{gate}",{shift},{o},{v}\n')


def main(dry_run=False):
    today = dt.date.today()
    dates = [today + dt.timedelta(days=i) for i in range(1, DAYS_AHEAD + 1, 5)]
    # per channel: slots that channel has successfully delivered, {"email": {key: seats}, "whatsapp": {...}}
    state = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
    days = lambda *n: [today + dt.timedelta(days=i) for i in n]
    # today included: same-day re-ticket (no-show) permits show up in the regular MP grid
    jobs = [j for label, p in MP_PARKS for j in (
        (label, lambda l=label, p=p: mp_slots(l, p, [today] + dates)),
        (f"{label} Single", lambda l=label, p=p: mp_slots(f"{l} Single", p, days(1), seat="S")),  # opens D-1 2 PM
        (f"{label} Tatkal", lambda l=label, p=p: mp_slots(f"{l} Tatkal", p, days(2, 5, 7), page="searchNew.aspx")),  # D-7 11 AM
    )] + [(label, lambda p=p, v=v, l=label: mh_slots(l, p, v, dates)) for label, p, v in MH_PARKS]
    cutoff = (today + dt.timedelta(days=DAYS_AHEAD)).isoformat()
    scraped, cells, failed = {}, {}, set()
    # each job has its own session; parallel because ~50 sequential requests to slow portals took ~2 min
    with ThreadPoolExecutor(len(jobs)) as pool:
        futures = [(label, pool.submit(job)) for label, job in jobs]
    for label, fut in futures:
        try:
            got = {k: v for k, v in fut.result().items() if today.isoformat() <= k.split("|")[1] <= cutoff and wanted(k)}
            cells.update(got)
            got = {k: v for k, v in got.items() if isinstance(v, int)}
            print(f"{label}: {len(got)} available slots")
            scraped.update(got)
        except Exception as e:
            print(f"{label}: FAILED {e!r}")
            failed.add(label)
    if dry_run:
        print(format_alert(scraped))
        return
    old_cells = state.get("cells", {})
    log_changes(old_cells, cells)
    state["cells"] = {**{k: v for k, v in old_cells.items() if k.split("|")[0] in failed}, **cells}
    sent_ok = True
    for channel, send in (("email", email_alert), ("telegram", telegram_alert), ("whatsapp", whatsapp_alert)):
        old = state.get(channel, {})
        # a park that failed to scrape keeps this channel's last view, so it doesn't re-alert on recovery
        now = {**{k: v for k, v in old.items() if k.split("|")[0] in failed}, **scraped}
        # new = freed-up slot or newly added date/slot; with no state yet that's every open slot
        new = {k: v for k, v in now.items() if k not in old}
        if not new:
            state[channel] = now
            continue
        try:
            if send(new, first=not old):
                state[channel] = now  # only mark delivered after a successful send
                print(f"{channel}: sent {len(new)} new slots")
        except Exception as e:
            sent_ok = False
            print(f"{channel}: SEND FAILED {e!r}, will retry next run")
    json.dump(state, open(STATE_FILE, "w"), indent=1, sort_keys=True)
    if not sent_ok or len(failed) == len(jobs):
        sys.exit("send failed" if not sent_ok else "all portals failed")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
