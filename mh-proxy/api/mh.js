// Runs in Mumbai (bom1) so the MH portal sees an Indian IP. Only proxies the public booking-status call.
export async function GET(request) {
  const q = new URL(request.url).searchParams;
  if (q.get("token") !== process.env.PROXY_TOKEN) return new Response("forbidden", { status: 403 });
  const park = q.get("park"), vehicle = q.get("vehicle"), date = q.get("date");
  if (!/^\d+$/.test(park) || !/^\d+$/.test(vehicle) || !/^\d{4}-\d{2}-\d{2}$/.test(date))
    return new Response("bad params", { status: 400 });
  const base = "https://safaribooking.mahaforest.gov.in";
  const r = await fetch(`${base}/Home/viewList/${park}/${vehicle}/${date}/R`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "X-Requested-With": "XMLHttpRequest",
      Referer: base + "/",
      "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36",
    },
    body: new URLSearchParams({ cmbSanctuary: park, cmbVehicle: vehicle, txtDateBooking: date, booking_type: "R" }),
    signal: AbortSignal.timeout(60000),
  });
  return new Response(await r.text(), { status: r.status, headers: { "Content-Type": "application/json" } });
}
