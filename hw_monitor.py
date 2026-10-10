#!/usr/bin/env python3
"""Hot Wheels Mattel Creations stock monitor.

Checks https://creations.mattel.com/collections/hot-wheels-collectors and emails
you when a new vehicle appears or a sold-out vehicle comes back in stock.

Setup (once):
  1. Turn on 2-Step Verification for your Google account, then create an
     App Password at https://myaccount.google.com/apppasswords
  2. Set environment variables:
       HW_GMAIL_USER=<your Gmail address>
       HW_GMAIL_APP_PASSWORD=<the 16-character app password>
  3. Run:  python3 hw_monitor.py --loop 60      (checks every 60 seconds)
     or      python3 hw_monitor.py --loop 60 --for 270   (stops after 4.5 minutes)
     or schedule `python3 hw_monitor.py` with cron / Task Scheduler.
The first run records a baseline and sends nothing.
Optional: HW_WATCH_SKUS=JJY69,JKC06 to alert only on those item numbers.
"""
import html, json, os, re, smtplib, sys, time, urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

URL = "https://creations.mattel.com/collections/hot-wheels-collectors/products.json?limit=250&page={}"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "hw_state.json")
# The store's search provider (Searchspring) lists each variant's sellable quantity.
COUNTS_URL = ("https://f37vx2.a.searchspring.io/api/search/search.json?siteId=f37vx2&resultsFormat=native"
              "&resultsPerPage=100&page={}&bgfilter.collection_handle=hot-wheels-collectors")
QTY_EVERY = 300  # record an item's stock count at most every 5 minutes
LAUNCH_AGE = 45 * 86400  # look for a launch countdown on sold-out vehicles listed in the last 45 days
_launch_cache = {}  # handle -> (checked_at, launch unix time or None)
HISTORY = os.path.join(HERE, "docs", "stock.json")  # read by the dashboard (docs/index.html)
WINDOW = 7 * 86400
TO = os.environ.get("HW_EMAIL_TO") or os.environ.get("HW_GMAIL_USER", "")
SKIP = ("t-shirt", "shirt", "hoodie", "jacket", "jersey", "sweatshirt", "mug", "hat", "luggage", "pin")
WATCH = {s.strip().upper() for s in os.environ.get("HW_WATCH_SKUS", "").split(",") if s.strip()}


def fetch_counts():
    """Units left per product handle, from the search index (it can lag the store a little)."""
    counts = {}
    for page in range(1, 30):
        req = urllib.request.Request(COUNTS_URL.format(page), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        for res in data.get("results", []):
            try:
                variants = json.loads(html.unescape(res.get("ss_variants") or "[]"))
            except ValueError:
                continue
            qty = [v["sellable_online_quantity"] for v in variants
                   if isinstance(v.get("sellable_online_quantity"), int)]
            if qty and res.get("handle"):
                counts[res["handle"]] = max(0, sum(qty))
        pg = data.get("pagination", {})
        if pg.get("currentPage", page) >= pg.get("totalPages", 0):
            break
    return counts


def fetch_launch(handle):
    """Launch time of a "Coming Soon" product, read from its page's countdown, else None."""
    req = urllib.request.Request(f"https://creations.mattel.com/products/{handle}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        page = r.read().decode("utf-8", "replace")
    m = re.search(r"SDG\.Data\.comingSoon\s*=\s*\{(.*?)\}", page, re.S)
    if not m or not re.search(r"isComingSoon:\s*1", m.group(1)):
        return None
    e = re.search(r'expiry:\s*"([A-Za-z]+ \d+ \d{4}, \d\d:\d\d:\d\d) (P[DS]T)"', m.group(1))
    if not e:
        return None
    tz = timezone(timedelta(hours=-7 if e.group(2) == "PDT" else -8))
    return int(datetime.strptime(e.group(1), "%B %d %Y, %H:%M:%S").replace(tzinfo=tz).timestamp())


def find_launches(now):
    """Launch times for recently listed vehicles that can't be bought yet (each page re-read every 30 min)."""
    t, out = time.time(), {}
    for h, i in now.items():
        if not i["vehicle"] or i["available"] or not i.get("listed"):
            continue
        try:
            age = t - datetime.fromisoformat(i["listed"]).timestamp()
        except ValueError:
            continue
        if age > LAUNCH_AGE:
            continue
        checked, launch = _launch_cache.get(h, (0, None))
        if t - checked > 1800:
            try:
                launch = fetch_launch(h)
            except Exception as e:
                print("launch check failed:", h, e)
            _launch_cache[h] = (t, launch)
        if launch and launch > t:
            out[h] = launch
    return out


def is_vehicle(p):
    """Mattel tags each product with categories such as "Category: Vehicles" or "Category: Apparel"."""
    cats = [t for t in p.get("tags", []) if t.startswith("Category:")]
    if cats:
        return "Category: Vehicles" in cats
    return not any(w in p["title"].lower() for w in SKIP)  # untagged: fall back to skipping merch words


def fetch():
    items = {}
    for page in range(1, 20):
        req = urllib.request.Request(URL.format(page), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            products = json.load(r)["products"]
        if not products:
            break
        for p in products:
            v = p["variants"]
            items[p["handle"]] = {
                "title": p["title"],
                "sku": (v[0].get("sku") or "") if v else "",
                "price": v[0].get("price") if v else "",
                "available": any(x.get("available") for x in v),
                "image": (p.get("images") or [{}])[0].get("src", ""),
                "vehicle": is_vehicle(p),
                "listed": p.get("created_at") or p.get("published_at") or "",
            }
    return items


def interesting(handle, item):
    if WATCH:
        return item["sku"].upper() in WATCH
    return item["vehicle"]


def send(subject, body):
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ["HW_GMAIL_USER"], TO
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(os.environ["HW_GMAIL_USER"], os.environ["HW_GMAIL_APP_PASSWORD"])
        s.send_message(msg)


def save_state(now):
    keep = ("title", "sku", "price", "available")
    json.dump({h: {k: i[k] for k in keep} for h, i in now.items()}, open(STATE, "w"))


def record_history(all_now, counts=None, launches=None):
    """Keep 7 days of in/out-of-stock changes per item for the dashboard.

    Each item keeps "events", a list of [unix_time, 1 in stock / 0 sold out / -1 removed]
    changes, and "qty", a list of [unix_time, units left] changes from the search index.
    The newest entry older than 7 days is kept so the dashboard knows each item's state
    for the whole week.
    """
    t = int(time.time())
    try:
        data = json.load(open(HISTORY))
    except (OSError, ValueError):
        data = {"items": {}}
    items = data["items"]
    for h in [h for h in items if h in all_now and not all_now[h]["vehicle"]]:
        del items[h]  # vehicles only
    now = {h: i for h, i in all_now.items() if i["vehicle"]}
    for h, i in now.items():
        rec = items.setdefault(h, {"first_seen": t, "events": []})
        rec.update(title=i["title"], sku=i["sku"], price=i["price"], image=i["image"] or rec.get("image", ""),
                   listed=i.get("listed", ""))
        if (launches or {}).get(h):
            rec["launch"] = launches[h]
        else:
            rec.pop("launch", None)
        state = 1 if i["available"] else 0
        if not rec["events"] or rec["events"][-1][1] != state:
            rec["events"].append([t, state])
        q = (counts or {}).get(h)
        if q is not None:
            qty = rec.setdefault("qty", [])
            if not qty or (qty[-1][1] != q and t - qty[-1][0] >= QTY_EVERY):
                qty.append([t, q])
    for h, rec in items.items():
        if h not in all_now and rec["events"] and rec["events"][-1][1] != -1:
            rec["events"].append([t, -1])
    cutoff = t - WINDOW
    for h in list(items):
        if "qty" in items[h]:
            q = items[h]["qty"]
            items[h]["qty"] = [e for e in q if e[0] < cutoff][-1:] + [e for e in q if e[0] >= cutoff]
        ev = items[h]["events"]
        old = [e for e in ev if e[0] < cutoff]
        ev = old[-1:] + [e for e in ev if e[0] >= cutoff]
        if len(ev) == 1 and ev[0][1] == -1 and ev[0][0] < cutoff:
            del items[h]  # gone from the store for over a week
        else:
            items[h]["events"] = ev
    out = json.dumps({"items": items}, separators=(",", ":"), sort_keys=True)
    try:
        if open(HISTORY).read() == out:
            return
    except OSError:
        os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
    open(HISTORY, "w").write(out)


def check():
    now = fetch()
    counts = None
    try:
        counts = fetch_counts()
        print(f"stock counts: {len(counts)} items")
    except Exception as e:
        print("stock count fetch failed:", e)
    try:
        record_history(now, counts, find_launches(now))
    except Exception as e:  # the dashboard must never stop the alerts
        print("history update failed:", e)
    if not os.path.exists(STATE):
        save_state(now)
        print(f"Baseline saved: {len(now)} items")
        return
    old = json.load(open(STATE))
    new = [h for h in now if h not in old and interesting(h, now[h])]
    back = [h for h in now if h in old and now[h]["available"] and not old[h]["available"]
            and interesting(h, now[h])]
    if new or back:
        lines = []
        for label, hs in (("NEW", new), ("BACK IN STOCK", back)):
            for h in hs:
                i = now[h]
                lines.append(f"[{label}] {i['title']}  ({i['sku']}, ${i['price']}, "
                             f"{'in stock' if i['available'] else 'sold out'})\n"
                             f"  https://creations.mattel.com/products/{h}")
        send(f"Hot Wheels alert: {len(new)} new, {len(back)} restocked", "\n\n".join(lines))
        print("\n".join(lines))
    save_state(now)


if __name__ == "__main__":
    if sys.argv[1:] == ["--test"]:
        send("Hot Wheels alert: test email",
             "This is a test from your Hot Wheels stock monitor. Real alerts look like:\n\n"
             "[BACK IN STOCK] Hot Wheels RLC Exclusive RWB Porsche 930  (JJY69, $38.00, in stock)\n"
             "  https://creations.mattel.com/products/hot-wheels-rlc-exclusive-rwb-porsche-930-jjy69")
        print("Test email sent")
        sys.exit(0)
    if len(sys.argv) in (3, 5) and sys.argv[1] == "--loop":
        # --loop SECONDS [--for TOTAL]: check every SECONDS, stopping after TOTAL seconds if given
        every = max(30, int(sys.argv[2]))
        stop = time.time() + int(sys.argv[4]) if len(sys.argv) == 5 and sys.argv[3] == "--for" else None
        while True:
            try:
                check()
            except Exception as e:
                print("check failed:", e)
            if stop is not None and time.time() + every > stop:
                break
            time.sleep(every)
    else:
        check()
