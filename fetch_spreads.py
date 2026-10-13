"""Daily credit-spread fetcher for the Credit Stress Monitor.

Pulls ICE BofA option-adjusted spreads and the S&P 500 from FRED's public CSV
endpoint (no API key needed) and writes spreads.json with ~2 years of daily
history, the latest values, distance to Wolfe's triggers, and a status.

IMPORTANT: FRED's CDN bot-gates non-browser HTTP clients from datacenter IPs
(curl and Python's http stack hang forever), but serves real browsers fine.
So the CSVs are fetched through a headless Chromium (Playwright) instead of
urllib/requests/curl. Playwright is installed by the GitHub workflow.

Triggers (Josh Wolfe, @wolfejosh, 2026-10-12): CCC OAS > 13% and HY OAS > 3.5%
signal credit stress spreading to stocks. "Watch" = within the close band.
"""
import csv
import io
import json
import os
from datetime import date, timedelta

SERIES = {
    "ccc": "BAMLH0A3HYC",    # ICE BofA CCC & Lower US High Yield OAS, %
    "hy": "BAMLH0A0HYM2",    # ICE BofA US High Yield OAS, %
    "bbb": "BAMLC0A4CBBB",   # ICE BofA BBB US Corporate OAS, %
    "spx": "SP500",          # S&P 500 index
}
LABELS = {
    "ccc": "CCC OAS",
    "hy": "HY OAS",
    "bbb": "BBB OAS",
    "spx": "S&P 500",
}
TRIGGERS = {"ccc": 13.0, "hy": 3.5}
CLOSE_BAND = {"ccc": 0.5, "hy": 0.25}  # "getting close" = within this (pp) of trigger
HISTORY_DAYS = 730
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spreads.json")
TODAY = date.today().isoformat()


def fetch_one(page, sid):
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=" + sid
    try:
        # FRED serves the CSV as a file download. page.goto() raises
        # "Download is starting" when navigation turns into a download, so
        # trigger it via location.href inside expect_download instead.
        with page.expect_download(timeout=60000) as dl_info:
            page.evaluate("(url) => { window.location.href = url; }", url)
        text = open(dl_info.value.path()).read()
    except Exception:  # noqa: BLE001 - didn't download; maybe it rendered as a page
        page.goto(url, timeout=60000)
        text = page.evaluate("() => document.body.innerText")
    if "DATE," not in text:
        raise RuntimeError("unexpected content")
    return text


def fetch_csv_texts(series_ids):
    """Fetch FRED CSVs via headless Chromium (see module docstring for why)."""
    from playwright.sync_api import sync_playwright
    texts = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.goto("https://fred.stlouisfed.org/", timeout=90000)
            for sid in series_ids:
                try:
                    texts[sid] = fetch_one(page, sid)
                except Exception as e:  # noqa: BLE001 - one bad series must not kill the run
                    print(f" - {sid}: browser fetch failed ({type(e).__name__}: {str(e)[:100]})")
        finally:
            browser.close()
    return texts


def parse_csv(series_id, text):
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        d = row["DATE"].strip()
        v = row[series_id].strip()
        if v and v != ".":
            rows.append([d, float(v)])
    if not rows:
        raise RuntimeError("empty CSV for " + series_id)
    return rows


def pct_change(history, days_back):
    """Percent change of the last value vs the value ~days_back trading days earlier."""
    if len(history) < days_back + 1:
        return None
    old = history[-1 - days_back][1]
    new = history[-1][1]
    return (new / old - 1) if old else None


def status_of(ccc, hy):
    if ccc is None or hy is None:
        return "unknown"
    if ccc > TRIGGERS["ccc"] or hy > TRIGGERS["hy"]:
        return "triggered"
    if ccc >= TRIGGERS["ccc"] - CLOSE_BAND["ccc"] or hy >= TRIGGERS["hy"] - CLOSE_BAND["hy"]:
        return "watch"
    return "calm"


def main():
    cutoff = (date.today() - timedelta(days=HISTORY_DAYS)).isoformat()
    try:
        texts = fetch_csv_texts(list(SERIES.values()))
    except Exception as e:  # noqa: BLE001 - keep the existing file on total failure
        print(f" - browser fetch failed ({type(e).__name__}): {str(e)[:150]}")
        texts = {}
    series = {}
    for key, sid in SERIES.items():
        try:
            rows = [r for r in parse_csv(sid, texts[sid]) if r[0] >= cutoff]
        except Exception as e:  # noqa: BLE001 - one bad series must not kill the run
            print(f" - {key} ({sid}): failed ({type(e).__name__})")
            rows = []
        series[key] = {"id": sid, "label": LABELS[key], "history": rows}

    latest = {}
    for key, s in series.items():
        h = s["history"]
        latest[key] = {
            "date": h[-1][0] if h else None,
            "value": h[-1][1] if h else None,
            "chg_5d_pct": pct_change(h, 5),
            "chg_1m_pct": pct_change(h, 21),
        }

    ccc_v = latest["ccc"]["value"]
    hy_v = latest["hy"]["value"]
    if ccc_v is None or hy_v is None:
        if os.path.exists(DATA_FILE):
            print(f"Spreads refresh {TODAY}: CCC/HY fetch failed - keeping existing data file.")
            return
        # no existing file and no data: write what we have so the page has something
    out = {
        "asof": TODAY,
        "triggers": TRIGGERS,
        "close_band": CLOSE_BAND,
        "status": status_of(ccc_v, hy_v),
        "gaps": {
            "ccc": TRIGGERS["ccc"] - ccc_v if ccc_v is not None else None,
            "hy": TRIGGERS["hy"] - hy_v if hy_v is not None else None,
        },
        "latest": latest,
        "series": series,
    }
    with open(DATA_FILE, "w") as fh:
        json.dump(out, fh)
        fh.write("\n")

    def fgap(g):
        return f"{g:+.2f}pp" if g is not None else "n/a"

    print(f"Spreads refresh {TODAY}: status={out['status']}, "
          f"CCC={ccc_v} (gap {fgap(out['gaps']['ccc'])}), "
          f"HY={hy_v} (gap {fgap(out['gaps']['hy'])})")


if __name__ == "__main__":
    main()
