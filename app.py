"""
Investment Tracker — Flask backend.

Data is stored in JSON files in %APPDATA%\InvestmentTracker\ (packaged) or
alongside this script (dev mode):
  portfolio.json        — purchase records
  sells.json            — sell records
  dividends.json        — dividend records
  snapshots.json        — monthly portfolio value history
  sector_overrides.json — manually assigned sector/category labels

All monetary totals are expressed in BASE_CURRENCY (SGD by default).
Live prices and FX rates are fetched via yfinance and cached in memory.
"""

import calendar
import io
import json
import os
import sys
import time
import threading
import uuid
from datetime import datetime, date as _date, timedelta
from flask import Flask, jsonify, request, render_template, send_file
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# CONFIG — edit these values to match your setup
# ---------------------------------------------------------------------------
PRICE_CACHE_TTL = 300     # Seconds to cache live prices (default: 5 minutes)
PORT            = int(os.environ.get("PORT", 5000))  # overridden by launcher via env var
APP_VERSION     = "1.0.0"
# URL of the version.json you host on GitHub.
# After creating your GitHub repo, replace this with:
#   https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/version.json
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/danielczk17/Investment-Tracker/main/version.json"
# ---------------------------------------------------------------------------

# All currencies that can be chosen as the base currency
ALL_CURRENCY_SYMBOLS: dict[str, str] = {
    "SGD": "S$",  "USD": "US$", "EUR": "€",   "GBP": "£",
    "JPY": "¥",   "AUD": "A$",  "CAD": "C$",  "HKD": "HK$",
    "CHF": "CHF", "NZD": "NZ$",
}
SUPPORTED_CURRENCIES: list[str] = list(ALL_CURRENCY_SYMBOLS.keys())

# Default market definitions — overridden by settings.json at runtime
_DEFAULT_MARKETS: list[dict] = [
    {"name": "SGX",   "currency": "SGD", "suffix": ".SI"},
    {"name": "US",    "currency": "USD", "suffix": ""},
    {"name": "World", "currency": "USD", "suffix": ".L"},
]

# Runtime globals — updated by apply_settings() on startup and on every PUT /api/settings
BASE_CURRENCY:   str        = "SGD"
CURRENCY_SYMBOL: dict       = ALL_CURRENCY_SYMBOLS
MARKET_CURRENCY: dict       = {m["name"]: m["currency"] for m in _DEFAULT_MARKETS}
MARKET_SUFFIX:   dict       = {m["name"]: m.get("suffix", "") for m in _DEFAULT_MARKETS}
VALID_MARKETS:   tuple      = tuple(m["name"] for m in _DEFAULT_MARKETS)

# ---------------------------------------------------------------------------
# Path resolution — works both in development and when frozen by PyInstaller.
# When frozen: templates are in sys._MEIPASS (bundled read-only),
#              data files are next to the .exe (writable).
# When in development: everything is relative to this script file.
# ---------------------------------------------------------------------------
IS_FROZEN = getattr(sys, 'frozen', False)

if IS_FROZEN:
    BASE_DIR     = os.path.dirname(sys.executable)
    TEMPLATE_DIR = os.path.join(sys._MEIPASS, 'templates')
    STATIC_DIR   = os.path.join(sys._MEIPASS, 'static')
    # Store user data in %APPDATA%\InvestmentTracker — standard Windows location, works on any machine
    DATA_DIR     = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'InvestmentTracker')
else:
    BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
    TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
    STATIC_DIR   = os.path.join(BASE_DIR, 'static')
    DATA_DIR     = BASE_DIR

os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)

PORTFOLIO_FILE  = os.path.join(DATA_DIR, "portfolio.json")
DIVIDENDS_FILE  = os.path.join(DATA_DIR, "dividends.json")
SNAPSHOTS_FILE  = os.path.join(DATA_DIR, "snapshots.json")
SELLS_FILE      = os.path.join(DATA_DIR, "sells.json")
OVERRIDES_FILE  = os.path.join(DATA_DIR, "sector_overrides.json")
SETTINGS_FILE   = os.path.join(DATA_DIR, "settings.json")

_price_cache:     dict = {}  # { yf_symbol:  { 'price':  float|None, 'ts': float } }
_sector_cache:    dict = {}  # { yf_symbol:  { 'sector': str,        'ts': float } }
_sector_overrides: dict = {}  # { yf_symbol: str } — user-defined category overrides
SECTOR_CACHE_TTL = 86400  # 24 hours — sectors rarely change

# Serialises all load→modify→save sequences so rapid double-clicks can't interleave writes.
_write_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _ensure_ids(records: list) -> bool:
    """Stamp a uuid4 id onto any record that lacks one. Returns True if any were added."""
    modified = False
    for r in records:
        if "id" not in r:
            r["id"] = str(uuid.uuid4())
            modified = True
    return modified


def load_portfolio() -> list:
    """Return all purchase records, or [] if the file is missing/corrupt."""
    if not os.path.exists(PORTFOLIO_FILE):
        return []
    with open(PORTFOLIO_FILE, "r") as f:
        try:
            records = json.load(f)
        except json.JSONDecodeError:
            return []
    if _ensure_ids(records):
        save_portfolio(records)
    return records


def save_portfolio(purchases: list) -> None:
    """Persist the full purchases list to disk."""
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(purchases, f, indent=2)


def load_dividends() -> list:
    """Return all dividend records, or [] if the file is missing/corrupt."""
    if not os.path.exists(DIVIDENDS_FILE):
        return []
    with open(DIVIDENDS_FILE, "r") as f:
        try:
            records = json.load(f)
        except json.JSONDecodeError:
            return []
    if _ensure_ids(records):
        save_dividends(records)
    return records


def save_dividends(dividends: list) -> None:
    """Persist the full dividends list to disk."""
    with open(DIVIDENDS_FILE, "w") as f:
        json.dump(dividends, f, indent=2)


def load_snapshots() -> list:
    """Return all monthly portfolio snapshots, or [] if the file is missing/corrupt."""
    if not os.path.exists(SNAPSHOTS_FILE):
        return []
    with open(SNAPSHOTS_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def save_snapshots(snapshots: list) -> None:
    """Persist the full snapshots list to disk."""
    with open(SNAPSHOTS_FILE, "w") as f:
        json.dump(snapshots, f, indent=2)


def load_sells() -> list:
    """Return all sell records, or [] if the file is missing/corrupt."""
    if not os.path.exists(SELLS_FILE):
        return []
    with open(SELLS_FILE, "r") as f:
        try:
            records = json.load(f)
        except json.JSONDecodeError:
            return []
    if _ensure_ids(records):
        save_sells(records)
    return records


def save_sells(sells: list) -> None:
    """Persist the full sells list to disk."""
    with open(SELLS_FILE, "w") as f:
        json.dump(sells, f, indent=2)


def load_overrides() -> dict:
    """Return the sector override map {yf_symbol: sector_label}, or {} if missing/corrupt."""
    if not os.path.exists(OVERRIDES_FILE):
        return {}
    with open(OVERRIDES_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def save_overrides(overrides: dict) -> None:
    """Persist the sector overrides map to disk."""
    with open(OVERRIDES_FILE, "w") as f:
        json.dump(overrides, f, indent=2)


_sector_overrides = load_overrides()


def load_settings() -> dict:
    """Return persisted settings from disk, falling back to built-in defaults."""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"base_currency": "SGD", "markets": _DEFAULT_MARKETS, "benchmark_ticker": "SPY"}


def save_settings_file(settings: dict) -> None:
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


def apply_settings(settings: dict) -> None:
    """Update runtime globals from a settings dict."""
    global BASE_CURRENCY, VALID_MARKETS, MARKET_CURRENCY, MARKET_SUFFIX
    BASE_CURRENCY   = settings.get("base_currency", "SGD")
    markets         = settings.get("markets", _DEFAULT_MARKETS)
    VALID_MARKETS   = tuple(m["name"] for m in markets)
    MARKET_CURRENCY = {m["name"]: m["currency"] for m in markets}
    MARKET_SUFFIX   = {m["name"]: m.get("suffix", "") for m in markets}


# Apply saved settings on startup
apply_settings(load_settings())


def record_snapshot(value: float, invested: float) -> None:
    """Upsert today's portfolio snapshot into the monthly history.

    If an entry already exists for the current calendar month it is replaced
    with the latest values, so each month keeps only one (the most recent) entry.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    this_month = today[:7]  # "YYYY-MM"
    snapshots = load_snapshots()
    entry = {"date": today, "value": round(value, 2), "invested": round(invested, 2)}
    if snapshots and snapshots[-1]["date"][:7] == this_month:
        snapshots[-1] = entry   # update this month's entry with latest prices
    else:
        snapshots.append(entry)
    save_snapshots(snapshots)


# ---------------------------------------------------------------------------
# Price & FX fetching
# ---------------------------------------------------------------------------

def resolve_yf_symbol(ticker: str, market: str) -> str:
    """Return the yfinance-ready symbol for a ticker/market pair."""
    t      = ticker.upper()
    suffix = MARKET_SUFFIX.get(market, "")
    if suffix and not t.endswith(suffix):
        return t + suffix
    return t


def _fetch_yf(symbol: str) -> float | None:
    """Fetch latest price for any yfinance symbol (stock or FX pair)."""
    now = time.time()
    cached = _price_cache.get(symbol)
    if cached and now - cached["ts"] < PRICE_CACHE_TTL:
        return cached["price"]

    price = None
    try:
        t = yf.Ticker(symbol)
        try:
            lp = t.fast_info.last_price
            if lp is not None and float(lp) > 0:
                price = float(lp)
        except Exception:
            pass
        if price is None:
            hist = t.history(period="5d")
            if not hist.empty:
                closes = hist["Close"].dropna()
                if not closes.empty:
                    price = float(closes.iloc[-1])
    except Exception:
        pass

    _price_cache[symbol] = {"price": price, "ts": now}
    return price


def get_price(ticker: str, market: str) -> float | None:
    """Return the latest price for a ticker/market pair (cached for PRICE_CACHE_TTL seconds)."""
    return _fetch_yf(resolve_yf_symbol(ticker, market))


def get_sector(ticker: str, market: str) -> str:
    """Return the sector/industry label for a ticker. Cached for 24 h."""
    symbol = resolve_yf_symbol(ticker, market)
    if symbol in _sector_overrides:
        return _sector_overrides[symbol]
    now = time.time()
    cached = _sector_cache.get(symbol)
    if cached and now - cached["ts"] < SECTOR_CACHE_TTL:
        return cached["sector"]

    sector   = "Unknown"
    div_rate = None
    name     = ""
    try:
        info = yf.Ticker(symbol).info
        if info.get("quoteType") == "ETF":
            sector = "ETF"
        else:
            sector = info.get("sector") or "Unknown"
        div_rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate") or None
        if div_rate is not None:
            div_rate = float(div_rate) if float(div_rate) > 0 else None
        name = info.get("longName") or info.get("shortName") or ""
    except Exception:
        pass

    _sector_cache[symbol] = {"sector": sector, "div_rate": div_rate, "name": name, "ts": now}
    return sector


def get_company_name(ticker: str, market: str) -> str:
    """Return the company/fund display name for a ticker. Populated as a side-effect of get_sector."""
    symbol = resolve_yf_symbol(ticker, market)
    cached = _sector_cache.get(symbol)
    if cached:
        return cached.get("name", "")
    get_sector(ticker, market)
    return _sector_cache.get(symbol, {}).get("name", "")


def get_dividend_rate(ticker: str, market: str) -> float | None:
    """Return annual dividend per share in local currency. Cached with sector."""
    symbol = resolve_yf_symbol(ticker, market)
    cached = _sector_cache.get(symbol)
    if cached and time.time() - cached["ts"] < SECTOR_CACHE_TTL:
        return cached.get("div_rate")
    get_sector(ticker, market)  # populates cache including div_rate
    return _sector_cache.get(symbol, {}).get("div_rate")


def get_fx_rate(from_ccy: str, to_ccy: str) -> float | None:
    """Return rate so that: amount_from * rate = amount_to. Returns 1.0 for same currency."""
    if from_ccy == to_ccy:
        return 1.0
    return _fetch_yf(f"{from_ccy}{to_ccy}=X")


# ---------------------------------------------------------------------------
# Portfolio calculation
# ---------------------------------------------------------------------------

def build_response(purchases: list, sells: list | None = None) -> dict:
    """Compute the full portfolio state from raw purchases and sells.

    Aggregates units and cost basis per ticker (average-cost method), subtracts
    sold units, fetches live prices and FX rates, then returns a dict containing:
      holdings       — per-ticker position details
      totals         — portfolio-level invested/value/gain totals in BASE_CURRENCY
      purchases      — the original purchase records (passed through unchanged)
      base_currency  — the currency all base totals are expressed in
      currency_symbol — display symbols keyed by currency code
      fx_rates       — non-base FX rates used (for the frontend FX note)
      last_updated   — timestamp string
    """
    if sells is None:
        sells = []

    # Aggregate per ticker from buys
    agg: dict = {}
    for p in purchases:
        key = p["ticker"].upper()
        if key not in agg:
            agg[key] = {"ticker": key, "market": p["market"], "total_units": 0.0, "total_cost": 0.0}
        agg[key]["total_units"] += float(p["units"])
        agg[key]["total_cost"] += float(p["units"]) * float(p["price_paid"])

    # Aggregate sold units per ticker
    sold_units: dict[str, float] = {}
    for s in sells:
        key = s["ticker"].upper()
        sold_units[key] = sold_units.get(key, 0.0) + float(s["units"])

    # Fetch FX rates for every currency that appears in the portfolio
    unique_currencies = {MARKET_CURRENCY.get(h["market"], "USD") for h in agg.values()}
    fx_to_base: dict[str, float | None] = {
        ccy: get_fx_rate(ccy, BASE_CURRENCY) for ccy in unique_currencies
    }

    holdings = []
    total_invested_base  = 0.0
    total_current_value_base = 0.0

    for h in agg.values():
        total_units_bought = h["total_units"]
        avg_price          = h["total_cost"] / total_units_bought if total_units_bought else 0.0

        # Remaining units after sells; skip fully exited positions
        units = total_units_bought - sold_units.get(h["ticker"], 0.0)
        if units <= 1e-9:
            continue

        cost          = avg_price * units   # cost basis of remaining shares
        currency      = MARKET_CURRENCY.get(h["market"], "USD")
        rate          = fx_to_base.get(currency)
        current_price = get_price(h["ticker"], h["market"])

        if current_price is not None:
            current_value = current_price * units
            gl_amt = current_value - cost
            gl_pct = (gl_amt / cost * 100) if cost else 0.0
        else:
            current_value = gl_amt = gl_pct = None

        # Base-currency equivalents for sorting and totals
        cost_base          = cost * rate          if rate is not None else None
        current_value_base = current_value * rate if (current_value is not None and rate is not None) else None

        if cost_base is not None:
            total_invested_base += cost_base
        if current_value_base is not None:
            total_current_value_base += current_value_base

        div_rate    = get_dividend_rate(h["ticker"], h["market"])
        yield_cost  = (div_rate / avg_price  * 100) if (div_rate and avg_price  > 0) else None
        yield_mkt   = (div_rate / current_price * 100) if (div_rate and current_price and current_price > 0) else None

        holdings.append({
            "ticker":             h["ticker"],
            "market":             h["market"],
            "currency":           currency,
            "sector":             get_sector(h["ticker"], h["market"]),
            "company_name":       get_company_name(h["ticker"], h["market"]),
            "total_units":        units,
            "avg_price":          avg_price,
            "total_invested":     cost,
            "current_price":      current_price,
            "current_value":      current_value,
            "current_value_base": current_value_base,
            "gain_loss_amount":   gl_amt,
            "gain_loss_pct":      gl_pct,
            "div_rate":           div_rate,
            "yield_on_cost":      round(yield_cost, 2) if yield_cost is not None else None,
            "current_yield":      round(yield_mkt,  2) if yield_mkt  is not None else None,
        })

    # Sort by base-currency value descending; N/A entries go to the bottom
    holdings.sort(key=lambda x: (x["current_value_base"] is None, -(x["current_value_base"] or 0)))

    overall_gl     = total_current_value_base - total_invested_base
    overall_gl_pct = (overall_gl / total_invested_base * 100) if total_invested_base else 0.0

    # Only expose non-base FX rates to the frontend
    fx_rates_out = {
        ccy: rate for ccy, rate in fx_to_base.items()
        if ccy != BASE_CURRENCY and rate is not None
    }

    purchases_out = [
        {**p, "company_name": get_company_name(p["ticker"], p["market"])}
        for p in purchases
    ]

    return {
        "purchases":       purchases_out,
        "holdings":        holdings,
        "totals": {
            "total_invested":      total_invested_base,
            "total_current_value": total_current_value_base,
            "gain_loss_amount":    overall_gl,
            "gain_loss_pct":       overall_gl_pct,
        },
        "base_currency":   BASE_CURRENCY,
        "currency_symbol": CURRENCY_SYMBOL,
        "fx_rates":        fx_rates_out,
        "last_updated":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


_auto_backfill_done = False

def _run_auto_backfill():
    """Background thread: if purchases exist but snapshots are empty, rebuild history."""
    global _auto_backfill_done
    if _auto_backfill_done:
        return
    _auto_backfill_done = True
    try:
        if load_portfolio() and not load_snapshots():
            with app.test_request_context():
                api_backfill()
    except Exception:
        pass


def _portfolio_response() -> dict:
    """Build the portfolio response and record a snapshot if prices are available."""
    result = build_response(load_portfolio(), load_sells())
    tv = result["totals"]["total_current_value"]
    ti = result["totals"]["total_invested"]
    if tv is not None and tv > 0:
        record_snapshot(tv, ti)
    # Kick off auto-backfill once if snapshots are missing
    if not _auto_backfill_done:
        threading.Thread(target=_run_auto_backfill, daemon=True).start()
    return result


@app.route("/api/portfolio")
def api_portfolio():
    """GET — return full portfolio state (holdings, totals, FX rates)."""
    return jsonify(_portfolio_response())


@app.route("/api/purchase", methods=["POST"])
def api_add_purchase():
    """POST — append a new purchase record. Body: {ticker, date, units, price_paid, market}."""
    data = request.get_json(force=True) or {}
    for field in ("ticker", "date", "units", "price_paid", "market"):
        if field not in data:
            return jsonify({"error": f"Missing field: {field}"}), 400
    if data["market"] not in VALID_MARKETS:
        return jsonify({"error": f'market must be one of: {", ".join(VALID_MARKETS)}'}), 400

    purchase = {
        "id":         str(uuid.uuid4()),
        "ticker":     str(data["ticker"]).upper().strip(),
        "date":       str(data["date"]),
        "units":      float(data["units"]),
        "price_paid": float(data["price_paid"]),
        "market":     data["market"],
    }

    with _write_lock:
        purchases = load_portfolio()
        purchases.append(purchase)
        save_portfolio(purchases)
    return jsonify({"success": True, "purchase": purchase}), 201


@app.route("/api/purchase/<string:record_id>", methods=["PUT"])
def api_update_purchase(record_id):
    """PUT — overwrite the purchase with the given id. Body: same fields as POST."""
    data = request.get_json(force=True) or {}
    for field in ("ticker", "date", "units", "price_paid", "market"):
        if field not in data:
            return jsonify({"error": f"Missing field: {field}"}), 400
    if data["market"] not in VALID_MARKETS:
        return jsonify({"error": f'market must be one of: {", ".join(VALID_MARKETS)}'}), 400
    with _write_lock:
        purchases = load_portfolio()
        idx = next((i for i, p in enumerate(purchases) if p.get("id") == record_id), None)
        if idx is None:
            return jsonify({"error": "Record not found"}), 404
        purchases[idx] = {
            "id":         record_id,
            "ticker":     str(data["ticker"]).upper().strip(),
            "date":       str(data["date"]),
            "units":      float(data["units"]),
            "price_paid": float(data["price_paid"]),
            "market":     data["market"],
        }
        save_portfolio(purchases)
    return jsonify({"success": True, "purchase": purchases[idx]})


@app.route("/api/purchase/<string:record_id>", methods=["DELETE"])
def api_delete_purchase(record_id):
    """DELETE — remove the purchase with the given id."""
    with _write_lock:
        purchases = load_portfolio()
        idx = next((i for i, p in enumerate(purchases) if p.get("id") == record_id), None)
        if idx is None:
            return jsonify({"error": "Record not found"}), 404
        purchases.pop(idx)
        save_portfolio(purchases)
    return jsonify({"success": True})


@app.route("/api/sector-override", methods=["PUT"])
def api_set_sector_override():
    """PUT — save a manual sector/category label for a ticker. Body: {ticker, market, sector}.
    Clears the sector cache entry so the new label takes effect immediately.
    """
    global _sector_overrides
    data = request.get_json(force=True) or {}
    ticker = str(data.get("ticker", "")).upper().strip()
    market = str(data.get("market", ""))
    sector = str(data.get("sector", "")).strip()
    if not ticker or not market or not sector:
        return jsonify({"error": "ticker, market, and sector required"}), 400
    if market not in VALID_MARKETS:
        return jsonify({"error": f"market must be one of: {', '.join(VALID_MARKETS)}"}), 400
    symbol = resolve_yf_symbol(ticker, market)
    _sector_overrides[symbol] = sector
    save_overrides(_sector_overrides)
    _sector_cache.pop(symbol, None)
    return jsonify({"success": True, "symbol": symbol, "sector": sector})


@app.route("/api/dividends")
def api_get_dividends():
    """GET — return all dividend records with FX-converted base amounts and a running total."""
    dividends = load_dividends()
    result = []
    total_base = 0.0
    for d in dividends:
        ccy  = MARKET_CURRENCY.get(d["market"], "USD")
        rate = get_fx_rate(ccy, BASE_CURRENCY)
        amount_base = d["amount"] * rate if rate is not None else None
        if amount_base is not None:
            total_base += amount_base
        result.append({**d, "currency": ccy, "amount_base": amount_base,
                        "company_name": get_company_name(d["ticker"], d["market"])})
    return jsonify({
        "dividends":       result,
        "total_base":      total_base,
        "base_currency":   BASE_CURRENCY,
        "currency_symbol": CURRENCY_SYMBOL,
    })


@app.route("/api/dividend", methods=["POST"])
def api_add_dividend():
    """POST — append a new dividend record. Body: {ticker, date, amount, market}."""
    data = request.get_json(force=True) or {}
    for field in ("ticker", "date", "amount", "market"):
        if field not in data:
            return jsonify({"error": f"Missing field: {field}"}), 400
    if data["market"] not in VALID_MARKETS:
        return jsonify({"error": f'market must be one of: {", ".join(VALID_MARKETS)}'}), 400
    dividend = {
        "id":     str(uuid.uuid4()),
        "ticker": str(data["ticker"]).upper().strip(),
        "date":   str(data["date"]),
        "amount": float(data["amount"]),
        "market": data["market"],
    }
    with _write_lock:
        dividends = load_dividends()
        dividends.append(dividend)
        save_dividends(dividends)
    return jsonify({"success": True, "dividend": dividend}), 201


@app.route("/api/dividend/<string:record_id>", methods=["PUT"])
def api_update_dividend(record_id):
    """PUT — overwrite the dividend record with the given id. Body: same fields as POST."""
    data = request.get_json(force=True) or {}
    for field in ("ticker", "date", "amount", "market"):
        if field not in data:
            return jsonify({"error": f"Missing field: {field}"}), 400
    if data["market"] not in VALID_MARKETS:
        return jsonify({"error": f'market must be one of: {", ".join(VALID_MARKETS)}'}), 400
    with _write_lock:
        dividends = load_dividends()
        idx = next((i for i, d in enumerate(dividends) if d.get("id") == record_id), None)
        if idx is None:
            return jsonify({"error": "Record not found"}), 404
        dividends[idx] = {
            "id":     record_id,
            "ticker": str(data["ticker"]).upper().strip(),
            "date":   str(data["date"]),
            "amount": float(data["amount"]),
            "market": data["market"],
        }
        save_dividends(dividends)
    return jsonify({"success": True, "dividend": dividends[idx]})


@app.route("/api/dividend/<string:record_id>", methods=["DELETE"])
def api_delete_dividend(record_id):
    """DELETE — remove the dividend with the given id."""
    with _write_lock:
        dividends = load_dividends()
        idx = next((i for i, d in enumerate(dividends) if d.get("id") == record_id), None)
        if idx is None:
            return jsonify({"error": "Record not found"}), 404
        dividends.pop(idx)
        save_dividends(dividends)
    return jsonify({"success": True})


@app.route("/api/sells")
def api_get_sells():
    """GET — return all sell records enriched with avg cost, realized gain, and base-currency gain."""
    purchases = load_portfolio()
    sells     = load_sells()

    # Overall average cost per ticker from all buys
    buy_agg: dict = {}
    for p in purchases:
        key = p["ticker"].upper()
        if key not in buy_agg:
            buy_agg[key] = {"units": 0.0, "cost": 0.0}
        buy_agg[key]["units"] += float(p["units"])
        buy_agg[key]["cost"]  += float(p["units"]) * float(p["price_paid"])
    avg_costs = {k: v["cost"] / v["units"] if v["units"] else 0.0 for k, v in buy_agg.items()}

    result: list = []
    total_realized_base = 0.0

    for s in sells:
        key      = s["ticker"].upper()
        currency = MARKET_CURRENCY.get(s["market"], "USD")
        avg_cost = avg_costs.get(key, 0.0)
        units    = float(s["units"])
        sell_px  = float(s["price_sold"])

        realized_gain     = (sell_px - avg_cost) * units
        realized_gain_pct = ((sell_px - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0.0

        rate               = get_fx_rate(currency, BASE_CURRENCY)
        realized_gain_base = realized_gain * rate if rate is not None else None

        if realized_gain_base is not None:
            total_realized_base += realized_gain_base

        result.append({
            **s,
            "currency":           currency,
            "avg_cost":           round(avg_cost, 4),
            "realized_gain":      round(realized_gain, 2),
            "realized_gain_pct":  round(realized_gain_pct, 2),
            "realized_gain_base": round(realized_gain_base, 2) if realized_gain_base is not None else None,
            "company_name":       get_company_name(s["ticker"], s["market"]),
        })

    return jsonify({
        "sells":               result,
        "total_realized_base": round(total_realized_base, 2),
        "base_currency":       BASE_CURRENCY,
        "currency_symbol":     CURRENCY_SYMBOL,
    })


@app.route("/api/sell", methods=["POST"])
def api_add_sell():
    """POST — record a sale. Validates that units sold don't exceed remaining holdings.
    Body: {ticker, date, units, price_sold, market}.
    """
    data = request.get_json(force=True) or {}
    for field in ("ticker", "date", "units", "price_sold", "market"):
        if field not in data:
            return jsonify({"error": f"Missing field: {field}"}), 400
    if data["market"] not in VALID_MARKETS:
        return jsonify({"error": f'market must be one of: {", ".join(VALID_MARKETS)}'}), 400

    ticker        = str(data["ticker"]).upper().strip()
    units_to_sell = float(data["units"])

    # Validate: can't sell more than remaining units
    purchases   = load_portfolio()
    sells_curr  = load_sells()
    bought      = sum(float(p["units"]) for p in purchases if p["ticker"].upper() == ticker)
    sold_so_far = sum(float(s["units"]) for s in sells_curr if s["ticker"].upper() == ticker)
    remaining   = bought - sold_so_far

    if units_to_sell > remaining + 1e-9:
        return jsonify({"error": f"Only {remaining:.4f} units of {ticker} remaining"}), 400

    sell = {
        "id":         str(uuid.uuid4()),
        "ticker":     ticker,
        "date":       str(data["date"]),
        "units":      units_to_sell,
        "price_sold": float(data["price_sold"]),
        "market":     data["market"],
    }
    with _write_lock:
        sells_curr.append(sell)
        save_sells(sells_curr)
    return jsonify({"success": True, "sell": sell}), 201


@app.route("/api/sell/<string:record_id>", methods=["PUT"])
def api_update_sell(record_id):
    """PUT — overwrite the sell record with the given id. Body: same fields as POST."""
    data = request.get_json(force=True) or {}
    for field in ("ticker", "date", "units", "price_sold", "market"):
        if field not in data:
            return jsonify({"error": f"Missing field: {field}"}), 400
    if data["market"] not in VALID_MARKETS:
        return jsonify({"error": f'market must be one of: {", ".join(VALID_MARKETS)}'}), 400
    with _write_lock:
        sells = load_sells()
        idx = next((i for i, s in enumerate(sells) if s.get("id") == record_id), None)
        if idx is None:
            return jsonify({"error": "Record not found"}), 404
        sells[idx] = {
            "id":         record_id,
            "ticker":     str(data["ticker"]).upper().strip(),
            "date":       str(data["date"]),
            "units":      float(data["units"]),
            "price_sold": float(data["price_sold"]),
            "market":     data["market"],
        }
        save_sells(sells)
    return jsonify({"success": True, "sell": sells[idx]})


@app.route("/api/sell/<string:record_id>", methods=["DELETE"])
def api_delete_sell(record_id):
    """DELETE — remove the sell record with the given id."""
    with _write_lock:
        sells = load_sells()
        idx = next((i for i, s in enumerate(sells) if s.get("id") == record_id), None)
        if idx is None:
            return jsonify({"error": "Record not found"}), 404
        sells.pop(idx)
        save_sells(sells)
    return jsonify({"success": True})


@app.route("/api/snapshots")
def api_get_snapshots():
    """GET — return all monthly portfolio snapshots [{date, value, invested}]."""
    return jsonify(load_snapshots())


@app.route("/api/prices")
def api_refresh_prices():
    """GET — clear the in-memory price cache and return a freshly computed portfolio response."""
    _price_cache.clear()
    return jsonify(_portfolio_response())


def _get_hist_price(series: "pd.Series | None", target: _date) -> float | None:
    """Return the last closing price on or before target from a yfinance Close series."""
    if series is None or series.empty:
        return None
    try:
        ts = pd.Timestamp(target)
        if series.index.tz is not None:
            ts = ts.tz_localize("UTC")
        subset = series[series.index <= ts + pd.Timedelta(hours=23)]
        return float(subset.iloc[-1]) if not subset.empty else None
    except Exception:
        return None


@app.route("/api/snapshots/reset", methods=["POST"])
def api_snapshots_reset():
    """POST — clear all snapshots and rebuild from scratch via backfill.
    Returns same response as /api/backfill.
    """
    with _write_lock:
        save_snapshots([])
    # Delegate to backfill which does the full rebuild
    return api_backfill()


@app.route("/api/backfill", methods=["POST"])
def api_backfill():
    """POST — generate missing monthly snapshots by replaying historical prices.

    For each calendar month since the first purchase that doesn't already have a
    snapshot, fetches historical close prices from yfinance and computes the
    portfolio value at month-end using the average-cost method.
    Only adds months where all tickers have available price data.
    Returns {added, total}.
    """
    purchases = load_portfolio()
    sells     = load_sells()

    if not purchases:
        return jsonify({"added": 0, "total": 0, "message": "No purchases to backfill from"})

    purchases_sorted = sorted(purchases, key=lambda p: p["date"])
    start_str  = purchases_sorted[0]["date"]
    start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
    today      = datetime.now().date()
    fetch_end  = (today + timedelta(days=1)).strftime("%Y-%m-%d")

    # Fetch historical close prices for every ticker
    unique_tickers: dict[str, str] = {}
    for p in purchases:
        unique_tickers.setdefault(p["ticker"].upper(), p["market"])

    hist_prices: dict[str, pd.Series] = {}
    for ticker, market in unique_tickers.items():
        symbol = resolve_yf_symbol(ticker, market)
        try:
            df = yf.Ticker(symbol).history(start=start_str, end=fetch_end)
            if not df.empty:
                hist_prices[ticker] = df["Close"]
        except Exception:
            pass

    # Fetch historical FX rates for every non-base currency used in the portfolio
    unique_ccys = {MARKET_CURRENCY.get(p["market"], "USD") for p in purchases}
    hist_fx: dict[str, "pd.Series | None"] = {}
    for _ccy in unique_ccys:
        if _ccy == BASE_CURRENCY:
            continue
        try:
            df = yf.Ticker(f"{_ccy}{BASE_CURRENCY}=X").history(start=start_str, end=fetch_end)
            hist_fx[_ccy] = df["Close"] if not df.empty else None
        except Exception:
            hist_fx[_ccy] = None

    existing       = load_snapshots()
    existing_months = {s["date"][:7] for s in existing}

    new_snapshots: list[dict] = []
    cur_year, cur_month = start_date.year, start_date.month

    while (cur_year, cur_month) <= (today.year, today.month):
        month_key = f"{cur_year:04d}-{cur_month:02d}"

        if month_key not in existing_months:
            # Use last calendar day of the month (or today for the current month)
            if cur_year == today.year and cur_month == today.month:
                snap_date = today
            else:
                snap_date = _date(cur_year, cur_month,
                                  calendar.monthrange(cur_year, cur_month)[1])
            snap_str = snap_date.strftime("%Y-%m-%d")

            # Build portfolio state at snap_date using average-cost method
            held: dict[str, dict] = {}
            for p in purchases_sorted:
                if p["date"] > snap_str:
                    break
                key = p["ticker"].upper()
                if key not in held:
                    held[key] = {"units_bought": 0.0, "cost": 0.0, "market": p["market"]}
                held[key]["units_bought"] += float(p["units"])
                held[key]["cost"]         += float(p["units"]) * float(p["price_paid"])

            # Subtract sells
            for s in sells:
                if s["date"] <= snap_str:
                    key = s["ticker"].upper()
                    if key in held and held[key]["units_bought"] > 0:
                        avg  = held[key]["cost"] / held[key]["units_bought"]
                        sold = float(s["units"])
                        held[key]["units_bought"] -= sold
                        held[key]["cost"]         -= avg * sold

            total_value    = 0.0
            total_invested = 0.0
            skip           = False

            for ticker, h in held.items():
                if h["units_bought"] <= 1e-9:
                    continue
                price = _get_hist_price(hist_prices.get(ticker), snap_date)
                if price is None:
                    skip = True
                    break

                ccy = MARKET_CURRENCY.get(h["market"], "USD")
                if ccy == BASE_CURRENCY:
                    fx = 1.0
                else:
                    fx = _get_hist_price(hist_fx.get(ccy), snap_date) \
                         or get_fx_rate(ccy, BASE_CURRENCY) or 1.0

                total_value    += price * h["units_bought"] * fx
                total_invested += h["cost"] * fx

            if not skip and total_value > 0:
                new_snapshots.append({
                    "date":     snap_str,
                    "value":    round(total_value, 2),
                    "invested": round(total_invested, 2),
                })

        # Advance to next month
        cur_month += 1
        if cur_month > 12:
            cur_month = 1
            cur_year += 1

    if new_snapshots:
        all_snaps = sorted(existing + new_snapshots, key=lambda s: s["date"])
        save_snapshots(all_snaps)

    return jsonify({"added": len(new_snapshots), "total": len(existing) + len(new_snapshots)})


def _build_export_buf() -> tuple:
    """Build the export workbook and return (BytesIO, filename)."""
    purchases = load_portfolio()
    sells     = load_sells()
    dividends = load_dividends()
    snapshots = load_snapshots()

    # Pre-compute avg costs for sells sheet
    buy_agg: dict = {}
    for p in purchases:
        key = p["ticker"].upper()
        if key not in buy_agg:
            buy_agg[key] = {"units": 0.0, "cost": 0.0}
        buy_agg[key]["units"] += float(p["units"])
        buy_agg[key]["cost"]  += float(p["units"]) * float(p["price_paid"])
    avg_costs = {k: v["cost"] / v["units"] if v["units"] else 0.0 for k, v in buy_agg.items()}

    # Holdings via full portfolio calculation (includes live prices + FX)
    portfolio_data = build_response(purchases, sells)

    wb = openpyxl.Workbook()

    HDR_FONT = Font(bold=True, color="FFFFFF")
    HDR_FILL = PatternFill("solid", fgColor="1E3A5F")
    HDR_ALIGN = Alignment(horizontal="center")

    def write_headers(ws, headers):
        ws.append(headers)
        for cell in ws[1]:
            cell.font  = HDR_FONT
            cell.fill  = HDR_FILL
            cell.alignment = HDR_ALIGN

    def auto_width(ws):
        for col in ws.columns:
            max_len = max(
                (len(str(cell.value)) if cell.value is not None else 0)
                for cell in col
            )
            ws.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 10), 35)

    # ── Sheet 1: Purchases ────────────────────────────────────────────────────
    ws1 = wb.active
    ws1.title = "Purchases"
    write_headers(ws1, ["Date", "Ticker", "Market", "Units", "Price Paid/Unit", "Total Cost", "Currency"])
    for p in sorted(purchases, key=lambda x: x["date"]):
        ccy = MARKET_CURRENCY.get(p["market"], "USD")
        ws1.append([
            p["date"], p["ticker"], p["market"],
            float(p["units"]), float(p["price_paid"]),
            round(float(p["units"]) * float(p["price_paid"]), 2),
            ccy,
        ])
    auto_width(ws1)

    # ── Sheet 2: Sells ────────────────────────────────────────────────────────
    ws2 = wb.create_sheet("Sells")
    write_headers(ws2, ["Date", "Ticker", "Market", "Units Sold", "Price Sold",
                         "Avg Cost", "Realized Gain", "Gain %", "Currency"])
    for s in sorted(sells, key=lambda x: x["date"]):
        key      = s["ticker"].upper()
        ccy      = MARKET_CURRENCY.get(s["market"], "USD")
        avg_cost = avg_costs.get(key, 0.0)
        units    = float(s["units"])
        sell_px  = float(s["price_sold"])
        realized = (sell_px - avg_cost) * units
        gain_pct = ((sell_px - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0.0
        ws2.append([
            s["date"], s["ticker"], s["market"],
            units, sell_px, round(avg_cost, 4),
            round(realized, 2), round(gain_pct, 2), ccy,
        ])
    auto_width(ws2)

    # ── Sheet 3: Dividends ────────────────────────────────────────────────────
    ws3 = wb.create_sheet("Dividends")
    write_headers(ws3, ["Date", "Ticker", "Market", "Amount", "Currency",
                         f"Amount ({BASE_CURRENCY})"])
    for d in sorted(dividends, key=lambda x: x["date"]):
        ccy         = MARKET_CURRENCY.get(d["market"], "USD")
        rate        = get_fx_rate(ccy, BASE_CURRENCY)
        amount_base = round(float(d["amount"]) * rate, 2) if rate is not None else None
        ws3.append([d["date"], d["ticker"], d["market"], float(d["amount"]), ccy, amount_base])
    auto_width(ws3)

    # ── Sheet 4: Holdings (current) ───────────────────────────────────────────
    ws4 = wb.create_sheet("Holdings")
    write_headers(ws4, ["Ticker", "Market", "Sector", "Units", "Avg Cost",
                         "Current Price", "Invested (local)", "Current Value (local)",
                         "Gain/Loss (local)", "Gain/Loss %", "Currency",
                         f"Current Value ({BASE_CURRENCY})",
                         "Yield on Cost %", "Current Yield %"])
    for h in portfolio_data["holdings"]:
        ws4.append([
            h["ticker"], h["market"], h.get("sector", ""),
            round(h["total_units"], 4), round(h["avg_price"], 4),
            round(h["current_price"], 4) if h["current_price"] is not None else None,
            round(h["total_invested"], 2),
            round(h["current_value"], 2)      if h["current_value"]      is not None else None,
            round(h["gain_loss_amount"], 2)   if h["gain_loss_amount"]   is not None else None,
            round(h["gain_loss_pct"], 2)      if h["gain_loss_pct"]      is not None else None,
            h["currency"],
            round(h["current_value_base"], 2) if h["current_value_base"] is not None else None,
            h.get("yield_on_cost"),
            h.get("current_yield"),
        ])
    auto_width(ws4)

    # ── Sheet 5: Performance ──────────────────────────────────────────────────
    ws5 = wb.create_sheet("Performance")
    write_headers(ws5, ["Date", f"Portfolio Value ({BASE_CURRENCY})",
                         f"Cost Basis ({BASE_CURRENCY})"])
    for s in snapshots:
        ws5.append([s["date"], s["value"], s["invested"]])
    auto_width(ws5)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"investment_tracker_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return buf, filename


@app.route("/api/export")
def api_export_all():
    """GET — stream the export workbook to the browser."""
    buf, filename = _build_export_buf()
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/export/save")
def api_export_save():
    """GET — save the export XLSX to the app data folder and open it (packaged app).
    Returns {saved_to, filename} on success or {error} on failure.
    """
    try:
        buf, filename = _build_export_buf()
        save_path = os.path.join(DATA_DIR, filename)
        with open(save_path, "wb") as f:
            f.write(buf.read())
        # Open the file directly in Excel (or default .xlsx handler)
        os.startfile(save_path)
        return jsonify({"saved_to": save_path, "filename": filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/validate-ticker")
def api_validate_ticker():
    """GET — check whether a ticker/market combo resolves to a real security."""
    ticker = request.args.get("ticker", "").strip().upper()
    market = request.args.get("market", "")
    if not ticker or market not in VALID_MARKETS:
        return jsonify({"valid": False})
    symbol = resolve_yf_symbol(ticker, market)
    # Instant response if already cached
    if symbol in _sector_cache:
        return jsonify({"valid": True, "name": _sector_cache[symbol].get("name", "")})
    if symbol in _price_cache and _price_cache[symbol]["price"] is not None:
        return jsonify({"valid": True, "name": ""})
    # Live check via fast_info (lightweight, ~0.5 s)
    price = _fetch_yf(symbol)
    if price is None:
        return jsonify({"valid": False})
    name = _sector_cache.get(symbol, {}).get("name", "")
    return jsonify({"valid": True, "name": name})


@app.route("/api/benchmark")
def api_benchmark():
    """GET ?from=YYYY-MM-DD&ticker=SPY — return daily closes for the benchmark ticker."""
    from_date = request.args.get("from", "")
    ticker    = request.args.get("ticker", "SPY").strip().upper() or "SPY"
    if not from_date:
        return jsonify([])
    try:
        hist = yf.Ticker(ticker).history(start=from_date)
        result = [
            {"date": d.strftime("%Y-%m-%d"), "close": round(float(row["Close"]), 4)}
            for d, row in hist.iterrows()
        ]
        return jsonify(result)
    except Exception:
        return jsonify([])


@app.route("/api/search-ticker")
def api_search_ticker():
    """GET ?q=<query> — return up to 8 ticker suggestions from Yahoo Finance Search."""
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    try:
        results = yf.Search(q, max_results=8, news_count=0)
        suggestions = []
        for r in results.quotes:
            symbol   = r.get("symbol", "")
            name     = r.get("shortname") or r.get("longname", "")
            exchange = r.get("exchange", "")
            qtype    = r.get("quoteType", "")
            if symbol and name and qtype in ("EQUITY", "ETF"):
                suggestions.append({"symbol": symbol, "name": name, "exchange": exchange})
        return jsonify(suggestions)
    except Exception:
        return jsonify([])


# ---------------------------------------------------------------------------
# Auto-update check
# ---------------------------------------------------------------------------
_update_cache: dict = {
    "checked": False, "available": False,
    "version": "", "download_url": "", "release_notes": ""
}


def _version_newer(remote: str, local: str) -> bool:
    """Return True if remote semver string is strictly newer than local."""
    try:
        r = tuple(int(x) for x in remote.strip().split("."))
        l = tuple(int(x) for x in local.strip().split("."))
        return r > l
    except ValueError:
        return False


def _do_update_check() -> None:
    global _update_cache
    if not UPDATE_MANIFEST_URL:
        _update_cache = {"checked": True, "available": False, "version": APP_VERSION,
                         "download_url": "", "release_notes": ""}
        return
    import urllib.request as _req
    try:
        with _req.urlopen(UPDATE_MANIFEST_URL, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        remote = data.get("version", "")
        if remote and _version_newer(remote, APP_VERSION):
            _update_cache = {
                "checked":       True,
                "available":     True,
                "version":       remote,
                "download_url":  data.get("download_url", ""),
                "release_notes": data.get("release_notes", ""),
            }
            return
    except Exception:
        pass
    _update_cache = {"checked": True, "available": False, "version": APP_VERSION,
                     "download_url": "", "release_notes": ""}


@app.route("/api/check-update")
def api_check_update():
    """GET — return update info if a newer version is available on GitHub."""
    if not _update_cache["checked"]:
        threading.Thread(target=_do_update_check, daemon=True).start()
        # Return "not yet checked" immediately; client should retry after a short delay
        return jsonify({"checked": False, "available": False})
    return jsonify(_update_cache)


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    """GET — return current settings plus supported-currency metadata."""
    settings = load_settings()
    return jsonify({
        **settings,
        "supported_currencies": SUPPORTED_CURRENCIES,
        "currency_symbols":     ALL_CURRENCY_SYMBOLS,
        "is_frozen":            IS_FROZEN,
    })


@app.route("/api/settings", methods=["PUT"])
def api_save_settings():
    """PUT — update base currency and market definitions."""
    data = request.get_json(force=True) or {}
    base_currency = data.get("base_currency", "SGD")
    if base_currency not in SUPPORTED_CURRENCIES:
        return jsonify({"error": f"Unsupported currency: {base_currency}"}), 400
    markets = data.get("markets", [])
    if not markets:
        return jsonify({"error": "At least one market is required."}), 400
    for m in markets:
        if not m.get("name") or not m.get("currency"):
            return jsonify({"error": "Each market requires a name and currency."}), 400
        if m["currency"] not in SUPPORTED_CURRENCIES:
            return jsonify({"error": f"Unsupported currency for market {m['name']}: {m['currency']}"}), 400
    benchmark_ticker = data.get("benchmark_ticker", "SPY").strip().upper() or "SPY"
    new_settings = {
        "base_currency":    base_currency,
        "markets":          [{"name": m["name"], "currency": m["currency"], "suffix": m.get("suffix", "")} for m in markets],
        "benchmark_ticker": benchmark_ticker,
    }
    save_settings_file(new_settings)
    apply_settings(new_settings)
    _price_cache.clear()   # FX rates / price values may change with a new base currency
    _sector_cache.clear()
    return jsonify({"success": True})


if __name__ == "__main__":
    if getattr(sys, 'frozen', False):
        # ── Packaged as .exe — open a PyWebView app window ──────────────────
        import webview

        def run_flask():
            app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)

        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()

        # Poll until Flask responds (up to 10 s) instead of sleeping a fixed delay.
        import urllib.request as _urllib_req
        _deadline = time.time() + 10
        while time.time() < _deadline:
            try:
                _urllib_req.urlopen(f'http://127.0.0.1:{PORT}/', timeout=1)
                break
            except Exception:
                time.sleep(0.1)

        window = webview.create_window(
            title     = 'Investment Tracker',
            url       = f'http://127.0.0.1:{PORT}',
            width     = 1280,
            height    = 820,
            resizable = True,
            min_size  = (900, 600),
        )
        webview.start()
    else:
        # ── Development mode — use Flask dev server in the browser ───────────
        app.run(debug=True, host='127.0.0.1', port=PORT)
