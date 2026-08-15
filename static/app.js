  // ── State ──────────────────────────────────────────────────────────────────
  // Tracks ticker validation per form prefix: null = unchecked, true = valid, false = invalid
  const _tickerValid = { f: null, s: null, d: null };

  let portfolioData     = null;
  let logOpen           = true;
  let appSettings       = null;   // loaded from /api/settings on boot
  let editIdx           = null;   // null = add mode, number = edit mode
  let sortCol           = 'current_value_base';
  let sortDir           = -1;     // -1 = descending, 1 = ascending
  let lastHoldings      = [];
  let selectedPurchases = new Set();
  let sectorColorMap    = {};   // sector label → hex color, shared across all charts
  let sellOpen          = true;
  let sellData          = null;
  let selectedSells     = new Set();
  let sellEditIdx       = null;   // original index of sell record being edited, or null
  let _realizedGainsBase = 0;
  let _realizedDivsBase  = 0;
  let realizedViewMode   = 'all';
  const PAGE_SIZE = 10;
  let logPage  = 1;
  let divPage  = 1;
  let sellPage = 1;
  let holdingsPage   = 1;
  let holdingsSearch = '';
  let logSortCol  = 'date'; let logSortDir  = -1;
  let divSortCol  = 'date'; let divSortDir  = -1;
  let sellSortCol = 'date'; let sellSortDir = -1;

  // Switches the All-time / YTD toggle and re-renders the Realized + Dividends tile.
  function setRealizedView(mode) {
    realizedViewMode = mode;
    document.getElementById('tog-all').className = 'tog-btn' + (mode === 'all' ? ' tog-active' : '');
    document.getElementById('tog-ytd').className = 'tog-btn' + (mode === 'ytd' ? ' tog-active' : '');
    refreshRealizedTile();
  }

  // Recomputes the Realized + Dividends summary tile (total, gains row, dividends row).
  // In YTD mode, filters sell/dividend records to the current calendar year only.
  function refreshRealizedTile() {
    const sym = symOf(BASE_CCY);
    let gains, divs;
    if (realizedViewMode === 'ytd') {
      const year = new Date().getFullYear().toString();
      gains = (sellData?.sells || [])
        .filter(s => (s.date || '').startsWith(year))
        .reduce((sum, s) => sum + (s.realized_gain_base || 0), 0);
      divs = (divData?.dividends || [])
        .filter(d => (d.date || '').startsWith(year))
        .reduce((sum, d) => sum + (d.amount_base || 0), 0);
    } else {
      gains = _realizedGainsBase;
      divs  = _realizedDivsBase;
    }
    const total   = gains + divs;
    const totalEl = document.getElementById('s-realized-total');
    if (totalEl) {
      const sign = total >= 0 ? '+' : '';
      totalEl.textContent = `${sign}${sym}${fmt(Math.abs(total))}`;
      totalEl.className   = 'val ' + colorCls(total);
    }
    const gainsEl = document.getElementById('s-realized');
    if (gainsEl) {
      gainsEl.textContent = gains !== 0 ? `${gains >= 0 ? '+' : ''}${sym}${fmt(Math.abs(gains))}` : '—';
      gainsEl.className   = 'breakdown-val ' + colorCls(gains);
    }
    const divEl = document.getElementById('s-realized-div');
    if (divEl) {
      divEl.textContent = divs > 0 ? `+${sym}${fmt(divs)}` : '—';
      divEl.className   = 'breakdown-val ' + colorCls(divs);
    }
  }

  // Legacy alias kept so existing callers don't need updating.
  function updateRealizedTotal() { refreshRealizedTile(); }

  // ── Helpers ───────────────────────────────────────────────────────────────

  // Escapes a string for safe insertion into HTML attribute values or text nodes.
  const esc = s => String(s).replace(/[&<>"']/g, c =>
    ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));

  // Builds an HTML pagination control; returns '' when only one page exists.
  function buildPagination(page, total, setFn) {
    const pages = Math.ceil(total / PAGE_SIZE);
    if (pages <= 1) return '';
    const lo = Math.max(1, Math.min(page - 2, pages - 4));
    const hi = Math.min(pages, lo + 4);
    let btns = '';
    for (let i = lo; i <= hi; i++)
      btns += `<button class="${i === page ? 'pg-active' : ''}" onclick="${setFn}(${i})">${i}</button>`;
    return `<div class="pagination">
      <button onclick="${setFn}(1)"${page <= 1 ? ' disabled' : ''}>«</button>
      <button onclick="${setFn}(${page - 1})"${page <= 1 ? ' disabled' : ''}>‹</button>
      ${btns}
      <button onclick="${setFn}(${page + 1})"${page >= pages ? ' disabled' : ''}>›</button>
      <button onclick="${setFn}(${pages})"${page >= pages ? ' disabled' : ''}>»</button>
    </div>`;
  }

  // Formats a number with fixed decimal places; returns 'N/A' for null/undefined.
  const fmt = (n, d = 2) =>
    n == null ? 'N/A' : Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });

  // Currency helpers — populated from /api/settings on boot.
  let CURR_SYM   = { USD: 'US$', SGD: 'S$' };
  let BASE_CCY   = 'SGD';
  let MARKET_CCY = { US: 'USD', SGX: 'SGD', World: 'USD' };  // updated by fetchSettings()

  const symOf    = ccy  => CURR_SYM[ccy]  || (ccy + ' ');       // 'USD' → 'US$'
  const ccyOf    = mkt  => MARKET_CCY[mkt] || 'USD';             // 'SGX' → 'SGD'
  const fmtCcy   = (n, ccy) => n == null ? 'N/A' : symOf(ccy) + fmt(n);
  const fmtBase  = n    => fmtCcy(n, BASE_CCY);                  // always formats in SGD

  // Formats a percentage with a leading + for positive values.
  const fmtPct = n => n == null ? 'N/A' : (n >= 0 ? '+' : '') + fmt(n) + '%';

  // Returns a CSS class name ('pos', 'neg', 'neutral') based on sign of n.
  const colorCls = n => n == null ? 'neutral' : n > 0 ? 'pos' : n < 0 ? 'neg' : 'neutral';

  // Shows a transient toast notification at the bottom-right of the screen.
  function showToast(msg, duration = 3000) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), duration);
  }

  // Shows or hides the header spinner to indicate a background fetch is in progress.
  function setLoading(on) {
    document.getElementById('spinner').classList.toggle('active', on);
  }

  // ── Fetch ─────────────────────────────────────────────────────────────────

  // Loads portfolio data from the API and triggers a full re-render.
  async function fetchPortfolio() {
    setLoading(true);
    try {
      const res = await fetch('/api/portfolio');
      if (!res.ok) throw new Error();
      render(await res.json());
      fetchSnapshots();
    } catch {
      showToast('Failed to load portfolio data.');
    } finally {
      setLoading(false);
    }
  }

  // Clears the server-side price cache and re-renders with fresh prices.
  async function refreshPrices() {
    setLoading(true);
    try {
      const res = await fetch('/api/prices');
      if (!res.ok) throw new Error();
      render(await res.json());
      showToast('Prices refreshed.');
    } catch {
      showToast('Failed to refresh prices.');
    } finally {
      setLoading(false);
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────

  // Main render entry point — updates global state then dispatches to sub-renderers.
  function render(data) {
    portfolioData = data;
    if (data.currency_symbol) CURR_SYM = data.currency_symbol;
    if (data.base_currency)   BASE_CCY = data.base_currency;
    lastHoldings = data.holdings;
    renderSummary(data);
    renderPieChart(data.holdings);
    renderHoldings(data.holdings);
    renderHeatmap(data.holdings);
    renderTopHoldings(data.holdings);
    renderLog(data.purchases);
    renderWinnersLosers();
    document.getElementById('last-updated').textContent =
      'Updated ' + data.last_updated;
  }

  // Populates the 5 summary tiles and the FX rate note beneath them.
  function renderSummary({ totals, holdings, purchases, fx_rates }) {
    document.getElementById('s-invested').textContent = fmtBase(totals.total_invested);
    document.getElementById('s-value').textContent    = fmtBase(totals.total_current_value);

    const ytdEl = document.getElementById('s-ytd');
    if (totals.ytd_return_pct == null) {
      ytdEl.textContent  = 'N/A';
      ytdEl.className    = 'val';
    } else {
      ytdEl.textContent = fmtPct(totals.ytd_return_pct);
      ytdEl.className   = 'val ' + colorCls(totals.ytd_return_pct);
    }

    const oneYEl = document.getElementById('s-1y');
    if (totals.one_year_return_pct == null) {
      oneYEl.textContent = 'N/A';
      oneYEl.className   = 'val';
    } else {
      oneYEl.textContent = fmtPct(totals.one_year_return_pct);
      oneYEl.className   = 'val ' + colorCls(totals.one_year_return_pct);
    }

    // FX rate note under summary strip
    const fxEl = document.getElementById('fx-note');
    const pairs = Object.entries(fx_rates || {});
    fxEl.textContent = pairs.length
      ? 'Totals in ' + symOf(BASE_CCY).trim() + ' · ' +
        pairs.map(([ccy, rate]) => symOf(ccy).trim() + '1 = ' + symOf(BASE_CCY).trim() + fmt(rate)).join(' · ')
      : '';
  }

  // Handles a column header click in the Holdings table — toggles direction or changes column.
  function setSort(col) {
    sortDir = (sortCol === col) ? -sortDir : -1;
    sortCol = col;
    holdingsPage = 1;
    renderHoldings(lastHoldings);
  }

  // Renders the Holdings table with current sort order and pagination.
  function renderHoldings(holdings) {
    const wrap = document.getElementById('holdings-wrap');
    if (!holdings.length) {
      wrap.innerHTML = '<div class="empty">No holdings yet — add a purchase above.</div>';
      return;
    }

    // Sort
    const sorted = [...holdings].sort((a, b) => {
      const av = a[sortCol], bv = b[sortCol];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === 'string') return sortDir * av.localeCompare(bv);
      return sortDir * (av - bv);
    });

    const th = (label, col) => {
      const active = sortCol === col;
      const arrow  = active ? (sortDir === 1 ? ' ▲' : ' ▼') : '';
      return `<th class="sortable${active ? ' sort-active' : ''}" onclick="setSort('${col}')">${label}${arrow}</th>`;
    };

    const q        = holdingsSearch.trim().toLowerCase();
    const filtered = q ? sorted.filter(h => {
      const ticker = h.ticker.toLowerCase();
      const words  = (h.company_name || '').toLowerCase().split(/\s+/);
      return ticker.startsWith(q) || words.some(w => w.startsWith(q));
    }) : sorted;

    if (!filtered.length) {
      wrap.innerHTML = '<div class="empty">No holdings match your search.</div>';
      return;
    }

    const start = (holdingsPage - 1) * PAGE_SIZE;
    const page  = filtered.slice(start, start + PAGE_SIZE);

    const rows = page.map(h => `
      <tr>
        <td>
          <strong>${esc(h.ticker)}</strong>
          ${h.company_name ? `<div style="font-size:11px;color:#94a3b8;margin-top:1px;line-height:1.3">${esc(h.company_name)}</div>` : ''}
        </td>
        <td><span class="badge badge-${h.market.toLowerCase()}">${esc(h.market)}</span></td>
        <td>${fmt(h.total_units, 2)}</td>
        <td>${fmtCcy(h.avg_price, h.currency)}</td>
        <td>${h.total_fees ? fmtCcy(h.total_fees, h.currency) : '<span class="neutral">—</span>'}</td>
        <td>${fmtCcy(h.total_invested, h.currency)}</td>
        <td>${h.current_price != null ? fmtCcy(h.current_price, h.currency) : '<span class="neutral">N/A</span> <span title="Price unavailable — Yahoo Finance may be rate-limited or this ticker may be delisted." style="color:#f59e0b;cursor:help;font-size:.85em">&#9888;</span>'}</td>
        <td>${h.current_value != null ? fmtCcy(h.current_value, h.currency) : '<span class="neutral">N/A</span>'}</td>
        <td class="${colorCls(h.gain_loss_amount)}">${h.gain_loss_amount != null ? fmtCcy(h.gain_loss_amount, h.currency) : '<span class="neutral">N/A</span>'}</td>
        <td class="${colorCls(h.gain_loss_pct)}">${fmtPct(h.gain_loss_pct)}</td>
        <td class="pos" style="font-size:.78rem">${h.current_yield != null ? fmt(h.current_yield) + '%' : '—'}</td>
      </tr>`).join('');

    wrap.innerHTML = `
      <table>
        <thead><tr>
          ${th('Ticker','ticker')}${th('Market','market')}${th('Units','total_units')}
          ${th('Avg Price','avg_price')}${th('Fees','total_fees')}
          ${th('Invested','total_invested')}${th('Current Price','current_price')}${th('Current Value','current_value_base')}
          ${th('Gain/Loss $','gain_loss_amount')}${th('Gain/Loss %','gain_loss_pct')}
          ${th('Div Yield','current_yield')}
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${buildPagination(holdingsPage, filtered.length, 'setHoldingsPage')}`;
  }

  // Pagination handler for the Holdings table.
  function setHoldingsPage(p) {
    holdingsPage = p;
    renderHoldings(lastHoldings);
  }

  // Returns the purchases array sorted by the current logSortCol/logSortDir.
  // Used by renderLog, toggleSelect, and toggleSelectAll to ensure a consistent
  // sort order when mapping display positions back to original array indices.
  function getLogSorted(purchases) {
    const priceMap = {};
    (portfolioData?.holdings || []).forEach(h => { priceMap[h.ticker] = h.current_price; });
    return [...purchases].sort((a, b) => {
      let va, vb;
      if      (logSortCol === 'ticker') { va = a.ticker.toLowerCase(); vb = b.ticker.toLowerCase(); }
      else if (logSortCol === 'market') { va = a.market.toLowerCase(); vb = b.market.toLowerCase(); }
      else if (logSortCol === 'units')  { va = a.units;  vb = b.units; }
      else if (logSortCol === 'price')  { va = a.price_paid;  vb = b.price_paid; }
      else if (logSortCol === 'total')  { va = a.units * a.price_paid;  vb = b.units * b.price_paid; }
      else if (logSortCol === 'gain')   {
        const cpa = priceMap[a.ticker.toUpperCase()]; const cpb = priceMap[b.ticker.toUpperCase()];
        va = cpa != null ? (cpa - a.price_paid) * a.units : -Infinity;
        vb = cpb != null ? (cpb - b.price_paid) * b.units : -Infinity;
      }
      else { va = a.date; vb = b.date; }
      return logSortDir * (va < vb ? -1 : va > vb ? 1 : 0);
    });
  }

  // Renders the Purchase Log table. Uses original array indices (not sorted position)
  // for row IDs and checkbox values so edit/delete work correctly across pages.
  function renderLog(purchases, resetPage = true) {
    if (resetPage) { selectedPurchases.clear(); logPage = 1; }
    updateLogToolbar();
    const wrap = document.getElementById('log-wrap');
    if (!purchases.length) {
      wrap.innerHTML = '<div class="empty">No purchases recorded.</div>';
      return;
    }

    const priceMap = {};
    (portfolioData?.holdings || []).forEach(h => { priceMap[h.ticker] = h.current_price; });

    const sorted     = getLogSorted(purchases);
    const origIdxOf  = sorted.map(p => p.id);
    const total = sorted.length;
    const start = (logPage - 1) * PAGE_SIZE;
    const page  = sorted.slice(start, start + PAGE_SIZE);

    const rows = page.map((p, j) => {
      const origIdx = origIdxOf[start + j];
      const cp  = priceMap[p.ticker.toUpperCase()];
      const ccy = ccyOf(p.market);
      let glCell;
      if (cp != null) {
        const glAmt = (cp - p.price_paid) * p.units;
        const glPct = (cp - p.price_paid) / p.price_paid * 100;
        const cls   = colorCls(glAmt);
        glCell = `<td class="${cls}">${fmtCcy(glAmt, ccy)}<br><small>${fmtPct(glPct)}</small></td>`;
      } else {
        glCell = `<td class="neutral" style="color:#94a3b8">—</td>`;
      }
      const sel = selectedPurchases.has(origIdx);
      return `
      <tr id="log-row-${origIdx}"${sel ? ' class="selected"' : ''}>
        <td class="chk"><input type="checkbox" id="chk-${origIdx}"${sel ? ' checked' : ''} onchange="toggleSelect('${origIdx}')"></td>
        <td style="text-align:left">${esc(p.date)}</td>
        <td style="text-align:left">
          <strong>${esc(p.ticker)}</strong>
          ${p.company_name ? `<div style="font-size:11px;color:#94a3b8;margin-top:1px;line-height:1.3">${esc(p.company_name)}</div>` : ''}
        </td>
        <td style="text-align:left"><span class="badge badge-${p.market.toLowerCase()}">${esc(p.market)}</span></td>
        <td>${fmt(p.units, 2)}</td>
        <td>${fmtCcy(p.price_paid, ccy)}</td>
        <td>${p.fees ? fmtCcy(p.fees, ccy) : '<span class="neutral">—</span>'}</td>
        <td>${fmtCcy(p.units * p.price_paid + (p.fees || 0), ccy)}</td>
        ${glCell}
      </tr>`;
    }).join('');

    const pageOrigIdxs  = page.map((_, j) => origIdxOf[start + j]);
    const pageSelCount  = pageOrigIdxs.filter(oi => selectedPurchases.has(oi)).length;
    const allChkd = page.length > 0 && pageSelCount === page.length;
    const indet   = pageSelCount > 0 && pageSelCount < page.length;

    wrap.innerHTML = `
      <table>
        <thead><tr>
          <th class="chk"><input type="checkbox" id="chk-all"${allChkd ? ' checked' : ''}
            onchange="toggleSelectAll(this.checked)" title="Select all"></th>
          ${['date','ticker','market','units','price','fees','total','gain'].map((c,i) => {
            const labels = ['Date','Ticker','Market','Units','Price/Unit','Fees','Total Cost','Gain / Loss'];
            const active = logSortCol === c;
            const arrow  = active ? (logSortDir === -1 ? ' ▼' : ' ▲') : '';
            const left   = i < 3 ? ' style="text-align:left"' : '';
            return `<th class="sortable${active?' sort-active':''}"${left} onclick="setLogSort('${c}')">${labels[i]}${arrow}</th>`;
          }).join('')}
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${buildPagination(logPage, total, 'setLogPage')}`;

    const chkAll = document.getElementById('chk-all');
    if (chkAll) chkAll.indeterminate = indet;
  }

  // Pagination handler for the Purchase Log table.
  function setLogPage(p) {
    logPage = p;
    renderLog(portfolioData?.purchases || [], false);
  }

  // Column sort handler for the Purchase Log table.
  function setLogSort(col) {
    if (logSortCol === col) logSortDir = -logSortDir;
    else { logSortCol = col; logSortDir = (col === 'ticker' || col === 'market') ? 1 : -1; }
    logPage = 1;
    renderLog(portfolioData?.purchases || [], false);
  }

  // Toggles a single purchase row's selection state and syncs the select-all checkbox.
  function toggleSelect(idx) {
    if (selectedPurchases.has(idx)) {
      selectedPurchases.delete(idx);
    } else {
      selectedPurchases.add(idx);
    }
    const row = document.getElementById(`log-row-${idx}`);
    if (row) row.classList.toggle('selected', selectedPurchases.has(idx));
    const purchases = portfolioData?.purchases || [];
    const sorted = getLogSorted(purchases);
    const start  = (logPage - 1) * PAGE_SIZE;
    const page   = sorted.slice(start, start + PAGE_SIZE);
    const pageSelCount = page.filter(p => selectedPurchases.has(p.id)).length;
    const chkAll = document.getElementById('chk-all');
    if (chkAll) {
      chkAll.checked       = page.length > 0 && pageSelCount === page.length;
      chkAll.indeterminate = pageSelCount > 0 && pageSelCount < page.length;
    }
    updateLogToolbar();
  }

  // Selects or deselects all rows on the current Purchase Log page.
  function toggleSelectAll(checked) {
    const purchases = portfolioData?.purchases || [];
    const sorted = getLogSorted(purchases);
    const start  = (logPage - 1) * PAGE_SIZE;
    const page   = sorted.slice(start, start + PAGE_SIZE);
    page.forEach(p => {
      const origIdx = p.id;
      checked ? selectedPurchases.add(origIdx) : selectedPurchases.delete(origIdx);
      const chk = document.getElementById(`chk-${origIdx}`);
      if (chk) chk.checked = checked;
      const row = document.getElementById(`log-row-${origIdx}`);
      if (row) row.classList.toggle('selected', checked);
    });
    updateLogToolbar();
  }

  // Syncs the Edit/Delete button states with the current selection count.
  // Edit is only enabled when exactly one row is selected.
  function updateLogToolbar() {
    const count     = selectedPurchases.size;
    const editBtn   = document.getElementById('log-edit-btn');
    const deleteBtn = document.getElementById('log-delete-btn');
    const countEl   = document.getElementById('log-sel-count');
    if (editBtn)   editBtn.disabled   = count !== 1;
    if (deleteBtn) deleteBtn.disabled = count === 0;
    if (countEl)   countEl.textContent = count > 0 ? `${count} selected` : '';
  }

  // ── Sector override modal ────────────────────────────────────────────────

  // Pending tickers for the currently open sector editor (one entry per modal row).
  let _pendingOverrides = [];

  // Opens the sector override modal, pre-filling each ticker row with the current sector label.
  function openSectorEditor(tickers, currentSector) {
    _pendingOverrides = tickers.map(t => ({ ...t }));
    document.getElementById('sector-modal-rows').innerHTML = tickers.map((t, i) => `
      <div class="modal-row">
        <strong>${esc(t.ticker)}</strong>
        <span class="badge badge-${t.market.toLowerCase()}">${esc(t.market)}</span>
        <input type="text" id="sector-input-${i}" value="${esc(currentSector)}"
          placeholder="e.g. ETF, Technology…">
      </div>`).join('');
    document.getElementById('sector-modal').style.display = 'flex';
  }

  // Closes the sector override modal and clears pending state.
  function closeSectorModal() {
    document.getElementById('sector-modal').style.display = 'none';
    _pendingOverrides = [];
  }

  // Reads input values from the modal, saves each override via the API,
  // then refreshes the portfolio so the new sector labels appear immediately.
  async function saveSectorOverrides() {
    if (!_pendingOverrides.length) { closeSectorModal(); return; }
    setLoading(true);
    try {
      for (let i = 0; i < _pendingOverrides.length; i++) {
        const { ticker, market } = _pendingOverrides[i];
        const sector = (document.getElementById(`sector-input-${i}`)?.value || '').trim();
        if (!sector) continue;
        const res = await fetch('/api/sector-override', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ticker, market, sector }),
        });
        if (!res.ok) { showToast('Failed to save category.'); return; }
      }
      closeSectorModal();
      await fetchPortfolio();
      showToast('Category updated.');
    } catch {
      showToast('Network error.');
    } finally {
      setLoading(false);
    }
  }

  // ── Charts ────────────────────────────────────────────────────────────────

  // Fixed colours for the three markets (blue = US, green = SGX, amber = World).
  const MARKET_COLORS  = { US: '#3b82f6', SGX: '#22c55e', World: '#f59e0b' };

  // Rotating colour palette for sector slices. Sectors are assigned in value-desc order
  // so the largest slice always gets the first (most distinct) colour.
  const SECTOR_PALETTE = [
    '#6366f1','#ef4444','#10b981','#f97316',
    '#06b6d4','#ec4899','#84cc16','#8b5cf6','#14b8a6','#eab308',
  ];
  const FALLBACK_COLOR = '#94a3b8';

  // Compacts a large number to K/M notation for axis labels (e.g. 38500 → '38.5K').
  function fmtK(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return fmt(n);
  }

  // Draws a donut chart on canvasEl and populates legendEl with colour-coded rows.
  // entries: [{label, value, color}], grand: sum of all values.
  // Gaps between slices are created by shrinking each sweep angle by GAP radians;
  // slices whose sweep would go negative are skipped but still advance the angle.
  function renderDonut(canvasEl, legendEl, centerEl, entries, grand, label1, label2) {
    const dpr   = window.devicePixelRatio || 1;
    const SIZE  = 170;
    const physW = Math.round(SIZE * dpr);
    const physH = Math.round(SIZE * dpr);
    canvasEl.width        = physW;
    canvasEl.height       = physH;
    canvasEl.style.width  = SIZE + 'px';
    canvasEl.style.height = SIZE + 'px';

    const ctx    = canvasEl.getContext('2d');
    ctx.scale(physW / SIZE, physH / SIZE);
    ctx.clearRect(0, 0, SIZE, SIZE);

    const cx = SIZE / 2, cy = SIZE / 2;
    const outerR = SIZE / 2 - 6;
    const innerR = outerR * 0.58;
    const GAP    = 0.02;

    let angle = -Math.PI / 2;
    for (const { color, value } of entries) {
      const rawSweep = (value / grand) * 2 * Math.PI;
      const sweep = rawSweep - GAP;
      if (sweep <= 0) { angle += rawSweep; continue; }
      ctx.beginPath();
      ctx.arc(cx, cy, outerR, angle + GAP / 2, angle + GAP / 2 + sweep);
      ctx.arc(cx, cy, innerR, angle + GAP / 2 + sweep, angle + GAP / 2, true);
      ctx.closePath();
      ctx.fillStyle = color;
      ctx.fill();
      angle += rawSweep;
    }

    // Center labels rendered in HTML for crisp text at any DPR
    if (centerEl) {
      centerEl.querySelector('.donut-val').textContent = label1;
      centerEl.querySelector('.donut-sub').textContent = label2;
    }

    legendEl.innerHTML = entries.map(e => `
      <div class="legend-row">
        <span class="legend-dot" style="background:${e.color}"></span>
        <span class="legend-name" title="${esc(e.label)}">${esc(e.label)}</span>
        <span class="legend-val">${fmtBase(e.value)}</span>
        <span class="legend-pct">${fmt(e.value / grand * 100)}%</span>
      </div>`).join('');
  }

  // Renders the By Market and By Industry donut charts.
  // Also rebuilds sectorColorMap so the dividend bar chart uses matching colours,
  // and injects edit buttons into the industry legend for sector overrides.
  function renderPieChart(holdings) {
    const card = document.getElementById('chart-card');
    if (!holdings.length) { card.style.display = 'none'; return; }
    card.style.display = '';

    const baseVal = h => h.current_value_base ?? h.current_value ?? h.total_invested;

    // ── By Market ──
    const mktMap = {};
    for (const h of holdings) mktMap[h.market] = (mktMap[h.market] || 0) + baseVal(h);
    const mktEntries = Object.entries(mktMap).map(([m, v]) => ({
      label: m, value: v, color: MARKET_COLORS[m] || FALLBACK_COLOR,
    }));
    const mktGrand = mktEntries.reduce((s, e) => s + e.value, 0);
    renderDonut(
      document.getElementById('pie-canvas'),
      document.getElementById('chart-legend'),
      document.getElementById('pie-center'),
      mktEntries, mktGrand,
      symOf(BASE_CCY).trim() + fmtK(mktGrand), 'by market'
    );

    // ── By Industry ──
    const indMap = {};
    const sectorTickersMap = {};  // sector → [{ticker, market}]
    for (const h of holdings) {
      const s = h.sector || 'Unknown';
      indMap[s] = (indMap[s] || 0) + baseVal(h);
      if (!sectorTickersMap[s]) sectorTickersMap[s] = [];
      sectorTickersMap[s].push({ ticker: h.ticker, market: h.market });
    }
    const indEntries = Object.entries(indMap)
      .sort((a, b) => b[1] - a[1])
      .map(([label, value], i) => ({ label, value, color: SECTOR_PALETTE[i % SECTOR_PALETTE.length] }));
    const indGrand = indEntries.reduce((s, e) => s + e.value, 0);
    // Publish canonical sector → color mapping; refresh dividend chart so colours stay in sync
    sectorColorMap = {};
    indEntries.forEach(e => { sectorColorMap[e.label] = e.color; });
    if (divData?.dividends?.length) requestAnimationFrame(() => renderDivChart(divData.dividends));
    renderDonut(
      document.getElementById('industry-canvas'),
      document.getElementById('industry-legend'),
      document.getElementById('industry-center'),
      indEntries, indGrand,
      symOf(BASE_CCY).trim() + fmtK(indGrand), 'by industry'
    );
    // Override legend to add per-sector edit buttons
    const indLegendEl = document.getElementById('industry-legend');
    indLegendEl.innerHTML = indEntries.map(e => {
      const tickers = sectorTickersMap[e.label] || [];
      const tickersJson = esc(JSON.stringify(tickers));
      const labelJson   = esc(JSON.stringify(e.label));
      return `<div class="legend-row">
        <span class="legend-dot" style="background:${e.color}"></span>
        <span class="legend-name" title="${esc(e.label)}">${esc(e.label)}</span>
        <span class="legend-val">${fmtBase(e.value)}</span>
        <span class="legend-pct">${fmt(e.value / indGrand * 100)}%</span>
        <button class="sector-edit-btn" title="Set category"
          onclick='openSectorEditor(JSON.parse(this.dataset.t), JSON.parse(this.dataset.l))'
          data-t="${tickersJson}" data-l="${labelJson}">✎</button>
      </div>`;
    }).join('');
  }

  // ── Actions ───────────────────────────────────────────────────────────────

  // Loads the selected purchase into the form and switches it to edit mode.
  function startEdit() {
    if (selectedPurchases.size !== 1) return;
    const id = [...selectedPurchases][0];
    const p = portfolioData.purchases.find(p => p.id === id);
    document.getElementById('f-ticker').value = p.ticker;
    document.getElementById('f-date').value   = p.date;
    document.getElementById('f-units').value  = p.units;
    document.getElementById('f-price').value  = p.price_paid;
    document.getElementById('f-fees').value   = p.fees || '';
    document.getElementById('f-market').value = p.market;
    updatePurchasePriceLabel();
    editIdx = id;
    _tickerValid['f'] = true;
    document.getElementById('submit-btn').textContent = 'Save Changes';
    document.getElementById('cancel-btn').style.display = '';
    document.getElementById('purchase-card').classList.add('editing');
    document.getElementById('purchase-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // Resets the purchase form back to add mode and clears the editing highlight.
  function cancelEdit() {
    editIdx = null;
    _tickerValid['f'] = null;
    document.getElementById('f-ticker').value = '';
    document.getElementById('f-units').value  = '';
    document.getElementById('f-price').value  = '';
    document.getElementById('f-fees').value   = '';
    document.getElementById('f-date').value   = new Date().toISOString().slice(0, 10);
    document.getElementById('submit-btn').textContent  = 'Add';
    document.getElementById('cancel-btn').style.display = 'none';
    document.getElementById('purchase-card').classList.remove('editing');
    clearTickerHint('f');
  }

  // Validates the purchase form and POSTs (add) or PUTs (edit) to the API.
  async function submitPurchase() {
    const ticker     = document.getElementById('f-ticker').value.trim().toUpperCase();
    const date       = document.getElementById('f-date').value;
    const units      = parseFloat(document.getElementById('f-units').value);
    const price_paid = parseFloat(document.getElementById('f-price').value);
    const market     = document.getElementById('f-market').value;

    if (!ticker)                        { showToast('Enter a ticker symbol.'); return; }
    if (_tickerValid['f'] === false)    { showToast(`"${ticker}" was not found on ${document.getElementById('f-market').value} — check the ticker and market.`); return; }
    if (_tickerValid['f'] === null)     { showToast('Ticker is still being validated — please wait a moment.'); return; }
    if (!date)                          { showToast('Pick a date.'); return; }
    if (!units      || units  <= 0)     { showToast('Units must be > 0.'); return; }
    if (!price_paid || price_paid <= 0) { showToast('Price must be > 0.'); return; }

    const isEdit = editIdx !== null;
    const url    = isEdit ? `/api/purchase/${editIdx}` : '/api/purchase';
    const method = isEdit ? 'PUT' : 'POST';

    setLoading(true);
    try {
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker, date, units, price_paid, fees: parseFloat(document.getElementById('f-fees').value) || 0, market }),
      });
      if (!res.ok) {
        const err = await res.json();
        showToast(err.error || 'Failed to save purchase.');
        return;
      }
      showToast(isEdit ? `Updated ${ticker}` : `Added ${fmt(units, 2)} × ${ticker}`);
      cancelEdit();
      await fetchPortfolio();
    } catch {
      showToast('Network error — purchase not saved.');
    } finally {
      setLoading(false);
    }
  }

  // Deletes all selected purchase rows. Iterates in descending index order so
  // earlier indices don't shift after each removal.
  async function deleteSelected() {
    const count = selectedPurchases.size;
    if (count === 0) return;
    if (!await showConfirm(`Delete ${count} purchase entr${count === 1 ? 'y' : 'ies'}?`, 'Delete')) return;
    const ids = [...selectedPurchases];
    setLoading(true);
    try {
      for (const id of ids) {
        const res = await fetch(`/api/purchase/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error();
      }
      showToast(`Deleted ${count} purchase entr${count === 1 ? 'y' : 'ies'}.`);
      selectedPurchases.clear();
      await fetchPortfolio();
    } catch {
      showToast('Failed to delete one or more purchases.');
    } finally {
      setLoading(false);
    }
  }

  // Collapses or expands the Purchase Log card body.
  function toggleLog() {
    logOpen = !logOpen;
    document.getElementById('log-body').classList.toggle('open', logOpen);
    document.getElementById('log-icon').innerHTML = logOpen ? '&#x25BC;' : '&#x25B6;';
    document.getElementById('log-lbl').textContent = logOpen ? 'Hide' : 'Show';
  }

  // ── Performance state ─────────────────────────────────────────────────────
  let perfPeriod   = 'ALL';
  let equityPeriod = 'ALL';
  let snapData     = [];
  let equityLayout = null;   // geometry/data shared between renderEquityChart and _equityHover

  // Filters a snapshots array to only those within the selected time window.
  function filterSnapshots(snaps, period) {
    if (period === 'ALL' || !snaps.length) return snaps;
    const days = { '7D': 7, '1M': 30, '3M': 90, '6M': 180, '1Y': 365 }[period] || 9999;
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - days);
    return snaps.filter(s => new Date(s.date) >= cutoff);
  }

  // Loads portfolio snapshots from the API and renders the equity curve.
  async function fetchSnapshots() {
    try {
      const res = await fetch('/api/snapshots');
      snapData  = await res.json();
      renderEquityChart(snapData);
    } catch { /* silently ignore */ }
  }

  // Draws the portfolio % return vs SPY benchmark chart.
  // Uses two canvas layers: a static base layer and a transparent overlay for the crosshair.
  function renderPerfChart(snaps) {
    const emptyEl   = document.getElementById('perf-empty');
    const chartArea = document.getElementById('perf-chart-area');
    const legendEl  = document.getElementById('perf-legend');
    const filtered  = filterSnapshots(snaps, perfPeriod);

    if (!filtered.length) {
      emptyEl.style.display   = '';
      chartArea.style.display = 'none';
      return;
    }
    emptyEl.style.display   = 'none';
    chartArea.style.display = '';

    const baseCanvas  = document.getElementById('perf-base');
    const crossCanvas = document.getElementById('perf-cross');
    const dpr = window.devicePixelRatio || 1;
    const W   = baseCanvas.parentElement.clientWidth || 700;
    const H   = 220;

    [baseCanvas, crossCanvas].forEach(c => {
      c.width        = W * dpr;
      c.height       = H * dpr;
      c.style.width  = W + 'px';
      c.style.height = H + 'px';
    });

    const ctx = baseCanvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);

    const PAD_L = 58, PAD_R = 20, PAD_T = 22, PAD_B = 34;
    const chartW = W - PAD_L - PAD_R;
    const chartH = H - PAD_T - PAD_B;
    const n      = filtered.length;

    // Portfolio % return = gain on cost basis at each point: (value - invested) / invested
    // This stays meaningful even when new money is added over time, and matches the summary tile.
    const portPcts = filtered.map(s => s.invested > 0 ? (s.value - s.invested) / s.invested * 100 : 0);

    // Align benchmark closes to each snapshot date (nearest trading day)
    function nearestBench(dateStr) {
      if (!benchData.length) return null;
      const t = new Date(dateStr + 'T00:00:00').getTime();
      let best = benchData[0], bestD = Infinity;
      for (const b of benchData) {
        const d = Math.abs(new Date(b.date + 'T00:00:00').getTime() - t);
        if (d < bestD) { bestD = d; best = b; }
      }
      return best;
    }
    const benchPeriod = filtered.map(s => nearestBench(s.date));
    const baseB       = benchPeriod[0] ? benchPeriod[0].close : null;
    const bPctVals    = benchPeriod.map(b => (b && baseB) ? (b.close - baseB) / baseB * 100 : null);
    const hasBench    = bPctVals.some(v => v !== null);

    // Y-axis range covering both series
    const allPcts = [...portPcts, ...bPctVals.filter(v => v !== null)];
    const rawMin  = Math.min(...allPcts);
    const rawMax  = Math.max(...allPcts);
    const pad     = Math.max((rawMax - rawMin) * 0.12, 1);
    const yMin    = rawMin - pad;
    const yMax    = rawMax + pad;
    const yRange  = yMax - yMin || 1;

    const toX = i => PAD_L + (n < 2 ? chartW / 2 : i / (n - 1) * chartW);
    const toY = v => PAD_T + (1 - (v - yMin) / yRange) * chartH;

    const vPts = portPcts.map((p, i) => ({ x: toX(i), y: toY(p) }));
    const bPts = bPctVals.map((p, i) => p !== null ? { x: toX(i), y: toY(p) } : null);

    perfLayout = { dpr, W, H, PAD_L, PAD_R, PAD_T, PAD_B, chartW, chartH, n,
                   yMin, yRange, filtered, vPts, bPts, portPcts, bPctVals };

    const FONT = '10px system-ui,-apple-system,sans-serif';

    // Y-axis gridlines + % labels
    for (let i = 0; i <= 4; i++) {
      const v = yMin + yRange * i / 4;
      const y = toY(v);
      ctx.strokeStyle = '#e2e8f0'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(PAD_L + chartW, y); ctx.stroke();
      const label = (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
      ctx.fillStyle = '#94a3b8'; ctx.font = FONT; ctx.textAlign = 'right';
      ctx.fillText(label, PAD_L - 5, y + 3.5);
    }

    // Zero reference line
    if (yMin < 0 && yMax > 0) {
      const y0 = toY(0);
      ctx.strokeStyle = '#cbd5e1'; ctx.lineWidth = 1; ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(PAD_L, y0); ctx.lineTo(PAD_L + chartW, y0); ctx.stroke();
      ctx.setLineDash([]);
    }

    // X-axis date labels
    const MNAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const step   = Math.max(1, Math.floor((n - 1) / 4));
    const shown  = new Set();
    for (let i = 0; i < n; i += step) shown.add(i);
    shown.add(n - 1);
    shown.forEach(i => {
      const d = new Date(filtered[i].date + 'T00:00:00');
      ctx.fillStyle = '#94a3b8'; ctx.font = '9px system-ui,sans-serif'; ctx.textAlign = 'center';
      ctx.fillText(MNAMES[d.getMonth()] + " '" + String(d.getFullYear()).slice(2), toX(i), H - PAD_B + 14);
    });

    // Baseline
    ctx.strokeStyle = '#e2e8f0'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(PAD_L, PAD_T + chartH); ctx.lineTo(PAD_L + chartW, PAD_T + chartH); ctx.stroke();

    // Diverging fill — green when portfolio beats benchmark (or 0), red when trailing
    const refPts = hasBench ? bPts : vPts.map((_, i) => ({ x: toX(i), y: toY(0) }));
    for (let i = 0; i < n - 1; i++) {
      const rA = refPts[i], rB = refPts[i + 1];
      if (!rA || !rB) continue;
      const x1 = vPts[i].x,   y1v = vPts[i].y,   y1r = rA.y;
      const x2 = vPts[i+1].x, y2v = vPts[i+1].y, y2r = rB.y;
      const ahead1 = y1v <= y1r, ahead2 = y2v <= y2r;

      const drawSeg = (ax, ayv, ayr, bx, byv, byr, ahead) => {
        ctx.beginPath();
        ctx.moveTo(ax, ayv); ctx.lineTo(bx, byv); ctx.lineTo(bx, byr); ctx.lineTo(ax, ayr);
        ctx.closePath();
        ctx.fillStyle = ahead ? 'rgba(10,163,12,0.11)' : 'rgba(208,59,59,0.11)';
        ctx.fill();
      };

      if (ahead1 === ahead2) {
        drawSeg(x1, y1v, y1r, x2, y2v, y2r, ahead1);
      } else {
        const dv = y2v - y1v, dr = y2r - y1r;
        const t  = (y1r - y1v) / (dv - dr);
        const ix = x1 + t * (x2 - x1), iy = y1v + t * dv;
        drawSeg(x1, y1v, y1r, ix, iy, iy, ahead1);
        drawSeg(ix, iy, iy, x2, y2v, y2r, ahead2);
      }
    }

    // SPY benchmark — dashed orange line
    if (hasBench) {
      ctx.strokeStyle = '#f97316'; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]);
      ctx.beginPath();
      let started = false;
      bPts.forEach(p => {
        if (!p) return;
        if (!started) { ctx.moveTo(p.x, p.y); started = true; }
        else ctx.lineTo(p.x, p.y);
      });
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Portfolio — solid blue line
    ctx.strokeStyle = '#2a78d6'; ctx.lineWidth = 2;
    ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    ctx.beginPath();
    vPts.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
    ctx.stroke();

    // End-dot
    const lp = vPts[n - 1];
    ctx.fillStyle = '#fff';
    ctx.beginPath(); ctx.arc(lp.x, lp.y, 5, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#2a78d6';
    ctx.beginPath(); ctx.arc(lp.x, lp.y, 4, 0, Math.PI * 2); ctx.fill();

    legendEl.innerHTML = `
      <span style="display:flex;align-items:center;gap:.35rem">
        <span style="display:inline-block;width:18px;height:2px;background:#2a78d6;border-radius:1px;flex-shrink:0"></span>
        Portfolio
      </span>
      ${hasBench ? `<span style="display:flex;align-items:center;gap:.35rem">
        <span style="display:inline-block;width:18px;height:0;border-top:2px dashed #f97316;flex-shrink:0"></span>
        ${(appSettings?.benchmark_ticker || 'SPY')} (benchmark)
      </span>` : ''}`;

    crossCanvas.onmousemove  = e => _perfHover(e);
    crossCanvas.onmouseleave = () => {
      document.getElementById('perf-cross').getContext('2d').clearRect(0, 0, W * dpr, H * dpr);
      document.getElementById('perf-tip').style.display = 'none';
    };
  }

  // Handles mouse movement over the performance chart overlay canvas.
  function _perfHover(e) {
    if (!perfLayout) return;
    const { dpr, W, H, PAD_L, PAD_T, PAD_B, chartW, chartH, n, filtered, vPts, bPts, portPcts, bPctVals } = perfLayout;
    const canvas = document.getElementById('perf-cross');
    const rect   = canvas.getBoundingClientRect();
    const mx     = e.clientX - rect.left;

    const frac = Math.max(0, Math.min(1, (mx - PAD_L) / chartW));
    const idx  = Math.min(n - 1, Math.max(0, Math.round(frac * (n - 1))));
    const snap = filtered[idx];
    const px   = vPts[idx].x;
    const pvy  = vPts[idx].y;

    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    ctx.strokeStyle = '#cbd5e1'; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(px, PAD_T); ctx.lineTo(px, PAD_T + chartH); ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = '#fff';
    ctx.beginPath(); ctx.arc(px, pvy, 5, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#2a78d6';
    ctx.beginPath(); ctx.arc(px, pvy, 4, 0, Math.PI * 2); ctx.fill();

    // Also draw dot on benchmark line if available
    const bp = bPts ? bPts[idx] : null;
    if (bp) {
      ctx.fillStyle = '#fff';
      ctx.beginPath(); ctx.arc(bp.x, bp.y, 4, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = '#f97316';
      ctx.beginPath(); ctx.arc(bp.x, bp.y, 3, 0, Math.PI * 2); ctx.fill();
    }

    const portPct  = portPcts[idx];
    const benchPct = bPctVals ? bPctVals[idx] : null;
    const portSign = portPct >= 0 ? '+' : '';
    const portCol  = portPct >= 0 ? '#22c55e' : '#ef4444';
    const gl       = snap.value - (snap.invested ?? snap.value);
    const glSign   = gl >= 0 ? '+' : '';
    const glCol    = gl >= 0 ? '#22c55e' : '#ef4444';

    let benchRow = '';
    if (benchPct !== null) {
      const bSign = benchPct >= 0 ? '+' : '';
      const diff  = portPct - benchPct;
      const dSign = diff >= 0 ? '+' : '';
      const dCol  = diff >= 0 ? '#22c55e' : '#ef4444';
      benchRow = `
        <div style="display:flex;justify-content:space-between;gap:.9rem">
          <span style="color:#f97316">SPY</span>
          <strong style="color:#f97316">${bSign}${fmt(benchPct, 2)}%</strong>
        </div>
        <div style="display:flex;justify-content:space-between;gap:.9rem;border-top:1px solid rgba(255,255,255,.1);margin-top:.25rem;padding-top:.25rem">
          <span style="color:#94a3b8">vs SPY</span>
          <strong style="color:${dCol}">${dSign}${fmt(diff, 2)}%</strong>
        </div>`;
    }

    const tip = document.getElementById('perf-tip');
    tip.innerHTML = `
      <div style="font-size:.7rem;color:#94a3b8;margin-bottom:.3rem;font-variant-numeric:tabular-nums">${esc(snap.date)}</div>
      <div style="display:flex;justify-content:space-between;gap:.9rem">
        <span style="color:#94a3b8">Portfolio</span>
        <strong style="color:${portCol}">${portSign}${fmt(portPct, 2)}%</strong>
      </div>
      <div style="display:flex;justify-content:space-between;gap:.9rem">
        <span style="color:#94a3b8">Value</span>
        <strong>${fmtBase(snap.value)}</strong>
      </div>
      <div style="display:flex;justify-content:space-between;gap:.9rem">
        <span style="color:#94a3b8">Gain/Loss</span>
        <strong style="color:${glCol}">${glSign}${fmtBase(gl)}</strong>
      </div>
      ${benchRow}`;

    tip.style.display = '';
    const tipW = 210;
    const left = (px + 12 + tipW > W - 20) ? px - tipW - 12 : px + 12;
    tip.style.left = left + 'px';
    tip.style.top  = (PAD_T + 4) + 'px';
  }

  // ── Equity Curve ─────────────────────────────────────────────────────────

  function setEquityPeriod(p) {
    equityPeriod = p;
    document.querySelectorAll('[id^="eq-"]').forEach(b =>
      b.classList.toggle('active', b.id === 'eq-' + p));
    renderEquityChart(snapData);
  }

  function renderEquityChart(snaps) {
    const card      = document.getElementById('equity-card');
    const emptyEl   = document.getElementById('equity-empty');
    const chartArea = document.getElementById('equity-chart-area');
    const legendEl  = document.getElementById('equity-legend');
    if (!card) return;

    const filtered = filterSnapshots(snaps, equityPeriod);
    if (filtered.length < 2) {
      card.style.display = 'none';
      return;
    }
    card.style.display = '';
    emptyEl.style.display   = 'none';
    chartArea.style.display = '';

    const baseCanvas  = document.getElementById('equity-base');
    const crossCanvas = document.getElementById('equity-cross');
    const dpr = window.devicePixelRatio || 1;
    const W   = baseCanvas.parentElement.clientWidth || 700;
    const H   = 210;

    [baseCanvas, crossCanvas].forEach(c => {
      c.width = W * dpr; c.height = H * dpr;
      c.style.width = W + 'px'; c.style.height = H + 'px';
    });

    const ctx = baseCanvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);

    const PAD_L = 68, PAD_R = 20, PAD_T = 18, PAD_B = 34;
    const chartW = W - PAD_L - PAD_R;
    const chartH = H - PAD_T - PAD_B;
    const n      = filtered.length;

    const values   = filtered.map(s => s.value);
    const invested = filtered.map(s => s.invested);
    const allVals  = [...values, ...invested].filter(v => v != null && isFinite(v));
    const rawMin   = Math.min(...allVals);
    const rawMax   = Math.max(...allVals);
    const pad      = Math.max((rawMax - rawMin) * 0.12, rawMax * 0.02, 1);
    const yMin     = Math.max(0, rawMin - pad);
    const yMax     = rawMax + pad;
    const yRange   = yMax - yMin || 1;

    const toX = i => PAD_L + (n < 2 ? chartW / 2 : i / (n - 1) * chartW);
    const toY = v => PAD_T + (1 - (v - yMin) / yRange) * chartH;

    const vPts = values.map((v, i)   => ({ x: toX(i), y: toY(v) }));
    const iPts = invested.map((v, i) => ({ x: toX(i), y: toY(v) }));

    equityLayout = { dpr, W, H, PAD_L, PAD_R, PAD_T, PAD_B, chartW, chartH, n,
                     yMin, yRange, filtered, vPts, iPts, values, invested };

    const isDark     = document.body.classList.contains('dark');
    const gridColor  = isDark ? 'rgba(255,255,255,0.07)' : 'rgba(0,0,0,0.06)';
    const labelColor = isDark ? '#64748b' : '#94a3b8';
    const FONT       = '10px system-ui,-apple-system,sans-serif';
    const sym        = symOf(BASE_CCY);

    // Y-axis gridlines + labels
    for (let i = 0; i <= 4; i++) {
      const v = yMin + yRange * i / 4;
      const y = toY(v);
      ctx.strokeStyle = gridColor; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(W - PAD_R, y); ctx.stroke();
      ctx.fillStyle = labelColor; ctx.font = FONT;
      ctx.textAlign = 'right';
      ctx.fillText(sym.trim() + fmtK(v), PAD_L - 6, y + 3.5);
    }

    // X-axis date labels
    ctx.fillStyle = labelColor; ctx.font = FONT; ctx.textAlign = 'center';
    const xCount = Math.min(n, 6);
    for (let i = 0; i < xCount; i++) {
      const idx = Math.round(i * (n - 1) / Math.max(xCount - 1, 1));
      const d   = new Date(filtered[idx].date + 'T00:00:00');
      ctx.fillText(d.toLocaleDateString('en-US', { month: 'short', year: '2-digit' }), toX(idx), H - PAD_B + 14);
    }

    const isGain   = values[n - 1] >= invested[n - 1];
    const lineClr  = isGain ? '#16a34a' : '#ef4444';

    // Invested dashed line
    ctx.save();
    ctx.strokeStyle = isDark ? '#475569' : '#cbd5e1';
    ctx.lineWidth   = 1.5;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    iPts.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
    ctx.stroke();
    ctx.restore();

    // Area fill under value line
    const grad = ctx.createLinearGradient(0, PAD_T, 0, H - PAD_B);
    grad.addColorStop(0, isGain ? 'rgba(22,163,74,0.18)' : 'rgba(239,68,68,0.18)');
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.beginPath();
    vPts.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
    ctx.lineTo(vPts[n - 1].x, H - PAD_B);
    ctx.lineTo(vPts[0].x,     H - PAD_B);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // Value line
    ctx.strokeStyle = lineClr; ctx.lineWidth = 2;
    ctx.lineJoin    = 'round';  ctx.setLineDash([]);
    ctx.beginPath();
    vPts.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
    ctx.stroke();

    // Legend
    const lastGain    = values[n - 1] - invested[n - 1];
    const lastGainPct = invested[n - 1] > 0 ? lastGain / invested[n - 1] * 100 : 0;
    const glSign      = lastGain >= 0 ? '+' : '';
    legendEl.innerHTML = `
      <span style="display:flex;align-items:center;gap:.3rem">
        <span style="width:14px;height:2px;background:${lineClr};display:inline-block;border-radius:2px"></span> Portfolio Value
      </span>
      <span style="display:flex;align-items:center;gap:.3rem">
        <span style="width:14px;height:0;border-top:2px dashed ${isDark?'#475569':'#cbd5e1'};display:inline-block"></span> Invested
      </span>
      <span style="margin-left:.25rem;color:${lineClr};font-weight:600">${glSign}${sym}${fmt(Math.abs(lastGain))} (${glSign}${fmt(lastGainPct)}%)</span>`;

    // Crosshair handlers
    crossCanvas.onmousemove  = e => _equityHover(e);
    crossCanvas.onmouseleave = () => {
      document.getElementById('equity-cross').getContext('2d').clearRect(0, 0, W * dpr, H * dpr);
      document.getElementById('equity-tip').style.display = 'none';
    };
  }

  function _equityHover(e) {
    if (!equityLayout) return;
    const { dpr, W, H, PAD_L, PAD_R, PAD_T, PAD_B, n, filtered, vPts, iPts, values, invested } = equityLayout;
    const canvas = document.getElementById('equity-cross');
    const rect   = canvas.getBoundingClientRect();
    const mx     = e.clientX - rect.left;
    const tip    = document.getElementById('equity-tip');

    if (mx < PAD_L - 10 || mx > W - PAD_R + 10) {
      canvas.getContext('2d').clearRect(0, 0, W * dpr, H * dpr);
      tip.style.display = 'none';
      return;
    }

    let nearest = 0, minDist = Infinity;
    vPts.forEach((p, i) => { const d = Math.abs(p.x - mx); if (d < minDist) { minDist = d; nearest = i; } });

    const ctx2 = canvas.getContext('2d');
    ctx2.clearRect(0, 0, W * dpr, H * dpr);
    ctx2.scale(dpr, dpr);

    const isDark  = document.body.classList.contains('dark');
    const isGain  = values[n - 1] >= invested[n - 1];
    const dotClr  = isGain ? '#16a34a' : '#ef4444';
    const p       = vPts[nearest];

    // Vertical crosshair
    ctx2.strokeStyle = isDark ? 'rgba(255,255,255,0.18)' : 'rgba(0,0,0,0.1)';
    ctx2.lineWidth = 1; ctx2.setLineDash([3, 3]);
    ctx2.beginPath(); ctx2.moveTo(p.x, PAD_T); ctx2.lineTo(p.x, H - PAD_B); ctx2.stroke();
    ctx2.setLineDash([]);

    // Dot on value line
    ctx2.fillStyle = dotClr;
    ctx2.beginPath(); ctx2.arc(p.x, p.y, 4, 0, Math.PI * 2); ctx2.fill();
    ctx2.strokeStyle = '#fff'; ctx2.lineWidth = 1.5; ctx2.stroke();

    // Tooltip
    const snap     = filtered[nearest];
    const sym      = symOf(BASE_CCY);
    const gain     = snap.value - snap.invested;
    const gainPct  = snap.invested > 0 ? gain / snap.invested * 100 : 0;
    const glSign   = gain >= 0 ? '+' : '';
    const glColor  = gain >= 0 ? '#4ade80' : '#f87171';
    tip.style.display = '';
    tip.innerHTML = `
      <div style="font-weight:600;margin-bottom:.3rem;color:#94a3b8">${snap.date}</div>
      <div style="display:flex;justify-content:space-between;gap:1.2rem"><span>Value</span><strong>${sym}${fmt(snap.value)}</strong></div>
      <div style="display:flex;justify-content:space-between;gap:1.2rem"><span style="color:#94a3b8">Invested</span><span>${sym}${fmt(snap.invested)}</span></div>
      <div style="display:flex;justify-content:space-between;gap:1.2rem"><span style="color:#94a3b8">Gain/Loss</span><span style="color:${glColor}">${glSign}${sym}${fmt(Math.abs(gain))} (${glSign}${fmt(gainPct)}%)</span></div>`;

    const tipW = tip.offsetWidth || 175;
    tip.style.left = (p.x + 12 + tipW > W - PAD_R ? p.x - tipW - 12 : p.x + 12) + 'px';
    tip.style.top  = Math.max(PAD_T, Math.min(p.y - 40, H - PAD_B - 80)) + 'px';
  }

  // ── Concentration heatmap (squarified treemap) ───────────────────────────

  let _heatTiles = [];   // [{x,y,w,h, ticker, name, weight, glPct}] for hit-testing

  // Worst aspect ratio across a candidate row of areas, given the row's short side
  function _heatWorstAspect(areas, side) {
    const s = areas.reduce((a, b) => a + b, 0);
    const mx = Math.max(...areas), mn = Math.min(...areas);
    return Math.max(side * side * mx / (s * s), s * s / (side * side * mn));
  }

  // Recursive squarify: mutates `out` with {x,y,w,h,...node} entries
  function _heatSquarify(nodes, x0, y0, x1, y1, out) {
    if (!nodes.length) return;
    if (nodes.length === 1) {
      out.push({ ...nodes[0], x: x0, y: y0, w: x1 - x0, h: y1 - y0 });
      return;
    }
    const W = x1 - x0, H = y1 - y0;
    const isWide  = W >= H;
    const side    = isWide ? H : W;

    let row = [], rowArea = 0, prevWorst = Infinity, cutIdx = 0;
    for (let i = 0; i < nodes.length; i++) {
      const candidate = [...row.map(n => n.area), nodes[i].area];
      const worst = _heatWorstAspect(candidate, side);
      if (row.length > 0 && worst > prevWorst) break;
      row.push(nodes[i]);
      rowArea += nodes[i].area;
      prevWorst = worst;
      cutIdx = i + 1;
    }

    const thickness = rowArea / side;
    let cursor = isWide ? y0 : x0;
    for (const node of row) {
      const len = node.area / thickness;
      if (isWide) {
        out.push({ ...node, x: x0, y: cursor, w: thickness, h: len });
        cursor += len;
      } else {
        out.push({ ...node, x: cursor, y: y0, w: len, h: thickness });
        cursor += len;
      }
    }

    const rest = nodes.slice(cutIdx);
    if (!rest.length) return;
    if (isWide) _heatSquarify(rest, x0 + thickness, y0, x1, y1, out);
    else        _heatSquarify(rest, x0, y0 + thickness, x1, y1, out);
  }

  // Map a portfolio weight % to a heat colour (green → yellow → orange → red)
  function _heatColor(weight) {
    const stops = [
      { at: 0,  rgb: [34,  197, 94]  },  // green
      { at: 10, rgb: [234, 179, 8]   },  // yellow
      { at: 22, rgb: [249, 115, 22]  },  // orange
      { at: 40, rgb: [239, 68,  68]  },  // red
    ];
    const w = Math.min(weight, stops[stops.length - 1].at);
    for (let i = 0; i < stops.length - 1; i++) {
      const lo = stops[i], hi = stops[i + 1];
      if (w <= hi.at) {
        const t = (w - lo.at) / (hi.at - lo.at);
        const r = Math.round(lo.rgb[0] + t * (hi.rgb[0] - lo.rgb[0]));
        const g = Math.round(lo.rgb[1] + t * (hi.rgb[1] - lo.rgb[1]));
        const b = Math.round(lo.rgb[2] + t * (hi.rgb[2] - lo.rgb[2]));
        return `rgb(${r},${g},${b})`;
      }
    }
    return `rgb(239,68,68)`;
  }

  function renderHeatmap(holdings) {
    const card   = document.getElementById('heatmap-card');
    const canvas = document.getElementById('heatmap-canvas');
    if (!card || !canvas) return;

    const row   = document.getElementById('heatmap-holdings-row');
    const valid = (holdings || []).filter(h => h.current_value_base > 0);
    if (!valid.length) { if (row) row.style.display = 'none'; return; }
    if (row) row.style.display = 'flex';

    const total = valid.reduce((s, h) => s + h.current_value_base, 0);
    const items = valid
      .map(h => ({
        ticker: h.ticker.replace(/\.[A-Z0-9]+$/i, ''),
        name:   h.company_name || h.ticker,
        weight: h.current_value_base / total * 100,
        glPct:  h.gain_loss_pct,
        market: h.market,
        value:  h.current_value_base,
      }))
      .sort((a, b) => b.value - a.value);

    const DPR = window.devicePixelRatio || 1;
    const W   = canvas.offsetWidth || 700;
    const H   = 260;
    canvas.width       = W * DPR;
    canvas.height      = H * DPR;
    canvas.style.height = H + 'px';

    const ctx = canvas.getContext('2d');
    ctx.scale(DPR, DPR);

    // Compute squarified layout
    const totalArea = W * H;
    const nodes = items.map(d => ({ ...d, area: d.value / total * totalArea }));
    const raw = [];
    _heatSquarify(nodes, 0, 0, W, H, raw);

    const GAP = 3;
    _heatTiles = [];

    raw.forEach(t => {
      const x = t.x + GAP, y = t.y + GAP;
      const w = t.w - GAP * 2,  h = t.h - GAP * 2;
      if (w < 2 || h < 2) return;

      _heatTiles.push({ x, y, w, h, ticker: t.ticker, name: t.name, weight: t.weight, glPct: t.glPct });

      // Tile background
      ctx.fillStyle = _heatColor(t.weight);
      ctx.beginPath();
      ctx.roundRect(x, y, w, h, 5);
      ctx.fill();

      // Subtle inner shadow for depth
      ctx.strokeStyle = 'rgba(0,0,0,.12)';
      ctx.lineWidth   = 1;
      ctx.stroke();

      if (w < 28 || h < 18) return;  // too small for any text

      ctx.textAlign    = 'center';
      ctx.textBaseline = 'middle';
      const cx = x + w / 2, cy = y + h / 2;
      const textAlpha = 'rgba(255,255,255,0.95)';

      if (h >= 72 && w >= 72) {
        // Large tile: ticker + weight + gain/loss
        const fs = Math.min(18, Math.floor(w / 4));
        ctx.fillStyle = textAlpha;
        ctx.font      = `700 ${fs}px Inter,system-ui,sans-serif`;
        ctx.fillText(t.ticker, cx, cy - fs * 0.75);

        ctx.font      = `500 ${Math.max(11, fs - 4)}px Inter,system-ui,sans-serif`;
        ctx.fillText(fmt(t.weight, 1) + '%', cx, cy + fs * 0.35);

        if (t.glPct != null && h >= 90) {
          const sign = t.glPct >= 0 ? '+' : '';
          ctx.fillStyle = t.glPct >= 0 ? 'rgba(187,247,208,.9)' : 'rgba(254,202,202,.9)';
          ctx.font      = `400 ${Math.max(10, fs - 5)}px Inter,system-ui,sans-serif`;
          ctx.fillText(sign + fmt(t.glPct, 2) + '%', cx, cy + fs * 1.35);
        }
      } else if (h >= 40 && w >= 42) {
        // Medium tile: ticker + weight
        const fs = Math.min(14, Math.floor(w / 4.5));
        ctx.fillStyle = textAlpha;
        ctx.font      = `700 ${fs}px Inter,system-ui,sans-serif`;
        ctx.fillText(t.ticker, cx, cy - fs * 0.6);
        ctx.font      = `400 ${Math.max(10, fs - 2)}px Inter,system-ui,sans-serif`;
        ctx.fillText(fmt(t.weight, 1) + '%', cx, cy + fs * 0.75);
      } else {
        // Small tile: just ticker
        const fs = Math.min(12, Math.floor(Math.min(w, h) / 2.5));
        ctx.fillStyle = textAlpha;
        ctx.font      = `700 ${fs}px Inter,system-ui,sans-serif`;
        ctx.fillText(t.ticker, cx, cy);
      }
    });
  }

  // Tooltip on heatmap mousemove
  (function _attachHeatmapTooltip() {
    const canvas = document.getElementById('heatmap-canvas');
    const tip    = document.getElementById('heatmap-tip');
    if (!canvas || !tip) return;

    canvas.addEventListener('mousemove', e => {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const hit = _heatTiles.find(t => mx >= t.x && mx <= t.x + t.w && my >= t.y && my <= t.y + t.h);
      if (!hit) { tip.style.display = 'none'; return; }

      const sym  = symOf(BASE_CCY);
      const sign = (hit.glPct ?? 0) >= 0 ? '+' : '';
      const glColor = hit.glPct == null ? 'var(--text-faint)' : (hit.glPct >= 0 ? '#16a34a' : '#dc2626');
      tip.innerHTML =
        `<div style="font-weight:700;color:var(--text)">${esc(hit.name)}</div>` +
        `<div style="color:var(--text-mid)">${esc(hit.ticker)} · ${sym}${fmt(hit.weight / 100 * (portfolioData?.totals?.total_current_value ?? 0))} (${fmt(hit.weight, 1)}%)</div>` +
        (hit.glPct != null ? `<div style="color:${glColor}">${sign}${fmt(hit.glPct, 2)}% gain/loss</div>` : '');

      // Position tooltip so it doesn't overflow the card
      const padEl = canvas.parentElement;
      const padRect = padEl.getBoundingClientRect();
      let tx = e.clientX - padRect.left + 12;
      let ty = e.clientY - padRect.top  + 12;
      tip.style.display = 'block';
      const tw = tip.offsetWidth, th = tip.offsetHeight;
      if (tx + tw > padRect.width  - 8) tx = e.clientX - padRect.left - tw - 12;
      if (ty + th > padRect.height - 8) ty = e.clientY - padRect.top  - th - 12;
      tip.style.left = tx + 'px';
      tip.style.top  = ty + 'px';
    });

    canvas.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
  })();

  // ── Look-through exposure bars ────────────────────────────────────────────

  const _etfCache = {};   // symbol → holdings array, session-scoped

  async function _fetchEtfHoldings(symbol) {
    if (symbol in _etfCache) return _etfCache[symbol];
    try {
      const res  = await fetch(`/api/etf-holdings?symbol=${encodeURIComponent(symbol)}`);
      const data = await res.json();
      _etfCache[symbol] = data;
      return data;
    } catch {
      _etfCache[symbol] = [];
      return [];
    }
  }

  async function renderTopHoldings(holdings) {
    const card = document.getElementById('top-holdings-card');
    const body = document.getElementById('top-holdings-body');
    if (!card || !body) return;

    const valid = (holdings || []).filter(h => h.current_value_base != null && h.current_value_base > 0);
    if (!valid.length) { card.style.display = 'none'; return; }
    card.style.display = 'flex';
    card.style.flexDirection = 'column';
    body.innerHTML = '<div style="padding:.4rem 0;font-size:.8rem;color:var(--text-faint)">Computing look-through exposure…</div>';

    const total = valid.reduce((s, h) => s + h.current_value_base, 0);

    // Fetch ETF holdings for all holdings in parallel
    const etfResults = await Promise.all(valid.map(h => _fetchEtfHoldings(h.ticker)));

    // Build exposure map: underlying key → { name, value, vias[] }
    const map = {};

    const addTo = (key, name, value, via) => {
      if (!map[key]) map[key] = { name, value: 0, vias: [] };
      map[key].value += value;
      if (via && !map[key].vias.includes(via)) map[key].vias.push(via);
    };

    valid.forEach((h, i) => {
      const etfHoldings = etfResults[i];
      const hVal        = h.current_value_base;
      const shortTicker = h.ticker.replace(/\.[A-Z0-9]+$/i, '');

      if (etfHoldings.length > 0) {
        // ETF — distribute to underlying components
        let covered = 0;
        etfHoldings.forEach(eh => {
          covered += eh.weight;
          addTo(eh.symbol || eh.name, eh.name, hVal * eh.weight, shortTicker);
        });
        // Uncovered remainder → "ETF other holdings" bucket
        const rem = 1 - covered;
        if (rem > 0.005) {
          const key = `__rem__${h.ticker}`;
          addTo(key, `${shortTicker} · other holdings`, hVal * rem, null);
          map[key]._isRemainder = true;
        }
      } else {
        // Direct holding
        const key = h.ticker;
        addTo(key, h.company_name || shortTicker, hVal, null);
        map[key]._direct = true;
        map[key]._market = h.market;
        map[key]._ticker = shortTicker;
      }
    });

    // Sort: named holdings by value desc, remainder buckets last
    const entries = Object.values(map).sort((a, b) => {
      if (a._isRemainder && !b._isRemainder) return  1;
      if (!a._isRemainder && b._isRemainder) return -1;
      return b.value - a.value;
    });

    const TOP_N       = 12;
    const named       = entries.filter(e => !e._isRemainder);
    const remainder   = entries.filter(e =>  e._isRemainder);
    const top         = named.slice(0, TOP_N);
    const overflow    = named.slice(TOP_N);

    const sym    = symOf(BASE_CCY);
    const maxVal = top.length ? top[0].value : 1;

    const makeRow = (e, label, value) => {
      const pct   = value / total * 100;
      const barW  = value / maxVal * 100;
      const color = e._direct ? (MARKET_COLORS[e._market] || FALLBACK_COLOR) : '#6366f1';
      const viaBadge = e.vias?.length
        ? `<span style="font-size:.63rem;background:rgba(99,102,241,.12);color:#6366f1;border-radius:3px;padding:.05rem .3rem;margin-left:.3rem;vertical-align:middle;white-space:nowrap">via ${esc(e.vias.slice(0,2).join(', '))}${e.vias.length > 2 ? ' +' + (e.vias.length - 2) : ''}</span>`
        : '';
      const sub = e._direct
        ? `<div style="font-size:.69rem;color:var(--text-faint)">${esc(e._ticker || '')}</div>`
        : '';
      return `
        <div style="display:grid;grid-template-columns:140px 1fr 46px 88px;align-items:center;gap:.55rem;padding:.28rem 0;border-bottom:1px solid var(--border-subtle)">
          <div style="min-width:0">
            <div style="font-size:.8rem;font-weight:600;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(e.name)}">${esc(label.length > 20 ? label.slice(0,19)+'…' : label)}${viaBadge}</div>
            ${sub}
          </div>
          <div style="height:16px;background:var(--border-subtle);border-radius:3px;overflow:hidden">
            <div style="height:100%;width:${barW.toFixed(1)}%;background:${color};border-radius:3px;opacity:${e._isRemainder ? '.45' : '1'}"></div>
          </div>
          <div style="font-size:.77rem;font-weight:600;color:var(--text-mid);text-align:right">${fmt(pct, 1)}%</div>
          <div style="font-size:.77rem;color:var(--text-mid);text-align:right">${sym}${fmt(value)}</div>
        </div>`;
    };

    let html = '';
    top.forEach(e => { html += makeRow(e, e.name, e.value); });

    // Aggregate remainder ETF buckets + overflow named entries
    const residualEntries = [...remainder, ...overflow];
    if (residualEntries.length) {
      const residualVal = residualEntries.reduce((s, e) => s + e.value, 0);
      const dummy = { _isRemainder: true, name: 'Others', vias: [] };
      html += makeRow(dummy, 'Others', residualVal);
    }

    // Legend key
    const hasLookThrough = valid.some((h, i) => etfResults[i].length > 0);
    if (hasLookThrough) {
      html += `<div style="margin-top:.65rem;display:flex;gap:1rem;font-size:.72rem;color:var(--text-faint)">
        <span style="display:flex;align-items:center;gap:.3rem"><span style="width:10px;height:10px;border-radius:2px;background:#22c55e;display:inline-block"></span>Direct (SGX)</span>
        <span style="display:flex;align-items:center;gap:.3rem"><span style="width:10px;height:10px;border-radius:2px;background:#3b82f6;display:inline-block"></span>Direct (US)</span>
        <span style="display:flex;align-items:center;gap:.3rem"><span style="width:10px;height:10px;border-radius:2px;background:#6366f1;display:inline-block"></span>ETF look-through</span>
      </div>`;
    }

    body.innerHTML = html;
  }

  // ── Dividend state ────────────────────────────────────────────────────────
  let divOpen      = true;
  let divData      = null;   // last response from /api/dividends; used by refreshRealizedTile
  let selectedDivs = new Set();
  let divEditIdx   = null;   // original index of dividend record being edited, or null

  // Updates the dividend amount field label to show the correct currency symbol
  // when the market dropdown changes (e.g. SGX → 'Amount (S$)').
  function updateDivAmountLabel() {
    const sym = symOf(ccyOf(document.getElementById('d-market').value));
    document.getElementById('d-amount-label').textContent = `Amount (${sym})`;
  }

  // Loads dividend data from the API, renders the dividend log, and refreshes the summary tile.
  async function fetchDividends() {
    try {
      const res  = await fetch('/api/dividends');
      const data = await res.json();
      divData = data;
      renderDivLog(data);
      _realizedDivsBase = data.total_base || 0;
      refreshRealizedTile();
    } catch { /* silently ignore */ }
  }

  // Renders the dividend yield summary table (annual div/share, yield on cost, current yield).
  // Only shown when at least one holding has dividend rate data from yfinance.
  function renderDivYield(holdings) {
    const wrap = document.getElementById('div-yield-wrap');
    const tbl  = document.getElementById('div-yield-table');
    if (!wrap || !tbl) return;
    const payers = (holdings || []).filter(h => h.yield_on_cost != null || h.current_yield != null);
    if (!payers.length) { wrap.style.display = 'none'; return; }
    wrap.style.display = '';
    tbl.innerHTML = `<table>
      <thead><tr>
        <th>Ticker</th><th>Market</th>
        <th style="text-align:right">Ann. Div/Share</th>
        <th style="text-align:right">Yield on Cost</th>
        <th style="text-align:right">Current Yield</th>
      </tr></thead>
      <tbody>${payers.map(h => `
        <tr>
          <td><strong>${esc(h.ticker)}</strong></td>
          <td><span class="badge badge-${h.market.toLowerCase()}">${esc(h.market)}</span></td>
          <td style="text-align:right">${h.div_rate != null ? fmtCcy(h.div_rate, h.currency) : '—'}</td>
          <td style="text-align:right" class="pos">${h.yield_on_cost != null ? fmt(h.yield_on_cost) + '%' : '—'}</td>
          <td style="text-align:right" class="pos">${h.current_yield  != null ? fmt(h.current_yield)  + '%' : '—'}</td>
        </tr>`).join('')}
      </tbody></table>`;
  }

  // Renders the Dividend Income table and triggers the monthly bar chart.
  // Returns a sorted copy of the dividends array using the current sort column/direction.
  function getDivsSorted(dividends) {
    return [...dividends].sort((a, b) => {
      let va, vb;
      if      (divSortCol === 'ticker') { va = a.ticker.toLowerCase(); vb = b.ticker.toLowerCase(); }
      else if (divSortCol === 'market') { va = a.market.toLowerCase(); vb = b.market.toLowerCase(); }
      else if (divSortCol === 'amount') { va = a.amount_base ?? a.amount; vb = b.amount_base ?? b.amount; }
      else { va = a.date; vb = b.date; }
      return divSortDir * (va < vb ? -1 : va > vb ? 1 : 0);
    });
  }

  function renderDivLog(data, resetPage = true) {
    if (resetPage) { selectedDivs.clear(); divPage = 1; resetDivForm(); }
    updateDivToolbar();
    const dividends = data.dividends || [];
    const totalBase = data.total_base  || 0;

    const totalEl = document.getElementById('div-total');
    if (totalEl) totalEl.textContent = dividends.length ? `Total: ${fmtBase(totalBase)}` : '';

    const wrap = document.getElementById('div-wrap');
    if (!dividends.length) {
      wrap.innerHTML = '<div class="empty">No dividends recorded.</div>';
      document.getElementById('div-chart-wrap').style.display = 'none';
      return;
    }

    requestAnimationFrame(() => renderDivChart(dividends));

    const sorted    = getDivsSorted(dividends);
    const origIdxOf = sorted.map(d => d.id);
    const total = sorted.length;
    const start = (divPage - 1) * PAGE_SIZE;
    const page  = sorted.slice(start, start + PAGE_SIZE);

    const rows = page.map((d, j) => {
      const origIdx = origIdxOf[start + j];
      const sel     = selectedDivs.has(origIdx);
      return `
      <tr id="div-row-${origIdx}"${sel ? ' class="selected"' : ''}>
        <td class="chk"><input type="checkbox" id="dchk-${origIdx}"${sel ? ' checked' : ''} onchange="toggleSelectDiv('${origIdx}')"></td>
        <td style="text-align:left">${esc(d.date)}</td>
        <td style="text-align:left">
          <strong>${esc(d.ticker)}</strong>
          ${d.company_name ? `<div style="font-size:11px;color:#94a3b8;margin-top:1px;line-height:1.3">${esc(d.company_name)}</div>` : ''}
        </td>
        <td style="text-align:left"><span class="badge badge-${d.market.toLowerCase()}">${esc(d.market)}</span></td>
        <td class="pos">${fmtCcy(d.amount, d.currency)}</td>
      </tr>`;
    }).join('');

    const pageOrigIdxs = page.map((_, j) => origIdxOf[start + j]);
    const pageSelCount = pageOrigIdxs.filter(oi => selectedDivs.has(oi)).length;
    const allChkd = page.length > 0 && pageSelCount === page.length;
    const indet   = pageSelCount > 0 && pageSelCount < page.length;

    wrap.innerHTML = `
      <table>
        <thead><tr>
          <th class="chk"><input type="checkbox" id="dchk-all"${allChkd ? ' checked' : ''}
            onchange="toggleSelectAllDivs(this.checked)" title="Select all"></th>
          ${['date','ticker','market','amount'].map((c,i) => {
            const labels = ['Date','Ticker','Market','Amount'];
            const active = divSortCol === c;
            const arrow  = active ? (divSortDir === -1 ? ' ▼' : ' ▲') : '';
            const left   = i < 3 ? ' style="text-align:left"' : '';
            return `<th class="sortable${active?' sort-active':''}"${left} onclick="setDivSort('${c}')">${labels[i]}${arrow}</th>`;
          }).join('')}
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${buildPagination(divPage, total, 'setDivPage')}`;

    const chkAll = document.getElementById('dchk-all');
    if (chkAll) chkAll.indeterminate = indet;
  }

  // Pagination handler for the Dividend Income table.
  function setDivPage(p) {
    divPage = p;
    renderDivLog(divData, false);
  }

  // Column sort handler for the Dividend Income table.
  function setDivSort(col) {
    if (divSortCol === col) divSortDir = -divSortDir;
    else { divSortCol = col; divSortDir = (col === 'ticker' || col === 'market') ? 1 : -1; }
    divPage = 1;
    renderDivLog(divData, false);
  }

  // Draws a stacked monthly bar chart of dividend income (last 12 months).
  // Bars are stacked by sector and use the same colours as the industry donut chart.
  function renderDivChart(dividends) {
    const chartWrap = document.getElementById('div-chart-wrap');
    const canvas    = document.getElementById('div-chart');
    const legendEl  = document.getElementById('div-chart-legend');
    if (!dividends.length) { chartWrap.style.display = 'none'; return; }
    chartWrap.style.display = '';

    // Build sector lookup from current holdings
    const sectorMap = {};
    (portfolioData?.holdings || []).forEach(h => { sectorMap[h.ticker] = h.sector || 'Unknown'; });

    // Aggregate by YYYY-MM and sector
    const monthly = {};
    dividends.forEach(d => {
      const key    = d.date.slice(0, 7);
      const sector = sectorMap[d.ticker] || d.sector || 'Unknown';
      if (!monthly[key]) monthly[key] = {};
      monthly[key][sector] = (monthly[key][sector] || 0) + (d.amount_base || 0);
    });

    const keys = Object.keys(monthly).sort().slice(-12);

    // Order sectors by total amount desc so the largest sits at the bar base
    const sectorTotals = {};
    keys.forEach(k => Object.entries(monthly[k]).forEach(([s, v]) => {
      sectorTotals[s] = (sectorTotals[s] || 0) + v;
    }));
    const sectors = Object.keys(sectorTotals).sort((a, b) => sectorTotals[b] - sectorTotals[a]);
    // Use the shared map so colours match the industry donut exactly;
    // fall back to palette for any sector not yet seen in holdings
    const sectorColor = {};
    let fallbackIdx = Object.keys(sectorColorMap).length;
    sectors.forEach(s => {
      sectorColor[s] = sectorColorMap[s] ?? SECTOR_PALETTE[fallbackIdx++ % SECTOR_PALETTE.length];
    });

    const monthTotals = keys.map(k => Object.values(monthly[k]).reduce((a, b) => a + b, 0));
    const maxVal      = Math.max(...monthTotals, 0.01);

    const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const dpr  = window.devicePixelRatio || 1;
    const W    = canvas.parentElement.clientWidth || 600;
    const H    = 200;
    canvas.width        = W * dpr;
    canvas.height       = H * dpr;
    canvas.style.width  = W + 'px';
    canvas.style.height = H + 'px';

    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);

    const PAD_L = 52, PAD_R = 12, PAD_T = 24, PAD_B = 28;
    const chartW  = W - PAD_L - PAD_R;
    const chartH  = H - PAD_T - PAD_B;
    const barSlot = chartW / keys.length;
    // Cap bar width so a small number of bars doesn't stretch across the full chart
    const barW    = Math.min(Math.max(barSlot * 0.70, 8), 56);
    const barOff  = (barSlot - barW) / 2;

    // Y-axis gridlines + labels
    for (let i = 0; i <= 4; i++) {
      const y = PAD_T + chartH - chartH * (i / 4);
      ctx.strokeStyle = '#e2e8f0'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(PAD_L + chartW, y); ctx.stroke();
      ctx.fillStyle = '#94a3b8';
      ctx.font = '10px -apple-system,sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(fmtK(maxVal * i / 4), PAD_L - 5, y + 3.5);
    }

    // Stacked bars
    keys.forEach((key, i) => {
      const data           = monthly[key] || {};
      const total          = monthTotals[i];
      const x              = PAD_L + i * barSlot + barOff;
      const activeSectors  = sectors.filter(s => (data[s] || 0) > 0);
      let   yStack         = PAD_T + chartH;

      activeSectors.forEach((sector, si) => {
        const segH  = (data[sector] / maxVal) * chartH;
        const isTop = si === activeSectors.length - 1;
        yStack -= segH;
        ctx.fillStyle = sectorColor[sector];
        ctx.beginPath();
        if (isTop && ctx.roundRect) {
          ctx.roundRect(x, yStack, barW, segH, [3, 3, 0, 0]);
        } else {
          ctx.rect(x, yStack, barW, segH);
        }
        ctx.fill();
      });

      // Total label above bar
      if (total > 0) {
        const barTop = PAD_T + chartH - (total / maxVal) * chartH;
        ctx.fillStyle = '#374151';
        ctx.font      = '600 9px -apple-system,sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(fmtBase(total), x + barW / 2, barTop - 5);
      }

      // X-axis label
      const [yr, mo] = key.split('-');
      const label    = MONTH_NAMES[+mo - 1] + (keys.length > 6 ? " '" + yr.slice(2) : '');
      ctx.fillStyle  = '#64748b';
      ctx.font       = '9px -apple-system,sans-serif';
      ctx.textAlign  = 'center';
      ctx.fillText(label, x + barW / 2, PAD_T + chartH + 16);
    });

    // X baseline
    ctx.strokeStyle = '#cbd5e1'; ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(PAD_L, PAD_T + chartH); ctx.lineTo(PAD_L + chartW, PAD_T + chartH);
    ctx.stroke();

    // Legend (only when more than one sector present)
    legendEl.innerHTML = sectors.length > 1
      ? sectors.map(s => `
          <span style="display:flex;align-items:center;gap:.3rem">
            <span style="width:10px;height:10px;border-radius:2px;background:${sectorColor[s]};flex-shrink:0;display:inline-block"></span>
            ${esc(s)}
          </span>`).join('')
      : '';
  }

  // Toggles a dividend row's selection state.
  function toggleSelectDiv(origIdx) {
    if (selectedDivs.has(origIdx)) selectedDivs.delete(origIdx);
    else selectedDivs.add(origIdx);
    renderDivLog(divData, false);
  }

  // Selects or deselects all rows on the current Dividend Income page using original indices.
  function toggleSelectAllDivs(checked) {
    const dividends = divData?.dividends || [];
    const sorted    = getDivsSorted(dividends);
    const start     = (divPage - 1) * PAGE_SIZE;
    const page      = sorted.slice(start, start + PAGE_SIZE);
    page.forEach(d => {
      const origIdx = d.id;
      checked ? selectedDivs.add(origIdx) : selectedDivs.delete(origIdx);
    });
    renderDivLog(divData, false);
  }

  // Syncs the Edit and Delete button states with the current dividend selection count.
  function updateDivToolbar() {
    const count     = selectedDivs.size;
    const editBtn   = document.getElementById('div-edit-btn');
    const deleteBtn = document.getElementById('div-delete-btn');
    const countEl   = document.getElementById('div-sel-count');
    if (editBtn)   editBtn.disabled    = count !== 1;
    if (deleteBtn) deleteBtn.disabled  = count === 0;
    if (countEl)   countEl.textContent = count > 0 ? `${count} selected` : '';
  }

  // Populates the dividend form with the selected row's data for editing.
  function startDivEdit() {
    const id = [...selectedDivs][0];
    const div = divData.dividends.find(d => d.id === id);
    populateDivForm(div, id);
  }

  // Loads a dividend record into the form fields for editing.
  function populateDivForm(div, origIdx) {
    divEditIdx = origIdx;
    _tickerValid['d'] = true;
    document.getElementById('d-ticker').value = div.ticker;
    document.getElementById('d-market').value = div.market;
    document.getElementById('d-date').value   = div.date;
    document.getElementById('d-amount').value = div.amount;
    updateDivAmountLabel();
    document.getElementById('div-submit-btn').textContent = 'Save Changes';
    document.getElementById('div-cancel-btn').style.display = '';
    document.getElementById('div-card').classList.add('editing');
  }

  // Resets the dividend form to its default empty state.
  function resetDivForm() {
    divEditIdx = null;
    _tickerValid['d'] = null;
    document.getElementById('d-ticker').value = '';
    document.getElementById('d-amount').value = '';
    document.getElementById('d-date').value   = new Date().toISOString().slice(0, 10);
    document.getElementById('d-market').value = 'SGX';
    updateDivAmountLabel();
    document.getElementById('div-submit-btn').textContent = 'Add';
    document.getElementById('div-cancel-btn').style.display = 'none';
    document.getElementById('div-card').classList.remove('editing');
    clearTickerHint('d');
  }

  // Cancels an in-progress dividend edit, clears selection, and resets the form.
  function cancelDivEdit() {
    selectedDivs.clear();
    resetDivForm();
    renderDivLog(divData, false);
  }

  // Submits a new dividend record (POST) or saves an edit (PUT), then refreshes the log.
  async function addDividend() {
    const ticker = document.getElementById('d-ticker').value.trim().toUpperCase();
    const market = document.getElementById('d-market').value;
    const date   = document.getElementById('d-date').value;
    const amount = parseFloat(document.getElementById('d-amount').value);

    if (!ticker)                      { showToast('Enter a ticker symbol.'); return; }
    if (_tickerValid['d'] === false)  { showToast(`"${ticker}" was not found on ${document.getElementById('d-market').value} — check the ticker and market.`); return; }
    if (_tickerValid['d'] === null)   { showToast('Ticker is still being validated — please wait a moment.'); return; }
    if (!date)                        { showToast('Pick a date.'); return; }
    if (!amount || amount <= 0)       { showToast('Amount must be > 0.'); return; }

    const isEdit = divEditIdx !== null;
    const url    = isEdit ? `/api/dividend/${divEditIdx}` : '/api/dividend';
    const method = isEdit ? 'PUT' : 'POST';

    setLoading(true);
    try {
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker, market, date, amount }),
      });
      if (!res.ok) { const err = await res.json(); showToast(err.error || 'Failed to save.'); return; }
      showToast(isEdit ? `Updated ${ticker}` : `Dividend logged for ${ticker}`);
      selectedDivs.clear();
      resetDivForm();
      await fetchDividends();
    } catch {
      showToast('Network error — dividend not saved.');
    } finally {
      setLoading(false);
    }
  }

  // Deletes all selected dividend rows (using original indices, descending to preserve offsets).
  async function deleteSelectedDividends() {
    const count = selectedDivs.size;
    if (count === 0) return;
    if (!await showConfirm(`Delete ${count} dividend entr${count === 1 ? 'y' : 'ies'}?`, 'Delete')) return;
    const ids = [...selectedDivs];
    setLoading(true);
    try {
      for (const id of ids) {
        const res = await fetch(`/api/dividend/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error();
      }
      showToast(`Deleted ${count} dividend entr${count === 1 ? 'y' : 'ies'}.`);
      selectedDivs.clear();
      resetDivForm();
      await fetchDividends();
    } catch {
      showToast('Failed to delete one or more dividends.');
    } finally {
      setLoading(false);
    }
  }

  // Collapses or expands the Dividend Income card body.
  function toggleDiv() {
    divOpen = !divOpen;
    document.getElementById('div-body').classList.toggle('open', divOpen);
    document.getElementById('div-icon').innerHTML  = divOpen ? '&#x25BC;' : '&#x25B6;';
    document.getElementById('div-lbl').textContent = divOpen ? 'Hide' : 'Show';
  }

  // ── Tabs ──────────────────────────────────────────────────────────────────

  function switchTab(tab) {
    ['dashboard', 'transactions', 'settings', 'data'].forEach(t => {
      document.getElementById('tab-' + t).style.display = tab === t ? '' : 'none';
    });
    document.querySelectorAll('.tab-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.tab === tab));
    // Hide the Dashboard/Transactions tab bar when on Settings or Data pages
    const tabBar = document.querySelector('.tab-bar');
    if (tabBar) tabBar.style.display = (tab === 'settings' || tab === 'data') ? 'none' : '';
    if (tab === 'settings' && appSettings) renderSettingsPage();
  }

  // ── Sells ─────────────────────────────────────────────────────────────────

  // Collapses or expands the Realized Gains card body.
  function toggleSell() {
    sellOpen = !sellOpen;
    document.getElementById('sell-body').classList.toggle('open', sellOpen);
    document.getElementById('sell-icon').textContent = sellOpen ? '▼' : '▶';
    document.getElementById('sell-lbl').textContent  = sellOpen ? 'Hide' : 'Show';
  }

  // Updates the sell form price label to reflect the selected market's currency.
  function updateSellPriceLabel() {
    const mkt = document.getElementById('s-market').value;
    const sym = symOf(ccyOf(mkt));
    document.getElementById('s-price-label').textContent = `Price/Unit (${sym})`;
    document.getElementById('s-fees-label').textContent  = `Fees (${sym})`;
  }

  // Updates the purchase form price and fees labels to reflect the selected market's currency.
  function updatePurchasePriceLabel() {
    const mkt = document.getElementById('f-market').value;
    const sym = symOf(ccyOf(mkt));
    document.getElementById('f-price-label').textContent = `Price/Unit (${sym})`;
    document.getElementById('f-fees-label').textContent  = `Fees (${sym})`;
  }

  // Loads sell data from the API, renders the Realized Gains table, and refreshes the summary tile.
  async function fetchSells() {
    try {
      const res  = await fetch('/api/sells');
      if (!res.ok) return;
      const data = await res.json();
      sellData = data;
      renderSellLog(data.sells);
      const v    = data.total_realized_base;
      const sign = v >= 0 ? '+' : '';
      const sym  = symOf(data.base_currency);
      // Card header total
      const totalEl = document.getElementById('sell-total');
      if (v != null) {
        totalEl.textContent = `Realized: ${sign}${sym}${fmt(Math.abs(v))}`;
        totalEl.style.color = v > 0 ? '#16a34a' : v < 0 ? '#dc2626' : '#64748b';
      } else {
        totalEl.textContent = '';
      }
      _realizedGainsBase = v || 0;
      refreshRealizedTile();
    } catch { /* silent */ }
  }

  // Renders the Realized Gains table with sorting and pagination.
  // Returns a sorted copy of the sells array using the current sort column/direction.
  function getSellsSorted(sells) {
    return [...sells].sort((a, b) => {
      let va, vb;
      if      (sellSortCol === 'ticker')    { va = a.ticker.toLowerCase(); vb = b.ticker.toLowerCase(); }
      else if (sellSortCol === 'market')    { va = a.market.toLowerCase(); vb = b.market.toLowerCase(); }
      else if (sellSortCol === 'units')     { va = a.units; vb = b.units; }
      else if (sellSortCol === 'price_sold'){ va = a.price_sold; vb = b.price_sold; }
      else if (sellSortCol === 'fees')      { va = a.fees ?? 0;  vb = b.fees ?? 0; }
      else if (sellSortCol === 'avg_cost')  { va = a.avg_cost;   vb = b.avg_cost; }
      else if (sellSortCol === 'gain')      { va = a.realized_gain ?? -Infinity; vb = b.realized_gain ?? -Infinity; }
      else if (sellSortCol === 'gain_pct')  { va = a.realized_gain_pct ?? -Infinity; vb = b.realized_gain_pct ?? -Infinity; }
      else { va = a.date; vb = b.date; }
      return sellSortDir * (va < vb ? -1 : va > vb ? 1 : 0);
    });
  }

  function renderSellLog(sells, resetPage = true) {
    if (resetPage) { selectedSells.clear(); sellPage = 1; resetSellForm(); }
    const wrap = document.getElementById('sell-wrap');
    if (!sells?.length) {
      wrap.innerHTML = '<div class="empty">No sales recorded.</div>';
      updateSellToolbar();
      return;
    }
    const sorted    = getSellsSorted(sells);
    const origIdxOf = sorted.map(s => s.id);
    const total = sorted.length;
    const start = (sellPage - 1) * PAGE_SIZE;
    const page  = sorted.slice(start, start + PAGE_SIZE);

    const sellCols = ['date','ticker','market','units','price_sold','fees','avg_cost','gain','gain_pct'];
    const sellLabels = ['Date','Ticker','Market','Units','Sold At','Fees','Avg Cost','Gain/Loss','%'];
    const sth = (c, i) => {
      const active = sellSortCol === c;
      const arrow  = active ? (sellSortDir === -1 ? ' ▼' : ' ▲') : '';
      const left   = i < 3 ? ' style="text-align:left"' : '';
      return `<th class="sortable${active?' sort-active':''}"${left} onclick="setSellSort('${c}')">${sellLabels[i]}${arrow}</th>`;
    };
    let html = `<table><thead><tr>
      <th><input type="checkbox" id="sell-chk-all" onchange="toggleSelectAllSells(this.checked)"></th>
      ${sellCols.map((c,i) => sth(c,i)).join('')}
    </tr></thead><tbody>`;
    page.forEach((s, j) => {
      const origIdx = origIdxOf[start + j];
      const sel     = selectedSells.has(origIdx);
      const cls     = colorCls(s.realized_gain);
      const sym     = symOf(s.currency);
      const glAmt   = s.realized_gain != null
        ? (s.realized_gain >= 0 ? '+' : '-') + sym + fmt(Math.abs(s.realized_gain))
        : 'N/A';
      const glPct   = s.realized_gain_pct != null
        ? (s.realized_gain_pct >= 0 ? '+' : '') + fmt(s.realized_gain_pct) + '%'
        : 'N/A';
      html += `<tr${sel ? ' class="selected"' : ''}>
        <td><input type="checkbox"${sel ? ' checked' : ''} onchange="toggleSelectSell('${origIdx}')"></td>
        <td style="text-align:left">${esc(s.date)}</td>
        <td style="text-align:left">
          <strong>${esc(s.ticker)}</strong>
          ${s.company_name ? `<div style="font-size:11px;color:#94a3b8;margin-top:1px;line-height:1.3">${esc(s.company_name)}</div>` : ''}
        </td>
        <td style="text-align:left">${esc(s.market)}</td>
        <td>${fmt(s.units, 4)}</td>
        <td>${sym}${fmt(s.price_sold)}</td>
        <td>${s.fees ? sym + fmt(s.fees) : '—'}</td>
        <td>${sym}${fmt(s.avg_cost)}</td>
        <td class="${cls}">${glAmt}</td>
        <td class="${cls}">${glPct}</td>
      </tr>`;
    });
    html += `</tbody></table>${buildPagination(sellPage, total, 'setSellPage')}`;
    wrap.innerHTML = html;
    const chkAll = document.getElementById('sell-chk-all');
    if (chkAll) {
      const pageOrigIdxs  = page.map((_, j) => origIdxOf[start + j]);
      const pageSelCount  = pageOrigIdxs.filter(oi => selectedSells.has(oi)).length;
      chkAll.checked       = page.length > 0 && pageSelCount === page.length;
      chkAll.indeterminate = pageSelCount > 0 && pageSelCount < page.length;
    }
    updateSellToolbar();
  }

  // Pagination handler for the Realized Gains table.
  function setSellPage(p) {
    sellPage = p;
    renderSellLog(sellData?.sells || [], false);
  }

  // Column sort handler for the Realized Gains table.
  function setSellSort(col) {
    if (sellSortCol === col) sellSortDir = -sellSortDir;
    else { sellSortCol = col; sellSortDir = (col === 'ticker' || col === 'market') ? 1 : -1; }
    sellPage = 1;
    renderSellLog(sellData?.sells || [], false);
  }

  // Toggles a sell row's selection state.
  function toggleSelectSell(origIdx) {
    if (selectedSells.has(origIdx)) selectedSells.delete(origIdx);
    else selectedSells.add(origIdx);
    renderSellLog(sellData?.sells || [], false);
  }

  // Selects or deselects all rows on the current Realized Gains page using original indices.
  function toggleSelectAllSells(checked) {
    const sells  = sellData?.sells || [];
    const sorted = getSellsSorted(sells);
    const start  = (sellPage - 1) * PAGE_SIZE;
    const page   = sorted.slice(start, start + PAGE_SIZE);
    page.forEach(s => {
      const origIdx = s.id;
      checked ? selectedSells.add(origIdx) : selectedSells.delete(origIdx);
    });
    renderSellLog(sells, false);
  }

  // Syncs the Edit and Delete button states with the current sell selection count.
  function updateSellToolbar() {
    const n = selectedSells.size;
    document.getElementById('sell-sel-count').textContent = n > 0 ? `${n} selected` : '';
    document.getElementById('sell-edit-btn').disabled     = n !== 1;
    document.getElementById('sell-delete-btn').disabled   = n === 0;
  }

  // Populates the sell form with the selected row's data for editing.
  function startSellEdit() {
    const id = [...selectedSells][0];
    const sell = sellData.sells.find(s => s.id === id);
    populateSellForm(sell, id);
  }

  // Loads a sell record into the form fields for editing.
  function populateSellForm(sell, origIdx) {
    sellEditIdx = origIdx;
    _tickerValid['s'] = true;
    document.getElementById('s-ticker').value = sell.ticker;
    document.getElementById('s-market').value = sell.market;
    document.getElementById('s-date').value   = sell.date;
    document.getElementById('s-units').value  = sell.units;
    document.getElementById('s-price').value  = sell.price_sold;
    document.getElementById('s-fees').value   = sell.fees || '';
    updateSellPriceLabel();
    document.getElementById('sell-submit-btn').textContent = 'Save Changes';
    document.getElementById('sell-cancel-btn').style.display = '';
    document.getElementById('sell-card').classList.add('editing');
  }

  // Resets the sell form to its default empty state.
  function resetSellForm() {
    sellEditIdx = null;
    _tickerValid['s'] = null;
    document.getElementById('s-ticker').value = '';
    document.getElementById('s-units').value  = '';
    document.getElementById('s-price').value  = '';
    document.getElementById('s-fees').value   = '';
    document.getElementById('s-date').value   = new Date().toISOString().slice(0, 10);
    document.getElementById('s-market').value = 'SGX';
    updateSellPriceLabel();
    document.getElementById('sell-submit-btn').textContent = 'Sell';
    document.getElementById('sell-cancel-btn').style.display = 'none';
    document.getElementById('sell-card').classList.remove('editing');
    clearTickerHint('s');
  }

  // Cancels an in-progress sell edit, clears selection, and resets the form.
  function cancelSellEdit() {
    selectedSells.clear();
    resetSellForm();
    renderSellLog(sellData?.sells || [], false);
  }

  // Submits a new sell record (POST) or saves an edit (PUT), then refreshes data.
  async function addSell() {
    const ticker = document.getElementById('s-ticker').value.trim().toUpperCase();
    const market = document.getElementById('s-market').value;
    const date   = document.getElementById('s-date').value;
    const units  = parseFloat(document.getElementById('s-units').value);
    const price  = parseFloat(document.getElementById('s-price').value);
    const fees   = parseFloat(document.getElementById('s-fees').value) || 0;
    if (!ticker)                      { showToast('Enter a ticker symbol.'); return; }
    if (_tickerValid['s'] === false)  { showToast(`"${ticker}" was not found on ${document.getElementById('s-market').value} — check the ticker and market.`); return; }
    if (_tickerValid['s'] === null)   { showToast('Ticker is still being validated — please wait a moment.'); return; }
    if (!date || isNaN(units) || isNaN(price) || units <= 0 || price <= 0) {
      showToast('Please fill in all sell fields.');
      return;
    }
    const isEdit = sellEditIdx !== null;
    const url    = isEdit ? `/api/sell/${sellEditIdx}` : '/api/sell';
    const method = isEdit ? 'PUT' : 'POST';
    try {
      const res  = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker, market, date, units, price_sold: price, fees }),
      });
      const data = await res.json();
      if (!res.ok) { showToast(data.error || 'Failed to record sale.'); return; }
      showToast(isEdit ? `Updated ${ticker}` : `Sale recorded: ${fmt(units, 4)} × ${ticker}`);
      selectedSells.clear();
      resetSellForm();
      await fetchSells();
      await fetchPortfolio();
    } catch {
      showToast('Network error — sale not saved.');
    }
  }

  // Deletes all selected sell rows (using original indices) and refreshes both sells and portfolio.
  async function deleteSelectedSells() {
    const ids = [...selectedSells];
    if (!ids.length) return;
    const count = ids.length;
    if (!await showConfirm(`Delete ${count} sale entr${count === 1 ? 'y' : 'ies'}?`, 'Delete')) return;
    try {
      for (const id of ids) {
        const res = await fetch(`/api/sell/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error();
      }
      showToast(`Deleted ${count} sale entr${count === 1 ? 'y' : 'ies'}.`);
      selectedSells.clear();
      resetSellForm();
      await fetchSells();
      await fetchPortfolio();
    } catch {
      showToast('Failed to delete one or more sales.');
    }
  }

  // Triggers the server-side backfill of missing monthly snapshots using historical prices.
  async function backfillHistory() {
    const btn = document.getElementById('pb-backfill');
    btn.textContent = 'Working…';
    btn.disabled    = true;
    try {
      const res  = await fetch('/api/backfill', { method: 'POST' });
      const data = await res.json();
      if (data.added > 0) {
        showToast(`Backfill complete — ${data.added} month${data.added === 1 ? '' : 's'} added.`);
        await fetchSnapshots();
      } else {
        showToast('Nothing new to backfill — all months already recorded.');
      }
    } catch {
      showToast('Backfill failed — check server logs.');
    } finally {
      btn.textContent = 'Backfill History';
      btn.disabled    = false;
    }
  }

  // ── Header menu ───────────────────────────────────────────────────────────

  function toggleMenu(e) {
    e.stopPropagation();
    document.getElementById('app-menu').classList.toggle('open');
  }

  function closeMenu() {
    document.getElementById('app-menu').classList.remove('open');
  }

  document.addEventListener('click', () => closeMenu());

  async function doExport() {
    try {
      showToast('Preparing export…', 4000);
      // Packaged app (PyWebView) cannot trigger browser downloads — save to Desktop instead
      if (appSettings && appSettings.is_frozen) {
        const res  = await fetch('/api/export/save');
        if (!res.ok) throw new Error('Server error ' + res.status);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        showToast('Export saved — opening ' + data.filename, 4000);
      } else {
        const res  = await fetch('/api/export');
        if (!res.ok) throw new Error('Server error ' + res.status);
        const blob = await res.blob();
        const url  = URL.createObjectURL(blob);
        const a    = document.createElement('a');
        const date = new Date().toISOString().slice(0, 10).replace(/-/g, '');
        a.href     = url;
        a.download = `investment_tracker_${date}.xlsx`;
        document.body.appendChild(a);
        a.click();
        setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 2000);
      }
    } catch (err) {
      showToast('Export failed — ' + err.message);
    }
  }

  async function downloadImportTemplate() {
    try {
      if (appSettings && appSettings.is_frozen) {
        const res  = await fetch('/api/import/template/save');
        if (!res.ok) throw new Error('Server error ' + res.status);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        showToast('Template saved — opening ' + data.filename, 4000);
      } else {
        const res  = await fetch('/api/import/template');
        if (!res.ok) throw new Error('Server error ' + res.status);
        const blob = await res.blob();
        const url  = URL.createObjectURL(blob);
        const a    = document.createElement('a');
        a.href     = url;
        a.download = 'investment_tracker_import_template.xlsx';
        document.body.appendChild(a);
        a.click();
        setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 2000);
      }
    } catch (err) {
      showToast('Download failed — ' + err.message);
    }
  }

  async function importFromExcel(input) {
    if (!input.files || !input.files[0]) return;
    const file     = input.files[0];
    const formData = new FormData();
    formData.append('file', file);
    input.value = ''; // reset so same file can be re-imported if needed
    try {
      showToast('Importing…', 5000);
      const res  = await fetch('/api/import', { method: 'POST', body: formData });
      const data = await res.json();
      if (!res.ok) { showToast(data.error || 'Import failed.'); return; }
      const parts = [];
      if (data.purchases) parts.push(data.purchases + ' purchase' + (data.purchases !== 1 ? 's' : ''));
      if (data.dividends) parts.push(data.dividends + ' dividend' + (data.dividends !== 1 ? 's' : ''));
      if (data.sells)     parts.push(data.sells     + ' sell'     + (data.sells     !== 1 ? 's' : ''));
      if (parts.length === 0 && data.errors.length === 0) {
        showToast('Nothing imported — file may be empty or already up to date.');
      } else {
        showToast('Imported: ' + (parts.join(', ') || 'nothing') + (data.errors.length ? ` (${data.errors.length} row error${data.errors.length !== 1 ? 's' : ''})` : '') + '.', 6000);
      }
      if (data.errors.length) console.warn('Import row errors:', data.errors);
      await Promise.all([fetchPortfolio(), fetchDividends(), fetchSells()]);
    } catch (err) {
      showToast('Import failed — ' + err.message);
    }
  }

  // ── Confirm dialog ────────────────────────────────────────────────────────

  let _confirmResolve = null;

  function showConfirm(msg, okLabel = 'Confirm') {
    return new Promise(resolve => {
      _confirmResolve = resolve;
      document.getElementById('confirm-msg').textContent = msg;
      document.getElementById('confirm-ok-btn').textContent = okLabel;
      const overlay = document.getElementById('confirm-overlay');
      overlay.style.display = 'flex';
    });
  }

  function resolveConfirm(result) {
    document.getElementById('confirm-overlay').style.display = 'none';
    if (_confirmResolve) { _confirmResolve(result); _confirmResolve = null; }
  }

  // ── Feedback ──────────────────────────────────────────────────────────────

  const FEEDBACK_EMAIL = '';   // add recipient email here when ready

  function openFeedback() {
    document.getElementById('feedback-msg').value = '';
    document.querySelectorAll('.feedback-type-btn').forEach((b, i) =>
      b.classList.toggle('active', i === 0));
    document.getElementById('feedback-modal').style.display = 'flex';
  }

  function closeFeedback() {
    document.getElementById('feedback-modal').style.display = 'none';
  }

  function setFeedbackType(btn) {
    document.querySelectorAll('.feedback-type-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  }

  function submitFeedback() {
    const type = document.querySelector('.feedback-type-btn.active')?.dataset.type || 'General';
    const msg  = document.getElementById('feedback-msg').value.trim();
    if (!msg) { showToast('Please enter a message before sending.'); return; }
    const subject = encodeURIComponent(`[Investment Tracker] ${type}`);
    const body    = encodeURIComponent(msg);
    const to      = encodeURIComponent(FEEDBACK_EMAIL);
    window.location.href = `mailto:${to}?subject=${subject}&body=${body}`;
    closeFeedback();
  }

  // ── Ticker validation ─────────────────────────────────────────────────────

  const _tickerTimers = {};

  function scheduleTickerValidation(prefix) {
    clearTimeout(_tickerTimers[prefix]);
    _tickerValid[prefix] = null;
    const ticker = document.getElementById(prefix + '-ticker').value.trim();
    const hint   = document.getElementById(prefix + '-ticker-hint');
    if (!ticker) { hint.innerHTML = ''; return; }
    hint.innerHTML = '<span style="color:#94a3b8">Checking…</span>';
    _tickerTimers[prefix] = setTimeout(() => validateTicker(prefix), 700);
  }

  async function validateTicker(prefix) {
    const ticker = document.getElementById(prefix + '-ticker').value.trim();
    const market = document.getElementById(prefix + '-market').value;
    const hint   = document.getElementById(prefix + '-ticker-hint');
    if (!ticker) { hint.innerHTML = ''; return; }

    // Warn if the ticker's suffix belongs to a different market
    let mismatchWarning = '';
    if (appSettings?.markets) {
      const upper = ticker.toUpperCase();
      for (const m of appSettings.markets) {
        if (!m.suffix || m.name === market) continue;
        if (upper.endsWith(m.suffix.toUpperCase())) {
          mismatchWarning = `<span style="color:#f59e0b">&#9888; Looks like a ${esc(m.name)} ticker — is the market correct?</span>`;
          break;
        }
      }
    }

    try {
      const res  = await fetch(`/api/validate-ticker?ticker=${encodeURIComponent(ticker)}&market=${encodeURIComponent(market)}`);
      const data = await res.json();
      let validMsg;
      if (data.valid) {
        _tickerValid[prefix] = true;
        const label = data.name ? esc(data.name) : 'Valid ticker';
        validMsg = `<span style="color:#16a34a">&#10003; ${label}</span>`;
      } else {
        _tickerValid[prefix] = false;
        validMsg = `<span style="color:#ef4444">&#10007; Not found on ${esc(market)}</span>`;
      }
      hint.innerHTML = mismatchWarning ? `${validMsg} &nbsp; ${mismatchWarning}` : validMsg;
    } catch {
      _tickerValid[prefix] = null;
      hint.innerHTML = mismatchWarning || '<span style="color:#94a3b8">Could not validate</span>';
    }
  }

  function clearTickerHint(prefix) {
    const hint = document.getElementById(prefix + '-ticker-hint');
    if (hint) hint.innerHTML = '';
  }

  // ── Settings ──────────────────────────────────────────────────────────────

  async function fetchSettings() {
    try {
      const res  = await fetch('/api/settings');
      const data = await res.json();
      appSettings = data;
      // Update runtime currency globals from settings
      if (data.currency_symbols) CURR_SYM = data.currency_symbols;
      if (data.base_currency)    BASE_CCY  = data.base_currency;
      if (data.markets) {
        MARKET_CCY = {};
        data.markets.forEach(m => { MARKET_CCY[m.name] = m.currency; });
      }
      populateMarketDropdowns();
    } catch (e) {
      console.error('Failed to load settings', e);
    }
  }

  function populateMarketDropdowns() {
    const markets = appSettings ? appSettings.markets : [];
    ['f-market', 's-market', 'd-market'].forEach(id => {
      const sel = document.getElementById(id);
      if (!sel) return;
      const prev = sel.value;
      sel.innerHTML = markets.map(m =>
        `<option value="${m.name}">${m.name}</option>`
      ).join('');
      // Restore previous selection if it still exists, otherwise use first
      if (markets.some(m => m.name === prev)) sel.value = prev;
    });
    // Update price labels after repopulating
    updatePurchasePriceLabel();
    updateSellPriceLabel();
    updateDivAmountLabel();
  }

  function renderSettingsPage() {
    if (!appSettings) return;
    // Sync dark mode toggle state
    const toggle = document.getElementById('dark-mode-toggle');
    if (toggle) toggle.checked = document.body.classList.contains('dark');
    // Base currency dropdown
    const ccySel = document.getElementById('settings-base-ccy');
    const supported = appSettings.supported_currencies || Object.keys(CURR_SYM);
    ccySel.innerHTML = supported.map(c =>
      `<option value="${c}" ${c === BASE_CCY ? 'selected' : ''}>${c} (${CURR_SYM[c] || c})</option>`
    ).join('');
    // Markets table
    const tbody = document.getElementById('settings-markets-body');
    tbody.innerHTML = '';
    (appSettings.markets || []).forEach((m, i) => {
      tbody.insertAdjacentHTML('beforeend', settingsMarketRowHTML(m, i));
    });
    document.getElementById('settings-status').style.display = 'none';
    const benchEl = document.getElementById('settings-benchmark');
    if (benchEl) benchEl.value = appSettings?.benchmark_ticker || 'SPY';
  }

  function settingsMarketRowHTML(m, i) {
    const ccyOpts = (appSettings.supported_currencies || []).map(c =>
      `<option value="${c}" ${c === m.currency ? 'selected' : ''}>${c}</option>`
    ).join('');
    return `<tr data-mrow="${i}" style="border-bottom:1px solid #f1f5f9">
      <td style="padding:.45rem .6rem"><input type="text" value="${esc(m.name)}"
        style="width:100%;padding:.35rem .5rem;border:1px solid #e2e8f0;border-radius:.35rem;font-size:.85rem"
        oninput="settingsMarketChanged()" /></td>
      <td style="padding:.45rem .6rem"><select onchange="settingsMarketChanged()"
        style="padding:.35rem .5rem;border:1px solid #e2e8f0;border-radius:.35rem;font-size:.85rem">${ccyOpts}</select></td>
      <td style="padding:.45rem .6rem"><input type="text" value="${esc(m.suffix || '')}" placeholder="e.g. .SI"
        style="width:100%;padding:.35rem .5rem;border:1px solid #e2e8f0;border-radius:.35rem;font-size:.85rem"
        oninput="settingsMarketChanged()" /></td>
      <td style="padding:.45rem .6rem;text-align:center">
        <button onclick="removeSettingsMarketRow(${i})" title="Remove"
          style="background:none;border:none;color:#ef4444;cursor:pointer;font-size:1rem;line-height:1">×</button>
      </td>
    </tr>`;
  }

  function settingsMarketChanged() {
    document.getElementById('settings-status').style.display = 'none';
  }

  function addSettingsMarketRow() {
    if (!appSettings) return;
    appSettings.markets.push({ name: '', currency: 'USD', suffix: '' });
    renderSettingsPage();
  }

  function removeSettingsMarketRow(i) {
    if (!appSettings || appSettings.markets.length <= 1) {
      showToast('At least one market is required.');
      return;
    }
    appSettings.markets.splice(i, 1);
    renderSettingsPage();
  }

  async function rebuildPerformanceHistory() {
    if (!await showConfirm('This will clear all performance history and update it from your purchase records. Continue?', 'Update')) return;
    showToast('Updating performance history…', 8000);
    try {
      const res  = await fetch('/api/snapshots/reset', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed');
      await fetchSnapshots();
      showToast(`Done — updated ${data.added} monthly snapshot${data.added !== 1 ? 's' : ''}.`, 4000);
    } catch (err) {
      showToast('Update failed — ' + err.message);
    }
  }

  async function saveSettings() {
    const base_currency = document.getElementById('settings-base-ccy').value;
    const rows = document.querySelectorAll('#settings-markets-body tr[data-mrow]');
    const markets = [];
    for (const row of rows) {
      const inputs = row.querySelectorAll('input, select');
      const name     = inputs[0].value.trim();
      const currency = inputs[1].value;
      const suffix   = inputs[2].value.trim();
      if (!name) { showToast('Market name cannot be empty.'); return; }
      markets.push({ name, currency, suffix });
    }
    try {
      const res = await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_currency, markets, benchmark_ticker: document.getElementById('settings-benchmark')?.value.trim().toUpperCase() || 'SPY' }),
      });
      const data = await res.json();
      if (!res.ok) { showToast(data.error || 'Save failed.'); return; }
      // Re-fetch settings to sync all globals and dropdowns
      await fetchSettings();
      // Re-fetch data that depends on base currency
      await Promise.all([fetchPortfolio(), fetchDividends(), fetchSells()]);
      document.getElementById('settings-status').style.display = '';
      setTimeout(() => {
        const el = document.getElementById('settings-status');
        if (el) el.style.display = 'none';
      }, 3000);
    } catch {
      showToast('Failed to save settings.');
    }
  }

  // ── Winners / Losers breakdown ────────────────────────────────────────────

  function renderWinnersLosers() {
    const card = document.getElementById('wl-card');
    if (!lastHoldings || !lastHoldings.length) { if (card) card.style.display = 'none'; return; }

    const withGain = lastHoldings.filter(h => h.gain_loss_pct !== null && h.gain_loss_pct !== undefined);
    if (!withGain.length) { if (card) card.style.display = 'none'; return; }

    const sorted  = [...withGain].sort((a, b) => b.gain_loss_pct - a.gain_loss_pct);
    const winners = sorted.filter(h => h.gain_loss_pct > 0).slice(0, 5);
    const losers  = sorted.filter(h => h.gain_loss_pct < 0).reverse().slice(0, 5);

    const mkRows = items => items.map((h, i) => `
      <tr class="wl-row">
        <td class="wl-rank">${i + 1}</td>
        <td class="wl-ticker">
          <span class="wl-sym">${esc(h.ticker)}</span>
          <span class="wl-name">${esc(h.company_name || '')}</span>
        </td>
        <td class="wl-pct" style="color:${h.gain_loss_pct >= 0 ? '#16a34a' : '#ef4444'}">
          ${h.gain_loss_pct >= 0 ? '+' : ''}${fmt(h.gain_loss_pct, 2)}%
        </td>
        <td class="wl-amt" style="color:${h.gain_loss_pct >= 0 ? '#16a34a' : '#ef4444'}">
          ${h.gain_loss_pct >= 0 ? '+' : ''}${h.currency || ''}${fmt(Math.abs(h.gain_loss_amount || 0), 2)}
        </td>
      </tr>`).join('');

    document.getElementById('wl-winners').innerHTML = `
      <div class="wl-label wl-label-green">▲ Top Gainers</div>
      ${winners.length
        ? `<table class="wl-table"><tbody>${mkRows(winners)}</tbody></table>`
        : '<p class="wl-empty">No gains yet.</p>'}`;

    document.getElementById('wl-losers').innerHTML = `
      <div class="wl-label wl-label-red">▼ Top Losses</div>
      ${losers.length
        ? `<table class="wl-table"><tbody>${mkRows(losers)}</tbody></table>`
        : '<p class="wl-empty">All positions in profit!</p>'}`;

    if (card) card.style.display = '';
  }

  // ── Ticker autocomplete ───────────────────────────────────────────────────

  // Maps Yahoo Finance exchange codes to the market names used in this app.
  const _YF_EXCHANGE_MARKET = {
    NMS: 'US', NYQ: 'US', NGM: 'US', NCM: 'US', PCX: 'US',
    ASE: 'US', BTS: 'US', PNK: 'US',
    SES: 'SGX',
    LSE: 'World', IOB: 'World',
  };

  function setupTickerAutocomplete(prefix) {
    const input     = document.getElementById(prefix + '-ticker');
    const marketSel = document.getElementById(prefix + '-market');
    if (!input) return;

    const dropdown = document.createElement('div');
    dropdown.className = 'ticker-ac-dropdown';
    dropdown.style.display = 'none';
    document.body.appendChild(dropdown);

    let acTimer = null;

    function positionDropdown() {
      const r = input.getBoundingClientRect();
      dropdown.style.top   = (r.bottom + window.scrollY + 2) + 'px';
      dropdown.style.left  = r.left + 'px';
      dropdown.style.width = Math.max(r.width, 260) + 'px';
    }

    input.addEventListener('input', () => {
      clearTimeout(acTimer);
      const q = input.value.trim();
      if (q.length < 2) { dropdown.style.display = 'none'; return; }
      acTimer = setTimeout(async () => {
        try {
          const res   = await fetch('/api/search-ticker?q=' + encodeURIComponent(q));
          const items = await res.json();
          if (!items.length) { dropdown.style.display = 'none'; return; }
          dropdown.innerHTML = items.map(it =>
            `<div class="ticker-ac-item" data-symbol="${esc(it.symbol)}" data-exchange="${esc(it.exchange)}">
               <span class="ticker-ac-symbol">${esc(it.symbol)}</span>
               <span class="ticker-ac-name">${esc(it.name)}</span>
             </div>`
          ).join('');
          dropdown.querySelectorAll('.ticker-ac-item').forEach(item => {
            item.addEventListener('mousedown', e => {
              e.preventDefault();
              input.value = item.dataset.symbol;
              const market = _YF_EXCHANGE_MARKET[item.dataset.exchange];
              if (market && marketSel) {
                const opt = [...marketSel.options].find(o => o.value === market);
                if (opt) {
                  marketSel.value = market;
                  marketSel.dispatchEvent(new Event('change'));
                }
              }
              dropdown.style.display = 'none';
              scheduleTickerValidation(prefix);
            });
          });
          positionDropdown();
          dropdown.style.display = 'block';
        } catch { dropdown.style.display = 'none'; }
      }, 300);
    });

    input.addEventListener('blur',  () => setTimeout(() => { dropdown.style.display = 'none'; }, 150));
    window.addEventListener('scroll', () => { if (dropdown.style.display !== 'none') positionDropdown(); }, true);
  }

  // ── Update notification ───────────────────────────────────────────────────
  async function checkForUpdate() {
    try {
      const res  = await fetch('/api/check-update');
      const data = await res.json();
      if (data.available) {
        _showUpdateBanner(data);
      } else if (!data.checked) {
        // Background check still running — retry once after 4 s
        setTimeout(async () => {
          try {
            const r2   = await fetch('/api/check-update');
            const d2   = await r2.json();
            if (d2.available) _showUpdateBanner(d2);
          } catch { /* ignore */ }
        }, 4000);
      }
    } catch { /* silently ignore — app works fine without update info */ }
  }

  function _showUpdateBanner(data) {
    const banner = document.getElementById('update-banner');
    const text   = document.getElementById('update-banner-text');
    const link   = document.getElementById('update-banner-link');
    text.textContent = `Version ${data.version} is available — `;
    link.href        = data.download_url || '#';
    banner.style.display = '';
  }

  function dismissUpdate() {
    document.getElementById('update-banner').style.display = 'none';
  }

  // ── Theme ─────────────────────────────────────────────────────────────────

  function applyTheme(dark) {
    document.body.classList.toggle('dark', dark);
    const toggle = document.getElementById('dark-mode-toggle');
    if (toggle) toggle.checked = dark;
    localStorage.setItem('theme', dark ? 'dark' : 'light');
  }

  function toggleDarkMode(dark) {
    applyTheme(dark);
  }

  // ── Boot ──────────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', async () => {
    // Apply saved theme before anything renders to avoid flash
    applyTheme(localStorage.getItem('theme') === 'dark');
    document.getElementById('f-date').value = new Date().toISOString().slice(0, 10);
    document.getElementById('d-date').value = new Date().toISOString().slice(0, 10);
    document.getElementById('s-date').value = new Date().toISOString().slice(0, 10);
    setupTickerAutocomplete('f');
    setupTickerAutocomplete('s');
    setupTickerAutocomplete('d');
    await fetchSettings();   // must complete before data fetches so market dropdowns are ready
    fetchPortfolio();
    fetchDividends();
    fetchSells();
    fetchSnapshots();
    setInterval(() => { fetchPortfolio(); fetchSnapshots(); }, 60_000);
    checkForUpdate();

    if (typeof ResizeObserver !== 'undefined') {
      const equityWrap = document.getElementById('equity-wrap');
      if (equityWrap) {
        let _equityResizeTimer = null;
        new ResizeObserver(() => {
          clearTimeout(_equityResizeTimer);
          _equityResizeTimer = setTimeout(() => {
            if (snapData.length) renderEquityChart(snapData);
          }, 80);
        }).observe(equityWrap);
      }
      const heatCanvas = document.getElementById('heatmap-canvas');
      if (heatCanvas) {
        let _heatResizeTimer = null;
        new ResizeObserver(() => {
          clearTimeout(_heatResizeTimer);
          _heatResizeTimer = setTimeout(() => {
            if (lastHoldings.length) renderHeatmap(lastHoldings);
          }, 80);
        }).observe(heatCanvas);
      }
    }
  });
