"""Daily credit-spread fetcher for the Credit Stress Monitor.

Pulls ICE BofA option-adjusted spreads and the S&P 500 from the FRED API
(api.stlouisfed.org) and writes spreads.json with ~2 years of daily history,
the latest values, distance to Wolfe's triggers, and a status.

Requires a free FRED API key in the FRED_API_KEY environment variable
(the GitHub workflow injects it from the FRED_API_KEY repo secret).
FRED's website CDN bot-blocks datacenter IPs, so the keyless CSV endpoint
cannot be fetched from automation — the API is the supported path.

Triggers (Josh Wolfe, @wolfejosh, 2026-10-12): CCC OAS > 13% and HY OAS > 3.5%
signal credit stress spreading to stocks. "Watch" = within the close band.
"""
import json
import os
import urllib.parse
import urllib.request
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
API_KEY = os.environ.get("FRED_API_KEY", "").strip()


def fetch_observations(series_id, start):
    """Fetch daily observations from the FRED API. Returns [[date, value], ...]."""
    qs = urllib.parse.urlencode({
        "series_id": series_id,
        "api_key": API_KEY,
        "file_type": "json",
        "observation_start": start,
        "sort_order": "asc",
    })
    req = urllib.request.Request(
        "https://api.stlouisfed.org/fred/series/observations?" + qs,
        headers={"User-Agent": "credit-stress-monitor/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.load(resp)
    rows = []
    for ob in payload.get("observations", []):
        v = ob["value"].strip()
        if v and v != ".":
            rows.append([ob["date"], float(v)])
    if not rows:
        raise RuntimeError("no observations returned for " + series_id)
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
    if not API_KEY:
        print("Spreads refresh {}: FRED_API_KEY is not set - keeping existing data file.".format(TODAY))
        print("Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html "
              "and add it as the FRED_API_KEY repo secret.")
        return
    cutoff = (date.today() - timedelta(days=HISTORY_DAYS)).isoformat()
    series = {}
    for key, sid in SERIES.items():
        try:
            rows = fetch_observations(sid, cutoff)
        except Exception as e:  # noqa: BLE001 - one bad series must not kill the run
            print(f" - {key} ({sid}): failed ({type(e).__name__}: {str(e)[:120]})")
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
