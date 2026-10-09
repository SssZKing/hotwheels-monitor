# Hot Wheels stock monitor

Checks the [Mattel Creations Hot Wheels collectors collection](https://creations.mattel.com/collections/hot-wheels-collectors) about once a minute with GitHub Actions (each 5-minute scheduled run checks every 60 seconds for 4.5 minutes) and emails when an item is new or back in stock. Apparel and merch are ignored.

## Setup

Add two repository secrets under **Settings → Secrets and variables → Actions → Secrets**:

- `HW_GMAIL_USER`: the Gmail address that sends and receives alerts
- `HW_GMAIL_APP_PASSWORD`: a Gmail App Password (https://myaccount.google.com/apppasswords, needs 2-Step Verification)

Optional repository variable `HW_WATCH_SKUS` (e.g. `JJY69,JKC06`) limits alerts to those item numbers.

The first run saves a baseline to `hw_state.json` and sends nothing. Later runs compare against it and commit changes back.

GitHub's 5-minute schedule is best effort; runs are often delayed, and scheduled workflows pause after 60 days without repository activity (state commits count as activity).
