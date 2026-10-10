# Hot Wheels stock monitor

Checks the [Mattel Creations Hot Wheels collectors collection](https://creations.mattel.com/collections/hot-wheels-collectors) about once a minute with GitHub Actions (each 5-minute scheduled run checks every 60 seconds for 4.5 minutes) and emails when an item is new or back in stock. Apparel and merch are ignored.

## Setup

Add two repository secrets under **Settings → Secrets and variables → Actions → Secrets**:

- `HW_GMAIL_USER`: the Gmail address that sends and receives alerts
- `HW_GMAIL_APP_PASSWORD`: a Gmail App Password (https://myaccount.google.com/apppasswords, needs 2-Step Verification)

Optional repository variable `HW_WATCH_SKUS` (e.g. `JJY69,JKC06`) limits alerts to those item numbers.

The first run saves a baseline to `hw_state.json` and sends nothing. Later runs compare against it and commit changes back.

GitHub's 5-minute schedule is best effort; runs are often delayed, and scheduled workflows pause after 60 days without repository activity (state commits count as activity).

Because GitHub's scheduler has not been starting runs for this repository, each run also starts the next one when it finishes, so checks continue around the clock. To stop it, add a repository variable `HW_CHAIN` with the value `off` (Settings → Secrets and variables → Actions → Variables), or cancel the running job.

## Stock dashboard

`docs/index.html` is a searchable page of every item with its current status and a 7-day in-stock/sold-out timeline, served with GitHub Pages (Settings → Pages → Deploy from branch `main`, folder `/docs`). Each check also updates `docs/stock.json`, which keeps the last 7 days of status changes per item. The store does not publish unit counts, only whether an item can be bought, so the dashboard shows availability rather than quantities.
