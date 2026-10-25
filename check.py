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

MP_URL = "https://forest.mponline.gov.in/Search.aspx?park=4"
MH_BASE = "https://safaribooking.mahaforest.gov.in"
# (label, park id, vehicle id) — vehicle ids from Home/VehicleDropDown
MH_PARKS = [("Tadoba Core", 1, 1), ("Tadoba Buffer", 2, 4)]
P = "ctl00$ContentPlaceHolder1$"


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


def parse_mp(page):
    """Available cells are booking links; link text is seats left."""
    out = {}
    for href, seats in re.findall(r'<a href="(LoginUserProfilePage\.aspx\?[^"]+)"[^>]*>\s*(\d+)\s*</a>', page):
        q = urllib.parse.parse_qs(html.unescape(href))
        d = dt.datetime.strptime(q["Date"][0], "%d %b %Y").date().isoformat()
        out[f"Pench MP|{d}|{q['Zone'][0]}|{q['TimeSlot'][0]}"] = int(seats)
    return out


def mp_slots(dates):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    page = fetch(opener, MP_URL)
    hidden = dict(re.findall(r'<input type="hidden" name="([^"]+)" id="[^"]*" value="([^"]*)"', page))
    out = {}
    for d in dates:
        form = {**{k: html.unescape(v) for k, v in hidden.items()},
                P + "Seat": "N", P + "txtdate": d.strftime("%a, %d %B %Y"),
                P + "btnshow.x": "10", P + "btnshow.y": "10"}
        out.update(parse_mp(fetch(opener, MP_URL, form)))
    return out


def parse_mh(label, table):
    """Header: date cells with colspan, then shift row; each gate row has one cell per (date, shift)."""
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
            if s:
                out[f"{label}|{d}|{m.group(1).strip()}|{shift}"] = int(s.group(1))
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


def send_email(text):
    user, pw, to = (os.environ.get(k) for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "ALERT_EMAIL"))
    if not (user and pw and to):
        return print("email not configured, skipping")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = "Safari slots open!", user, to
    msg.set_content(text)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, pw)
        s.send_message(msg)


def send_whatsapp(text):
    phone, key = os.environ.get("WA_PHONE"), os.environ.get("WA_APIKEY")
    if not (phone and key):
        return print("whatsapp not configured, skipping")
    for i in range(0, len(text), 1500):  # CallMeBot message size limit
        q = urllib.parse.urlencode({"phone": phone, "text": text[i:i + 1500], "apikey": key})
        urllib.request.urlopen(f"https://api.callmebot.com/whatsapp.php?{q}", timeout=60).read()
        time.sleep(3)


def format_alert(new):
    lines = ["New safari slots available:"]
    for k in sorted(new, key=lambda k: (k.split("|")[1], k)):
        park, d, gate, shift = k.split("|")
        lines.append(f"- {park} | {d} | {gate} | {shift} | {new[k]} seats")
    lines += ["", f"MP: {MP_URL}", f"MH: {MH_BASE}/Userlogin/index/"]
    return "\n".join(lines)


def main(dry_run=False):
    today = dt.date.today()
    dates = [today + dt.timedelta(days=i) for i in range(1, DAYS_AHEAD + 1, 5)]
    old = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
    seen_parks = {k.split("|")[0] for k in old}
    jobs = [("Pench MP", lambda: mp_slots(dates))] + [
        (label, lambda p=p, v=v, l=label: mh_slots(l, p, v, dates)) for label, p, v in MH_PARKS]
    cutoff = (today + dt.timedelta(days=DAYS_AHEAD)).isoformat()
    now, failed = {}, []
    for label, job in jobs:
        try:
            got = {k: v for k, v in job().items() if k.split("|")[1] <= cutoff}
            print(f"{label}: {len(got)} available slots")
            now.update(got)
        except Exception as e:
            print(f"{label}: FAILED {e!r}")
            failed.append(label)
            # keep previous state for failed park so its slots don't re-alert on recovery
            now.update({k: v for k, v in old.items() if k.startswith(label + "|")})
    # a park's first successful run only seeds state, else every open slot alerts at once
    seeded = {k.split("|")[0] for k in now} - seen_parks
    if seeded:
        print(f"seeded without alert: {sorted(seeded)}")
    # new = freed-up slot or newly added date/slot (anything not open last run)
    new = {k: v for k, v in now.items()
           if k not in old and k.split("|")[0] not in seeded and k.split("|")[1] > today.isoformat()}
    if dry_run:
        print(format_alert(now))
        return
    if new:
        text = format_alert(new)
        print(text)
        send_email(text)
        send_whatsapp(text)
    else:
        print("no new slots")
    json.dump(now, open(STATE_FILE, "w"), indent=1, sort_keys=True)
    if len(failed) == len(jobs):
        sys.exit("all portals failed")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
