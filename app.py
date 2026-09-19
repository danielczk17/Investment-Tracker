"""
Investment Tracker — Flask backend.

Data is stored in JSON files in the per-user app data folder (packaged) —
%APPDATA%\\InvestmentTracker\\ on Windows, ~/Library/Application Support/InvestmentTracker
on macOS — or alongside this script (dev mode):
  portfolio.json        — purchase records
  sells.json            — sell records
  dividends.json        — dividend records
  snapshots.json        — monthly portfolio value history
  sector_overrides.json — manually assigned sector/category labels

All monetary totals are expressed in BASE_CURRENCY (SGD by default).
Live prices and FX rates are fetched via yfinance and cached in memory.
"""

# Standard library
import calendar         # used by backfill to calculate month-end dates
import io               # BytesIO for in-memory Excel buffers
import json             # reading/writing JSON data files
import os               # file path operations and environment variables
import sys              # detecting frozen (PyInstaller) vs dev mode
import time             # cache timestamp comparisons
import threading        # background threads and the write-lock
import uuid             # generating unique IDs for every record
from datetime import datetime, date as _date, timedelta

# Flask — HTTP server and response helpers
from flask import Flask, jsonify, request, render_template, send_file

# openpyxl — builds and reads Excel workbooks for export/import
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

# pandas — used only in the backfill route to slice historical price series
import pandas as pd

# yfinance — fetches live/historical prices, FX rates, and company metadata
import yfinance as yf

# ---------------------------------------------------------------------------
# CONFIG — edit these values to match your setup
# ---------------------------------------------------------------------------
# How long live prices and FX rates are served from memory before the next
# yfinance call. 5 minutes balances freshness with API rate-limit headroom.
PRICE_CACHE_TTL = 300

# TCP port Flask listens on. The PyInstaller launcher can override this via
# the PORT environment variable to avoid conflicts with other local services.
PORT = int(os.environ.get("PORT", 5000))

# Semantic version shown in the UI and compared against the remote manifest
# to decide whether to show an update banner.
APP_VERSION = "1.0.0"

# Raw URL of the version.json file hosted on GitHub.
# When a new release is published, bump APP_VERSION here and push version.json
# to the repo so existing installs will notice the update automatically.
# After creating your GitHub repo, replace this with:
#   https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/version.json
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/danielczk17/Investment-Tracker/main/version.json"
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Currency and market definitions
# ---------------------------------------------------------------------------
# Display symbols for every currency the app supports as a base or local currency.
# Shown in the UI wherever amounts are formatted (e.g. "S$1,234.56").
ALL_CURRENCY_SYMBOLS: dict[str, str] = {
    "SGD": "S$",                                               # default — always first
    "AUD": "A$",  "CAD": "C$",  "CHF": "CHF", "CNY": "CN¥",
    "EUR": "€",   "GBP": "£",   "HKD": "HK$", "IDR": "Rp",
    "JPY": "¥",   "MYR": "RM",  "NZD": "NZ$", "THB": "฿",
    "USD": "US$", "VND": "₫",
}
SUPPORTED_CURRENCIES: list[str] = list(ALL_CURRENCY_SYMBOLS.keys())

# Default markets loaded when settings.json doesn't exist yet.
# Each market entry defines:
#   name     — the label shown in the UI and used as the market key throughout the app
#   currency — the local currency for that exchange (used for gain/loss display and FX conversion)
#   suffix   — appended to a ticker to form the yfinance symbol (e.g. "ES3" + ".SI" → "ES3.SI")
_DEFAULT_MARKETS: list[dict] = [
    {"name": "SGX",   "currency": "SGD", "suffix": ".SI"},
    {"name": "US",    "currency": "USD", "suffix": ""},
    {"name": "World", "currency": "USD", "suffix": ".L"},
]

# ---------------------------------------------------------------------------
# Runtime globals
# ---------------------------------------------------------------------------
# These are set at startup by apply_settings() and updated live whenever the
# user saves new settings, so all routes always see the current configuration.
BASE_CURRENCY:   str        = "SGD"     # all base-currency totals are in this currency
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
# True when running inside a PyWebView window (packaged, or `python app.py --window`)
WINDOW_MODE = IS_FROZEN or '--window' in sys.argv

if IS_FROZEN:
    BASE_DIR     = os.path.dirname(sys.executable)
    TEMPLATE_DIR = os.path.join(sys._MEIPASS, 'templates')
    STATIC_DIR   = os.path.join(sys._MEIPASS, 'static')
    # Store user data in the standard per-user app data location for each OS
    if sys.platform == 'win32':
        _data_root = os.environ.get('APPDATA', os.path.expanduser('~'))
    elif sys.platform == 'darwin':
        _data_root = os.path.expanduser('~/Library/Application Support')
    else:
        _data_root = os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share'))
    DATA_DIR     = os.path.join(_data_root, 'InvestmentTracker')
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

# ---------------------------------------------------------------------------
# In-memory caches
# ---------------------------------------------------------------------------
# Prices are cached per yfinance symbol for PRICE_CACHE_TTL seconds.
# Each entry: { 'price': float|None, 'ts': float (epoch) }
_price_cache: dict = {}

# Sector, dividend rate, and company name are fetched together from yf.Ticker().info
# and cached for SECTOR_CACHE_TTL (24 h) because they change infrequently.
# Each entry: { 'sector': str, 'div_rate': float|None, 'name': str, 'ts': float }
_sector_cache: dict = {}

# User-defined overrides for the sector label (e.g. ETF → "Global Equity").
# Loaded from sector_overrides.json at startup; written back on every PUT /api/sector-override.
_sector_overrides: dict = {}

# Per-symbol locks prevent concurrent threads from hammering yfinance with duplicate
# requests for the same ticker (which triggers rate-limiting and empty responses).
_sector_symbol_locks: dict = {}
_sector_symbol_locks_mu = threading.Lock()

def _sector_lock_for(symbol: str) -> threading.Lock:
    with _sector_symbol_locks_mu:
        if symbol not in _sector_symbol_locks:
            _sector_symbol_locks[symbol] = threading.Lock()
        return _sector_symbol_locks[symbol]

SECTOR_CACHE_TTL = 86400  # 24 hours — sector/company info rarely changes

# All file writes go through this lock so that rapid back-to-back API calls
# (e.g. double-clicking Add) cannot interleave a load and a save.
_write_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
# All application data lives in JSON files in DATA_DIR.
# Each file holds a flat list of record dicts.  Every record carries a uuid4 id
# field so the frontend can reference individual items for edit/delete without
# relying on list position.
#
# The load_* / save_* pairs are deliberately thin: no ORM, no schema validation —
# just read the whole file in, mutate the list in Python, write the whole file back.
# The _write_lock ensures these sequences are atomic under concurrent requests.

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
    """Upsert today's portfolio snapshot into the daily history.

    If an entry already exists for today it is replaced with the latest values,
    so each calendar day keeps only one (the most recent) entry.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    snapshots = load_snapshots()
    entry = {"date": today, "value": round(value, 2), "invested": round(invested, 2)}
    if snapshots and snapshots[-1]["date"] == today:
        snapshots[-1] = entry   # update today's entry with latest prices
    else:
        snapshots.append(entry)
    save_snapshots(snapshots)


# ---------------------------------------------------------------------------
# Price & FX fetching
# ---------------------------------------------------------------------------
# All live data comes from yfinance.  Prices use a two-step strategy:
#   1. fast_info.last_price — lightweight, usually < 0.5 s, no info dict overhead.
#   2. history(period="5d") — fallback for tickers where fast_info is unavailable.
# FX rates are fetched as ordinary yfinance tickers using the standard "SGDUSD=X" convention.
#
# Both price and sector data are held in module-level dicts (_price_cache, _sector_cache)
# so every request within the TTL window is served from memory with no network call.
# Caches are cleared on settings save (new BASE_CURRENCY changes all FX calculations)
# and on the manual "Refresh Prices" action.

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
    symbol   = resolve_yf_symbol(ticker, market)
    override = _sector_overrides.get(symbol)
    now      = time.time()
    cached   = _sector_cache.get(symbol)

    # Serve from cache when the name is already populated (fresh or override present).
    # An override only controls the returned label — we still want the name fetched.
    if cached and now - cached["ts"] < SECTOR_CACHE_TTL:
        return override if override else cached["sector"]

    with _sector_lock_for(symbol):
        # Re-check after acquiring the lock — another thread may have just fetched.
        cached = _sector_cache.get(symbol)
        if cached and now - cached["ts"] < SECTOR_CACHE_TTL:
            return override if override else cached["sector"]

        sector    = "Unknown"
        div_rate  = None
        name      = ""
        fetch_ok  = False
        for attempt in range(3):
            try:
                info = yf.Ticker(symbol).info
                if info.get("quoteType") == "ETF":
                    sector = "ETF"
                else:
                    sector = info.get("sector") or "Unknown"
                div_rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate") or None
                if div_rate is not None:
                    div_rate = float(div_rate) if float(div_rate) > 0 else None
                name     = info.get("longName") or info.get("shortName") or ""
                fetch_ok = True
                break
            except Exception:
                if attempt < 2:
                    time.sleep(0.5)

        # If the fetch failed, cache briefly (60 s) so the next request retries.
        ts = now if fetch_ok else now - (SECTOR_CACHE_TTL - 60)
        _sector_cache[symbol] = {"sector": sector, "div_rate": div_rate, "name": name, "ts": ts}

    return override if override else sector


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

    # Average-cost method: for each ticker, accumulate total units bought and
    # total cost (units × price + fees across all purchases).  avg_price is
    # derived later as total_cost / total_units, so fees are automatically
    # folded into the cost basis and gain/loss figures.
    agg: dict = {}
    for p in purchases:
        key = p["ticker"].upper()
        if key not in agg:
            agg[key] = {"ticker": key, "market": p["market"], "total_units": 0.0, "total_cost": 0.0, "total_fees": 0.0}
        agg[key]["total_units"] += float(p["units"])
        agg[key]["total_cost"] += float(p["units"]) * float(p["price_paid"]) + float(p.get("fees", 0))
        agg[key]["total_fees"] += float(p.get("fees", 0))

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
            "total_fees":         round(h.get("total_fees", 0.0), 2),
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

    # YTD and 1-year return using Time-Weighted Return (TWR / chain-linking)
    def _twr(from_date: str) -> float | None:
        """
        Compute TWR from from_date to today using daily snapshots.
        Each sub-period return is neutralised for cash flows so new deposits
        don't inflate the figure:  r_i = value[i] / (value[i-1] + CF[i]) - 1
        where CF[i] = invested[i] - invested[i-1].
        Appends a synthetic 'today' sub-period using the live current value.
        Returns None if fewer than 2 data points exist.
        """
        all_snaps = load_snapshots()
        if not all_snaps or total_current_value_base == 0:
            return None

        from_dt = datetime.strptime(from_date, "%Y-%m-%d")
        today_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        # Keep only snapshots on or after from_date, sorted ascending
        window = sorted(
            [s for s in all_snaps
             if datetime.strptime(s["date"], "%Y-%m-%d") >= from_dt],
            key=lambda s: s["date"]
        )

        if not window:
            # Portfolio didn't exist yet at from_date — no data to chain
            return None

        # Prepend the anchor: the snapshot immediately before the window (or first in window)
        # so we have a clean starting value even if from_date falls between snapshots.
        before = [s for s in all_snaps if datetime.strptime(s["date"], "%Y-%m-%d") < from_dt]
        if before:
            anchor = max(before, key=lambda s: s["date"])
            chain = [anchor] + window
        else:
            chain = window  # portfolio started after from_date; use first available

        # Append today's live value as the final point
        last_invested = chain[-1].get("invested", chain[-1].get("value", 0))
        chain.append({"date": today_dt.strftime("%Y-%m-%d"),
                      "value": total_current_value_base,
                      "invested": last_invested})

        # Chain-link sub-period returns, skipping any where start value is 0
        cumulative = 1.0
        for i in range(1, len(chain)):
            v_start = chain[i - 1].get("value", 0)
            v_end   = chain[i].get("value", 0)
            cf      = chain[i].get("invested", 0) - chain[i - 1].get("invested", 0)
            denominator = v_start + max(cf, 0)   # only positive inflows adjust denominator
            if denominator <= 0:
                continue
            cumulative *= v_end / denominator

        return (cumulative - 1) * 100

    today         = datetime.now()
    ytd_target    = f"{today.year}-01-01"
    oneyear_target = (today.replace(year=today.year - 1)).strftime("%Y-%m-%d")
    ytd_return_pct      = _twr(ytd_target)
    one_year_return_pct = _twr(oneyear_target)

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
            "total_invested":       total_invested_base,
            "total_current_value":  total_current_value_base,
            "gain_loss_amount":     overall_gl,
            "gain_loss_pct":        overall_gl_pct,
            "ytd_return_pct":       ytd_return_pct,
            "one_year_return_pct":  one_year_return_pct,
        },
        "base_currency":   BASE_CURRENCY,
        "currency_symbol": CURRENCY_SYMBOL,
        "fx_rates":        fx_rates_out,
        "last_updated":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
# All JSON routes follow a simple REST convention:
#   GET  /api/<resource>            — list or retrieve
#   POST /api/<resource>            — create a new record (returns 201)
#   PUT  /api/<resource>/<id>       — overwrite a specific record by uuid
#   DELETE /api/<resource>/<id>     — remove a specific record by uuid
#
# Every mutating route wraps its load→modify→save sequence in _write_lock
# to prevent race conditions from concurrent requests.

# ── UI shell ─────────────────────────────────────────────────────────────────
# The single HTML page.  The frontend is a vanilla-JS SPA that calls the
# JSON API routes below for all data.

@app.route("/")
def index():
    return render_template("index.html")


# ── Portfolio ─────────────────────────────────────────────────────────────────
# /api/portfolio is the main data feed for the dashboard.  It recalculates
# everything from the raw JSON files on every call so the UI always shows
# current prices.  A snapshot of today's value is written to snapshots.json
# as a side-effect, keeping the performance chart up to date automatically.

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


# ── Purchase CRUD ─────────────────────────────────────────────────────────────
# Purchases are the source of truth for cost basis.  Every buy record stores
# units, price paid, and fees so the average-cost calculation in build_response()
# can reproduce the correct cost basis at any point in time.

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
        "fees":       float(data.get("fees") or 0),
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
            "fees":       float(data.get("fees") or 0),
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


# ── Sector override ───────────────────────────────────────────────────────────
# Allows the user to assign a custom category label to a holding (e.g. rename
# "Technology" to "Tech ETF").  Overrides are stored in sector_overrides.json
# and checked first in get_sector(), so yfinance is never consulted for them.

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


# ── Dividend CRUD ─────────────────────────────────────────────────────────────
# Dividends are recorded manually by the user (yfinance does not provide
# personalised payout history).  The GET route converts each local-currency
# amount to BASE_CURRENCY so the frontend can show a running total.

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
                        "company_name": get_company_name(d["ticker"], d["market"]),
                        "sector":       get_sector(d["ticker"], d["market"])})
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


# ── Sell CRUD ─────────────────────────────────────────────────────────────────
# Sell records reduce the remaining unit count shown in the holdings table.
# Realized gain is calculated at read time using the average cost of all buys
# for that ticker up to the sell date, so editing historical purchases
# automatically recalculates realized gains without reprocessing.

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
        buy_agg[key]["cost"]  += float(p["units"]) * float(p["price_paid"]) + float(p.get("fees", 0))
    avg_costs = {k: v["cost"] / v["units"] if v["units"] else 0.0 for k, v in buy_agg.items()}

    result: list = []
    total_realized_base = 0.0

    for s in sells:
        key      = s["ticker"].upper()
        currency = MARKET_CURRENCY.get(s["market"], "USD")
        avg_cost = avg_costs.get(key, 0.0)
        units    = float(s["units"])
        sell_px  = float(s["price_sold"])

        sell_fees         = float(s.get("fees", 0))
        realized_gain     = (sell_px - avg_cost) * units - sell_fees
        cost_basis        = avg_cost * units
        realized_gain_pct = (realized_gain / cost_basis * 100) if cost_basis > 0 else 0.0

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

    purchases = load_portfolio()
    bought    = sum(float(p["units"]) for p in purchases if p["ticker"].upper() == ticker)

    sell = {
        "id":         str(uuid.uuid4()),
        "ticker":     ticker,
        "date":       str(data["date"]),
        "units":      units_to_sell,
        "price_sold": float(data["price_sold"]),
        "fees":       float(data.get("fees") or 0),
        "market":     data["market"],
    }
    with _write_lock:
        sells_curr  = load_sells()
        sold_so_far = sum(float(s["units"]) for s in sells_curr if s["ticker"].upper() == ticker)
        remaining   = bought - sold_so_far
        if units_to_sell > remaining + 1e-9:
            return jsonify({"error": f"Only {remaining:.4f} units of {ticker} remaining"}), 400
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
            "fees":       float(data.get("fees") or 0),
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


# ── Snapshots and performance history ────────────────────────────────────────
# snapshots.json stores one entry per calendar month: the portfolio's total
# value and cost basis on (roughly) the last day of that month.  The chart on
# the dashboard plots this series to show performance over time.
#
# record_snapshot() upserts today's value on every /api/portfolio call so the
# current month is always current without a separate trigger.
#
# /api/backfill fills in any missing months by replaying historical close
# prices from yfinance — useful when first setting up the app or after adding
# old purchases.

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

    existing        = load_snapshots()
    existing_dates  = {s["date"] for s in existing}
    existing_months = {s["date"][:7] for s in existing}

    # Last 30 days: daily granularity.  Older history: month-end only.
    # Each day is classified independently so months that span the boundary
    # (e.g. July when cutoff falls on Jul 17) get daily coverage for the
    # recent portion and a single month-end snapshot for the older portion.
    DAILY_WINDOW = timedelta(days=30)
    daily_cutoff = today - DAILY_WINDOW

    target_dates: list[_date] = []
    d = start_date
    while d <= today:
        snap_str      = d.strftime("%Y-%m-%d")
        month_key     = f"{d.year:04d}-{d.month:02d}"
        last_of_month = _date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])

        if d > daily_cutoff:
            # Daily zone — add every missing day
            if snap_str not in existing_dates:
                target_dates.append(d)
        elif d == last_of_month:
            # Monthly zone — add month-end only if this month has no snapshot yet
            if month_key not in existing_months:
                target_dates.append(d)

        d += timedelta(days=1)

    new_snapshots: list[dict] = []
    for snap_date in target_dates:
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

    if new_snapshots:
        all_snaps = sorted(existing + new_snapshots, key=lambda s: s["date"])
        save_snapshots(all_snaps)

    return jsonify({"added": len(new_snapshots), "total": len(existing) + len(new_snapshots)})


# ── Export ────────────────────────────────────────────────────────────────────
# Produces a single .xlsx workbook with five sheets:
#   Purchases, Sells, Dividends, Holdings (live snapshot), Performance history.
#
# In dev mode the file is streamed directly to the browser via send_file().
# In packaged (frozen) mode PyWebView blocks the a.download click event, so
# /api/export/save writes the file to DATA_DIR and opens it with _open_path()
# instead (the user's default .xlsx handler, typically Excel).

def _open_path(path: str) -> None:
    """Open a file with the OS default handler (Windows, macOS or Linux)."""
    if sys.platform == 'win32':
        _open_path(path)
    else:
        import subprocess
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', path])


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
        buy_agg[key]["cost"]  += float(p["units"]) * float(p["price_paid"]) + float(p.get("fees", 0))
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
    write_headers(ws1, ["Date", "Ticker", "Market", "Units", "Price Paid/Unit", "Fees", "Total Cost", "Currency"])
    for p in sorted(purchases, key=lambda x: x["date"]):
        ccy  = MARKET_CURRENCY.get(p["market"], "USD")
        fees = float(p.get("fees") or 0)
        ws1.append([
            p["date"], p["ticker"], p["market"],
            float(p["units"]), float(p["price_paid"]),
            fees,
            round(float(p["units"]) * float(p["price_paid"]) + fees, 2),
            ccy,
        ])
    auto_width(ws1)

    # ── Sheet 2: Sells ────────────────────────────────────────────────────────
    ws2 = wb.create_sheet("Sells")
    write_headers(ws2, ["Date", "Ticker", "Market", "Units Sold", "Price Sold", "Fees",
                         "Avg Cost", "Realized Gain", "Gain %", "Currency"])
    for s in sorted(sells, key=lambda x: x["date"]):
        key        = s["ticker"].upper()
        ccy        = MARKET_CURRENCY.get(s["market"], "USD")
        avg_cost   = avg_costs.get(key, 0.0)
        units      = float(s["units"])
        sell_px    = float(s["price_sold"])
        sell_fees  = float(s.get("fees") or 0)
        realized   = (sell_px - avg_cost) * units - sell_fees
        cost_basis = avg_cost * units
        gain_pct   = (realized / cost_basis * 100) if cost_basis > 0 else 0.0
        ws2.append([
            s["date"], s["ticker"], s["market"],
            units, sell_px, sell_fees, round(avg_cost, 4),
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
        _open_path(save_path)
        return jsonify({"saved_to": save_path, "filename": filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Import ────────────────────────────────────────────────────────────────────
# Users can bulk-load historical data by filling in the import template and
# uploading it via POST /api/import.  The template has three sheets matching
# the app's data model (Purchases, Dividends, Sells).
#
# Row 1 is the header, row 2 is a colour-coded example, row 3 is a note
# reminding the user to delete both rows before importing.  The parser
# skips any row that is entirely blank, so partial sheets are fine.
#
# Validation errors (bad date format, unknown market, zero units) are collected
# per row and returned in the JSON response alongside the count of rows added,
# so the user can see exactly which rows were rejected.

def _build_import_template_buf() -> io.BytesIO:
    """Build a blank import template workbook and return it as a BytesIO buffer."""
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    hdr_font    = Font(bold=True, color="FFFFFF")
    hdr_fill    = PatternFill("solid", fgColor="2563EB")
    ex_font     = Font(italic=True, color="94A3B8")
    note_fill   = PatternFill("solid", fgColor="FEF9C3")
    note_font   = Font(italic=True, color="92400E")
    center      = Alignment(horizontal="center")

    valid = ", ".join(VALID_MARKETS)

    def _make_sheet(ws, headers, example, col_widths):
        # Header row
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=c, value=h)
            cell.font = hdr_font; cell.fill = hdr_fill; cell.alignment = center
        # Example row
        for c, v in enumerate(example, 1):
            cell = ws.cell(row=2, column=c, value=v)
            cell.font = ex_font
        # Note row spanning first column
        note = ws.cell(row=3, column=1, value=f"Valid markets: {valid}   |   Delete this row and the example row before importing.")
        note.font = note_font; note.fill = note_fill
        ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=len(headers))
        # Column widths
        for c, w in enumerate(col_widths, 1):
            ws.column_dimensions[get_column_letter(c)].width = w
        ws.row_dimensions[1].height = 18

    # Sheet 1 — Purchases
    ws1 = wb.active; ws1.title = "Purchases"
    _make_sheet(ws1,
        ["Date (YYYY-MM-DD)", "Ticker", "Market", "Units", "Price Per Unit", "Fees (optional)"],
        ["2024-01-15", "AAPL", "US", 10, 185.50, 1.99],
        [20, 12, 10, 10, 16, 16])

    # Sheet 2 — Dividends
    ws2 = wb.create_sheet("Dividends")
    _make_sheet(ws2,
        ["Date (YYYY-MM-DD)", "Ticker", "Market", "Amount"],
        ["2024-03-20", "AAPL", "US", 23.50],
        [20, 12, 10, 14])

    # Sheet 3 — Sells
    ws3 = wb.create_sheet("Sells")
    _make_sheet(ws3,
        ["Date (YYYY-MM-DD)", "Ticker", "Market", "Units Sold", "Price Per Unit", "Fees (optional)"],
        ["2024-06-10", "AAPL", "US", 5, 210.00, 1.99],
        [20, 12, 10, 12, 16, 16])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


@app.route("/api/import/template")
def api_import_template():
    """GET — stream the blank import template to the browser."""
    buf = _build_import_template_buf()
    return send_file(buf, download_name="investment_tracker_import_template.xlsx",
                     as_attachment=True,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/api/import/template/save")
def api_import_template_save():
    """GET — save the import template to DATA_DIR and open it (packaged app)."""
    try:
        buf      = _build_import_template_buf()
        filename = "investment_tracker_import_template.xlsx"
        path     = os.path.join(DATA_DIR, filename)
        with open(path, "wb") as f:
            f.write(buf.read())
        _open_path(path)
        return jsonify({"saved_to": path, "filename": filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/import", methods=["POST"])
def api_import_data():
    """POST multipart/form-data with field 'file' (.xlsx) — import purchases, dividends, sells."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    if not f.filename.lower().endswith(".xlsx"):
        return jsonify({"error": "File must be .xlsx format"}), 400

    try:
        wb = openpyxl.load_workbook(f, data_only=True)
    except Exception:
        return jsonify({"error": "Could not read Excel file — make sure it is a valid .xlsx"}), 400

    def _parse_date(val):
        if val is None:
            raise ValueError("Date is required")
        if hasattr(val, "strftime"):          # Excel datetime object
            return val.strftime("%Y-%m-%d")
        s = str(val).strip()
        datetime.strptime(s, "%Y-%m-%d")     # raises ValueError if invalid
        return s

    added  = {"purchases": 0, "dividends": 0, "sells": 0}
    errors = []

    with _write_lock:
        purchases = load_portfolio()
        dividends = load_dividends()
        sells     = load_sells()

        # ── Purchases ──────────────────────────────────────────────────────
        if "Purchases" in wb.sheetnames:
            for row_idx, row in enumerate(wb["Purchases"].iter_rows(min_row=2, values_only=True), start=2):
                if not any(c for c in row if c not in (None, "")):
                    continue
                try:
                    date_val, ticker, market, units, price, fees_raw = (list(row) + [None]*6)[:6]
                    date_str = _parse_date(date_val)
                    ticker   = str(ticker).strip().upper()
                    market   = str(market).strip()
                    units_f  = float(units)
                    price_f  = float(price)
                    fees_f   = float(fees_raw) if fees_raw not in (None, "") else 0.0
                    if not ticker:   raise ValueError("Ticker is required")
                    if market not in VALID_MARKETS:
                        raise ValueError(f"Market '{market}' not recognised — valid: {', '.join(VALID_MARKETS)}")
                    if units_f <= 0: raise ValueError("Units must be greater than 0")
                    if price_f <= 0: raise ValueError("Price must be greater than 0")
                    purchases.append({"id": str(uuid.uuid4()), "ticker": ticker,
                                      "date": date_str, "units": units_f,
                                      "price_paid": price_f, "fees": fees_f, "market": market})
                    added["purchases"] += 1
                except Exception as e:
                    errors.append(f"Purchases row {row_idx}: {e}")

        # ── Dividends ──────────────────────────────────────────────────────
        if "Dividends" in wb.sheetnames:
            for row_idx, row in enumerate(wb["Dividends"].iter_rows(min_row=2, values_only=True), start=2):
                if not any(c for c in row if c not in (None, "")):
                    continue
                try:
                    date_val, ticker, market, amount = (list(row) + [None]*4)[:4]
                    date_str = _parse_date(date_val)
                    ticker   = str(ticker).strip().upper()
                    market   = str(market).strip()
                    amount_f = float(amount)
                    if not ticker:   raise ValueError("Ticker is required")
                    if market not in VALID_MARKETS:
                        raise ValueError(f"Market '{market}' not recognised — valid: {', '.join(VALID_MARKETS)}")
                    if amount_f <= 0: raise ValueError("Amount must be greater than 0")
                    dividends.append({"id": str(uuid.uuid4()), "ticker": ticker,
                                      "date": date_str, "amount": amount_f, "market": market})
                    added["dividends"] += 1
                except Exception as e:
                    errors.append(f"Dividends row {row_idx}: {e}")

        # ── Sells ──────────────────────────────────────────────────────────
        if "Sells" in wb.sheetnames:
            for row_idx, row in enumerate(wb["Sells"].iter_rows(min_row=2, values_only=True), start=2):
                if not any(c for c in row if c not in (None, "")):
                    continue
                try:
                    date_val, ticker, market, units, price, fees_raw = (list(row) + [None]*6)[:6]
                    date_str = _parse_date(date_val)
                    ticker   = str(ticker).strip().upper()
                    market   = str(market).strip()
                    units_f  = float(units)
                    price_f  = float(price)
                    fees_f   = float(fees_raw) if fees_raw not in (None, "") else 0.0
                    if not ticker:   raise ValueError("Ticker is required")
                    if market not in VALID_MARKETS:
                        raise ValueError(f"Market '{market}' not recognised — valid: {', '.join(VALID_MARKETS)}")
                    if units_f <= 0: raise ValueError("Units must be greater than 0")
                    if price_f <= 0: raise ValueError("Price must be greater than 0")
                    sells.append({"id": str(uuid.uuid4()), "ticker": ticker,
                                  "date": date_str, "units": units_f,
                                  "price_sold": price_f, "fees": fees_f, "market": market})
                    added["sells"] += 1
                except Exception as e:
                    errors.append(f"Sells row {row_idx}: {e}")

        if added["purchases"]: save_portfolio(purchases)
        if added["dividends"]: save_dividends(dividends)
        if added["sells"]:     save_sells(sells)

    return jsonify({**added, "errors": errors})


# ── Ticker utilities ──────────────────────────────────────────────────────────
# Helper endpoints called by the frontend while the user is filling in the
# purchase/sell form to give real-time feedback before the form is submitted.

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


# ── Benchmark ────────────────────────────────────────────────────────────────
# Returns daily close prices for a benchmark ticker (e.g. SPY) from the date
# of the user's first snapshot so the performance chart can overlay them.
# The benchmark ticker is configurable in Settings; defaults to SPY.

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
# ETF look-through holdings
# ---------------------------------------------------------------------------
_etf_holdings_cache: dict = {}
ETF_HOLDINGS_TTL = 86400   # 24 hours — ETF compositions change slowly


@app.route("/api/etf-holdings")
def api_etf_holdings():
    """GET ?symbol=SPYL.L — top holdings for an ETF; empty list for direct stocks."""
    symbol = request.args.get("symbol", "").strip().upper()
    if not symbol:
        return jsonify([])
    now = time.time()
    cached = _etf_holdings_cache.get(symbol)
    if cached and now - cached["ts"] < ETF_HOLDINGS_TTL:
        return jsonify(cached["data"])
    result = []
    try:
        top = yf.Ticker(symbol).funds_data.top_holdings
        for sym, row in top.iterrows():
            result.append({
                "symbol": str(sym),
                "name":   str(row.get("Name", sym)),
                "weight": float(row.get("Holding Percent", 0)),
            })
    except Exception:
        pass   # not an ETF or no data available → returns []
    _etf_holdings_cache[symbol] = {"ts": now, "data": result}
    return jsonify(result)


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


# ── Settings ─────────────────────────────────────────────────────────────────
# Settings control which currency all totals are expressed in, which markets
# (exchanges) are available when adding a trade, and which benchmark ticker
# appears on the performance chart.
#
# On save (PUT), apply_settings() updates the runtime globals immediately so
# subsequent /api/portfolio calls reflect the new base currency without a restart.
# Both caches are cleared because FX rates and price-to-base conversions are
# now invalid.

@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    """GET — return current settings plus supported-currency metadata."""
    settings = load_settings()
    return jsonify({
        **settings,
        "supported_currencies": SUPPORTED_CURRENCIES,
        "currency_symbols":     ALL_CURRENCY_SYMBOLS,
        "is_frozen":            WINDOW_MODE,
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


# ---------------------------------------------------------------------------
# Application launcher
# ---------------------------------------------------------------------------
# Two launch modes depending on how the script is executed:
#
#   Packaged (.exe):
#     Flask runs on a background thread; a PyWebView window is opened pointing
#     at localhost.  The app polls until Flask responds before creating the window
#     to avoid a blank-screen race condition.
#
#   Development (python app.py):
#     Flask runs in the foreground with debug=True so code changes hot-reload
#     and full tracebacks appear in the browser.

if __name__ == "__main__":
    # Packaged builds always open a native window; from source, pass --window
    # (e.g. `python app.py --window`, or ./run.sh) to get the same window
    # instead of the browser-based dev server.
    if WINDOW_MODE:
        # ── Native app window (packaged, or --window) — open a PyWebView window ──
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
