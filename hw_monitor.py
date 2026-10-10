#!/usr/bin/env python3
"""Hot Wheels Mattel Creations stock monitor.

Checks https://creations.mattel.com/collections/hot-wheels-collectors and emails
you when a new item appears or a sold-out item comes back in stock.

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
import json, os, smtplib, sys, time, urllib.request
from email.message import EmailMessage

URL = "https://creations.mattel.com/collections/hot-wheels-collectors/products.json?limit=250&page={}"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "hw_state.json")
HISTORY = os.path.join(HERE, "docs", "stock.json")  # read by the dashboard (docs/index.html)
WINDOW = 7 * 86400
TO = os.environ.get("HW_EMAIL_TO") or os.environ.get("HW_GMAIL_USER", "")
SKIP = ("t-shirt", "shirt", "hoodie", "jacket", "jersey", "sweatshirt", "mug", "hat", "luggage", "pin")
WATCH = {s.strip().upper() for s in os.environ.get("HW_WATCH_SKUS", "").split(",") if s.strip()}


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
            }
    return items


def interesting(handle, item):
    if WATCH:
        return item["sku"].upper() in WATCH
    return not any(w in item["title"].lower() for w in SKIP)


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


def record_history(now):
    """Keep 7 days of in/out-of-stock changes per item for the dashboard.

    The store does not publish stock counts, only whether each item can be bought,
    so each item keeps a list of [unix_time, 1 in stock / 0 sold out / -1 removed]
    changes. The newest change older than 7 days is kept so the dashboard knows each
    item's state for the whole week.
    """
    t = int(time.time())
    try:
        data = json.load(open(HISTORY))
    except (OSError, ValueError):
        data = {"items": {}}
    items = data["items"]
    for h, i in now.items():
        rec = items.setdefault(h, {"first_seen": t, "events": []})
        rec.update(title=i["title"], sku=i["sku"], price=i["price"], image=i["image"] or rec.get("image", ""))
        state = 1 if i["available"] else 0
        if not rec["events"] or rec["events"][-1][1] != state:
            rec["events"].append([t, state])
    for h, rec in items.items():
        if h not in now and rec["events"] and rec["events"][-1][1] != -1:
            rec["events"].append([t, -1])
    cutoff = t - WINDOW
    for h in list(items):
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
    try:
        record_history(now)
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
