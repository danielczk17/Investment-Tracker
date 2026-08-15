# Investment Tracker

A personal desktop app for tracking your stock portfolio across multiple markets. Records purchases, sales, and dividends; fetches live prices via Yahoo Finance; and charts your portfolio's performance over time.

Built with Python + Flask, packaged as a standalone Windows `.exe` — no installation, no account required.

---

## Features

**Dashboard**
- Summary strip — total invested, current value, total gain/loss, realized gains + dividends (all-time and YTD)
- Performance chart with 7D / 1M / 3M / 6M / 1Y / ALL filters and an optional benchmark overlay (SPY, QQQ, VTI, IWDA.L, or any Yahoo Finance ticker)
- Market allocation and industry breakdown (donut charts)
- Winners / losers breakdown
- Holdings table with live prices, gain/loss per position, and sector labels

**Transactions**
- Purchase log — record buys with date, units, price, and brokerage fees
- Realized gains — log sales and see profit/loss against your average cost basis
- Dividend income — track dividend payments with a monthly bar chart

**Data management**
- Export everything to Excel (holdings, purchases, sales, dividends, performance history)
- Import purchases, sales, and dividends in bulk from the provided Excel template
- Sector overrides — manually assign a category label to any ticker

**Settings**
- Dark mode
- Configurable base currency (SGD default; 14 currencies supported)
- Custom markets — define any exchange with its Yahoo Finance suffix and local currency
- Performance benchmark ticker

**App**
- Live prices and FX rates fetched from Yahoo Finance (5-minute cache)
- Automatic update notifications when a new version is published on GitHub
- All data stored locally — nothing leaves your machine

---

## Download

Go to [Releases](https://github.com/danielczk17/Investment-Tracker/releases/latest) and download `InvestmentTracker-vX.X.X-windows.zip`.

Extract the zip and run `InvestmentTracker.exe`. No installer needed.

**System requirements:** Windows 10 or later (64-bit). An internet connection is required to fetch live prices.

---

## Data storage

When running as the `.exe`, all data is stored in:

```
%APPDATA%\InvestmentTracker\
  portfolio.json        — purchase records
  sells.json            — sale records
  dividends.json        — dividend records
  snapshots.json        — daily portfolio value history
  sector_overrides.json — custom sector labels
  settings.json         — app settings
```

Backing up this folder is all you need to preserve your data. You can also use **Data → Export to Excel** for a portable backup.

---

## Running from source

**Prerequisites:** Python 3.10+

```bash
git clone https://github.com/danielczk17/Investment-Tracker.git
cd Investment-Tracker
pip install -r requirements.txt
python app.py
```

Then open `http://127.0.0.1:5000` in your browser.

---

## Building a release

Requires [GitHub CLI](https://cli.github.com) (`gh auth login`) and PyInstaller.

```bash
python build_release.py --version 1.2.0 --notes "Bug fixes and dark mode"
```

This will:
1. Bump `APP_VERSION` in `app.py` and update `version.json`
2. Build the PyInstaller bundle
3. Zip the output into `InvestmentTracker-v1.2.0-windows.zip`
4. Commit the version bump and push to `main`
5. Create a GitHub Release and upload the zip

Existing users will see an in-app update notification automatically once the release is published.

---

## Supported currencies

SGD, AUD, CAD, CHF, CNY, EUR, GBP, HKD, IDR, JPY, MYR, NZD, THB, USD, VND

---

## Default markets

| Market | Currency | Yahoo Finance suffix |
|--------|----------|----------------------|
| SGX    | SGD      | `.SI`                |
| US     | USD      | *(none)*             |
| World  | USD      | `.L`                 |

You can add, rename, or remove markets in **Settings → Markets**.

---

## License

MIT
