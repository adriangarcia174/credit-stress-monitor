# Credit Stress Monitor

Watches Josh Wolfe's credit-stress triggers (**@wolfejosh**, Oct 12 2026):
**CCC OAS > 13%** and **HY OAS > 3.5%** = credit stress spreading to stocks.

- **Status**: `calm` normally, `watch` when within 0.50pp (CCC) / 0.25pp (HY) of a
  trigger, `triggered` when a trigger is hit.
- **Site**: https://adriangarcia174.github.io/credit-stress-monitor/

## How it works

- **`index.html`** — the dashboard. On every load it fetches `spreads.json` (same site,
  so no cross-origin issues) and falls back to an embedded snapshot if offline.
  No API keys anywhere — FRED's public CSV feed needs none.
- **`spreads.json`** — the dataset: ~2 years of daily ICE BofA option-adjusted
  spreads (CCC, HY, BBB) plus the S&P 500, latest values, gaps to triggers, status.
- **`fetch_spreads.py`** — the updater. Downloads the four FRED series
  (`BAMLH0A3HYC`, `BAMLH0A0HYM2`, `BAMLC0A4CBBB`, `SP500`) and writes
  `spreads.json`. Stdlib + curl only. If a fetch fails it keeps the existing file
  rather than writing empty data.
- **`.github/workflows/spreads-refresh.yml`** — runs the updater daily at 14:00 UTC
  (09:00 CT, after FRED's morning update) plus manually anytime, and commits
  `spreads.json` when something changed.
