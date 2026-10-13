"""Daily credit-spread fetcher for the Credit Stress Monitor.

Pulls ICE BofA option-adjusted spreads and the S&P 500 from FRED's public CSV
endpoint (no API key needed) and writes spreads.json with ~2 years of daily
history, the latest values, distance to Wolfe's triggers, and a status.

Triggers (Josh Wolfe, @wolfejosh, 2026-10-12): CCC OAS > 13% and HY OAS > 3.5%
signal credit stress spreading to stocks. "Watch" = within the close band.
"""
import csv
import io
import json
import os
import subprocess
import time
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


def fetch_csv(series_id, tries=4):
    # NOTE: FRED's CDN stalls Python's http.client (urllib/requests) on read;
    # curl handles the same URL fine, so shell out to it. curl exists on the
    # GitHub Actions ubuntu runner too. Retries with backoff: FRED throttles
    # rapid repeat hits from one IP.
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=" + series_id
    last_err = None
    for attempt in range(tries):
        if attempt:
            time.sleep(5 * attempt)
        try:
            p = subprocess.run(
                ["curl", "-s", "--http1.1", "--max-time", "45", "-A", "Mozilla/5.0", url],
                capture_output=True, text=True, timeout=120)
            if p.returncode != 0:
                raise RuntimeError(f"curl rc={p.returncode}: {p.stderr.strip()[:120]}")
            rows = []
            for row in csv.DictReader(io.StringIO(p.stdout)):
                d = row["DATE"].strip()
                v = row[series_id].strip()
                if v and v != ".":
                    rows.append([d, float(v)])
            if not rows:
                raise RuntimeError("empty CSV for " + series_id)
            return rows
        except Exception as e:  # noqa: BLE001 - retry, then let the caller handle it
            last_err = e
            print(f" - {series_id}: attempt {attempt + 1} failed ({type(e).__name__})")
    raise RuntimeError(f"{series_id} failed after {tries} tries: {last_err}")


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
    series = {}
    for key, sid in SERIES.items():
        try:
            rows = [r for r in fetch_csv(sid) if r[0] >= cutoff]
        except Exception as e:  # noqa: BLE001 - one bad series must not kill the run
            print(f" - {key} ({sid}): fetch failed ({type(e).__name__})")
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
    print(f"Spreads refresh {TODAY}: status={out['status']}, "
          f"CCC={ccc_v} (gap {out['gaps']['ccc']:+.2f}pp), "
          f"HY={hy_v} (gap {out['gaps']['hy']:+.2f}pp)")


if __name__ == "__main__":
    main()
