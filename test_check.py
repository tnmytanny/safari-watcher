"""Parser checks on trimmed real portal HTML. Run: python3 test_check.py"""
from check import parse_mh, parse_mp

MP = """<tr align="center"><td><b>14 Nov 2026</b></td>
<td><div style="background-image: url(images/Booked.png);"></div></td>
<td><div class="avail" style="width:100%;">
<a href="LoginUserProfilePage.aspx?langid=en-US&amp;TimeSlot=Evening&amp;Zone=Khawasa&amp;zone_id=94&amp;Tourist_Place_id=4&amp;Date=14 Nov 2026&amp;TicketType=N" title="Please click on the date for reservation">12</a>
</div></td></tr>"""

MH = """<table class='table table-stripped table-bordered'><tbody> <tr>
<td rowspan='3' align='center'><strong>Gate</strong></td><td colspan='2' align='center'>  <strong>14/11/2026 (Sat)</strong></td><td colspan='2' align='center'>  <strong>15/11/2026 (Sun)</strong></td></tr>
<tr><td colspan=''> <strong>Morning</strong></td><td colspan=''> <strong>Afternoon</strong></td><td colspan=''> <strong>Morning</strong></td><td colspan=''> <strong>Afternoon</strong></td> </tr>
<tr><td colspan=''> <strong>FG</strong></td><td colspan=''> <strong>FG</strong></td><td colspan=''> <strong>FG</strong></td><td colspan=''> <strong>FG</strong></td> </tr>
<tr><td><strong>Agarzari (Buffer)</strong> <a href='https://maps' title='Location Map'><i></i></a></td><td><span class='badge badge-danger'><strong>Waitlist Booking</strong></span></td><td><div class='badge badge-danger'><strong>Booked</strong></div></td><td> <div class='badge badge-dark'><strong>Gate Closed</strong></div></td><td> <a href='#' class='badge badge-success'>2</a></td></tr>
</tbody></table>"""

assert parse_mp(MP) == {"Pench MP|2026-11-14|Khawasa|Evening": 12}, parse_mp(MP)
assert parse_mh("Tadoba Buffer", MH) == {"Tadoba Buffer|2026-11-15|Agarzari (Buffer)|Afternoon": 2}, parse_mh("Tadoba Buffer", MH)
print("ok")

# delivery: state advances only after a successful send; failures retry next run
import datetime, os, tempfile
import check

D = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()

check.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")
portal = {f"Pench MP|{D}|Khawasa|Morning": 5}
check.mp_slots = lambda dates: dict(portal)
check.mh_slots = lambda *a: {}
sent, fail = [], [False]


def fake_email(new, first):
    if fail[0]:
        raise OSError("smtp down")
    sent.append((sorted(new), first))
    return True


check.email_alert, check.whatsapp_alert = fake_email, lambda new, first: None  # whatsapp unconfigured


def run():
    try:
        check.main()
    except SystemExit:
        pass


fail[0] = True; run()                       # send fails -> nothing marked delivered
fail[0] = False; run()                      # retried, sent as first snapshot
assert sent == [([f"Pench MP|{D}|Khawasa|Morning"], True)], sent
run(); assert len(sent) == 1                # no change -> no mail
portal[f"Pench MP|{D}|Khawasa|Evening"] = 3; run()  # freed slot -> only it
assert sent[-1] == ([f"Pench MP|{D}|Khawasa|Evening"], False), sent
print("delivery ok")
