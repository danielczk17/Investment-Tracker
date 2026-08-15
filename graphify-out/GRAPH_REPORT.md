# Graph Report - Investment Tracker  (2026-08-14)

## Corpus Check
- Corpus is ~20,255 words - fits in a single context window. You may not need a graph.

## Summary
- 299 nodes · 636 edges · 19 communities (18 shown, 1 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 32 edges (avg confidence: 0.87)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- app.js
- Main UI Jinja2 Template (index.html)
- showToast
- build_response
- fmt
- build_release.py
- app.py
- _portfolio_response
- route
- load_dividends
- load_sells
- renderSellLog
- symOf
- load_portfolio
- api_backfill
- renderDivLog
- renderLog
- _build_export_buf
- Investment Tracker README

## God Nodes (most connected - your core abstractions)
1. `Main UI Jinja2 Template (index.html)` - 22 edges
2. `showToast()` - 18 edges
3. `fmt()` - 17 edges
4. `esc()` - 15 edges
5. `renderDivLog()` - 15 edges
6. `renderSellLog()` - 15 edges
7. `load_portfolio()` - 14 edges
8. `api_backfill()` - 13 edges
9. `renderLog()` - 13 edges
10. `load_sells()` - 12 edges

## Surprising Connections (you probably didn't know these)
- `yfinance Yahoo Finance Python Library` --conceptually_related_to--> `Performance Benchmark Ticker Comparison (e.g. SPY, QQQ)`  [INFERRED]
  requirements.txt → templates/index.html
- `yfinance Yahoo Finance Python Library` --conceptually_related_to--> `Live Ticker Validation with Hint Display`  [INFERRED]
  requirements.txt → templates/index.html
- `Flask Web Framework` --conceptually_related_to--> `Main UI Jinja2 Template (index.html)`  [INFERRED]
  requirements.txt → templates/index.html
- `yfinance Yahoo Finance Python Library` --conceptually_related_to--> `Portfolio Performance Line Chart (Canvas-based)`  [INFERRED]
  requirements.txt → templates/index.html
- `pandas Data Analysis Library` --conceptually_related_to--> `Excel Import and Export Feature`  [INFERRED]
  requirements.txt → templates/index.html

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Transaction Record System: purchases, sales, and dividends collectively define portfolio P&L** — purchase_log, realized_gains, dividend_income [INFERRED 0.85]
- **Flask Web App Stack: Flask serves Jinja2 template which loads app.js for all UI logic** — flask, templates_index, static_app_js [INFERRED 0.85]
- **Yahoo Finance Data Pipeline: yfinance feeds ticker validation, performance chart, and benchmark comparison** — yfinance, ticker_validation, performance_chart, performance_benchmark [INFERRED 0.75]

## Communities (19 total, 1 thin omitted)

### Community 0 - "app.js"
Cohesion: 0.06
Nodes (27): addSettingsMarketRow(), applyTheme(), benchData, checkForUpdate(), CURR_SYM, filterSnapshots(), lastHoldings, MARKET_CCY (+19 more)

### Community 1 - "Main UI Jinja2 Template (index.html)"
Cohesion: 0.09
Nodes (29): Base Currency Setting for Multi-Currency Portfolio, Custom Confirm Dialog Modal (replaces browser confirm()), Dark Mode Appearance Toggle, Dashboard Tab (Performance, Holdings, Charts), Data Management Tab (Import/Export Excel), Dividend Income Log with Monthly Bar Chart, Excel Import and Export Feature, Send Feedback via Email Modal (+21 more)

### Community 2 - "showToast"
Cohesion: 0.15
Nodes (28): addDividend(), addSell(), backfillHistory(), cancelEdit(), clearTickerHint(), closeFeedback(), closeSectorModal(), deleteSelected() (+20 more)

### Community 3 - "build_response"
Cohesion: 0.11
Nodes (26): api_get_dividends(), api_get_sells(), api_set_sector_override(), api_validate_ticker(), build_response(), _fetch_yf(), get_company_name(), get_dividend_rate() (+18 more)

### Community 4 - "fmt"
Cohesion: 0.19
Nodes (22): buildPagination(), esc(), fmt(), fmtBase(), fmtCcy(), fmtK(), fmtPct(), openSectorEditor() (+14 more)

### Community 5 - "build_release.py"
Cohesion: 0.15
Nodes (20): build_pyinstaller(), bump_app_version(), check_gh_installed(), check_pyinstaller_installed(), create_github_release(), create_zip(), git_commit_and_push(), main() (+12 more)

### Community 6 - "app.py"
Cohesion: 0.13
Nodes (17): api_check_update(), api_get_settings(), api_save_settings(), apply_settings(), _do_update_check(), load_overrides(), load_settings(), Investment Tracker — Flask backend. Data is stored in JSON files in… (+9 more)

### Community 7 - "_portfolio_response"
Cohesion: 0.15
Nodes (14): api_get_snapshots(), api_portfolio(), api_refresh_prices(), load_snapshots(), _portfolio_response(), Return all monthly portfolio snapshots, or [] if the file is missing/corrupt., Upsert today's portfolio snapshot into the daily history. If an entry already…, Background thread: if purchases exist but snapshots are empty, rebuild history. (+6 more)

### Community 8 - "route"
Cohesion: 0.17
Nodes (13): api_benchmark(), api_import_template(), api_import_template_save(), api_search_ticker(), _build_import_template_buf(), index(), Build a blank import template workbook and return it as a BytesIO buffer., GET — stream the blank import template to the browser. (+5 more)

### Community 9 - "load_dividends"
Cohesion: 0.23
Nodes (12): api_add_dividend(), api_delete_dividend(), api_import_data(), api_update_dividend(), load_dividends(), POST multipart/form-data with field 'file' (.xlsx) — import purchases,…, Return all dividend records, or [] if the file is missing/corrupt., Persist the full dividends list to disk. (+4 more)

### Community 10 - "load_sells"
Cohesion: 0.21
Nodes (12): api_add_sell(), api_delete_sell(), api_update_sell(), _ensure_ids(), load_sells(), Stamp a uuid4 id onto any record that lacks one. Returns True if any were added., Return all sell records, or [] if the file is missing/corrupt., Persist the full sells list to disk. (+4 more)

### Community 11 - "renderSellLog"
Cohesion: 0.18
Nodes (12): cancelSellEdit(), colorCls(), getSellsSorted(), refreshRealizedTile(), renderSellLog(), setRealizedView(), setSellPage(), setSellSort() (+4 more)

### Community 12 - "symOf"
Cohesion: 0.23
Nodes (12): ccyOf(), fetchSettings(), populateDivForm(), populateMarketDropdowns(), populateSellForm(), startDivEdit(), startEdit(), startSellEdit() (+4 more)

### Community 13 - "load_portfolio"
Cohesion: 0.27
Nodes (10): api_add_purchase(), api_delete_purchase(), api_update_purchase(), load_portfolio(), Return all purchase records, or [] if the file is missing/corrupt., Persist the full purchases list to disk., POST — append a new purchase record. Body: {ticker, date, units, price_paid,…, PUT — overwrite the purchase with the given id. Body: same fields as POST. (+2 more)

### Community 14 - "api_backfill"
Cohesion: 0.28
Nodes (9): api_backfill(), api_snapshots_reset(), _get_hist_price(), POST — generate missing monthly snapshots by replaying historical prices. For…, Persist the full snapshots list to disk., Return the last closing price on or before target from a yfinance Close series., POST — clear all snapshots and rebuild from scratch via backfill. Returns same…, save_snapshots() (+1 more)

### Community 15 - "renderDivLog"
Cohesion: 0.29
Nodes (8): cancelDivEdit(), getDivsSorted(), renderDivLog(), setDivPage(), setDivSort(), toggleSelectAllDivs(), toggleSelectDiv(), updateDivToolbar()

### Community 16 - "renderLog"
Cohesion: 0.38
Nodes (7): getLogSorted(), renderLog(), setLogPage(), setLogSort(), toggleSelect(), toggleSelectAll(), updateLogToolbar()

### Community 17 - "_build_export_buf"
Cohesion: 0.33
Nodes (6): api_export_all(), api_export_save(), _build_export_buf(), Build the export workbook and return (BytesIO, filename)., GET — stream the export workbook to the browser., GET — save the export XLSX to the app data folder and open it (packaged app).…

## Knowledge Gaps
- **27 isolated node(s):** `_tickerValid`, `lastHoldings`, `selectedPurchases`, `sectorColorMap`, `selectedSells` (+22 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Flask Web Framework` connect `Main UI Jinja2 Template (index.html)` to `app.py`?**
  _High betweenness centrality (0.051) - this node is a cross-community bridge._
- **Why does `yfinance Yahoo Finance Python Library` connect `Main UI Jinja2 Template (index.html)` to `app.py`?**
  _High betweenness centrality (0.012) - this node is a cross-community bridge._
- **Are the 20 inferred relationships involving `Main UI Jinja2 Template (index.html)` (e.g. with `Flask Web Framework` and `Base Currency Setting for Multi-Currency Portfolio`) actually correct?**
  _`Main UI Jinja2 Template (index.html)` has 20 INFERRED edges - model-reasoned connections that need verification._
- **What connects `_tickerValid`, `lastHoldings`, `selectedPurchases` to the rest of the system?**
  _27 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `app.js` be split into smaller, more focused modules?**
  _Cohesion score 0.06401137980085349 - nodes in this community are weakly interconnected._
- **Should `Main UI Jinja2 Template (index.html)` be split into smaller, more focused modules?**
  _Cohesion score 0.09359605911330049 - nodes in this community are weakly interconnected._
- **Should `showToast` be split into smaller, more focused modules?**
  _Cohesion score 0.1455026455026455 - nodes in this community are weakly interconnected._