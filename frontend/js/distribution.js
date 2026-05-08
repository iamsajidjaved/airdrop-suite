// Airdrop admin UI — worker monitor + dispatch history.
// Settings (token, amount, mode, interval) live in /admin/settings#airdrop.

const API = '/api/distribution';
const AIRDROP_API = '/api/airdrop';
const TOKEN_KEY = 'wallet_explorer_admin_token';

const $ = (id) => document.getElementById(id);

function getAdminToken() {
    return localStorage.getItem(TOKEN_KEY) || '';
}

async function api(method, path, body = null, baseApi = API) {
    const headers = { 'Content-Type': 'application/json' };
    const token = getAdminToken();
    if (token) headers['X-Admin-Token'] = token;
    const res = await fetch(baseApi + path, {
        method,
        headers,
        body: body !== null ? JSON.stringify(body) : undefined,
    });
    if (res.status === 204) return null;
    let data;
    try { data = await res.json(); } catch (_) { data = null; }
    if (!res.ok) {
        const detail = (data && (data.detail || data.error)) || res.statusText;
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return data;
}

function escapeHtml(s) {
    return String(s ?? '')
        .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}
function shortAddr(a) { return a ? `${a.slice(0, 6)}…${a.slice(-4)}` : '—'; }
function fmtNum(v, dp = 6) {
    if (v === null || v === undefined || v === '') return '—';
    const n = Number(v);
    if (!isFinite(n)) return String(v);
    if (n === 0) return '0';
    if (Math.abs(n) < 1) return n.toFixed(Math.min(dp, 6));
    return n.toLocaleString(undefined, { maximumFractionDigits: dp });
}
function pill(status) {
    return `<span class="dist-pill ${escapeHtml(status)}">${escapeHtml(status)}</span>`;
}

const NETWORK_EXPLORERS = {
    ethereum: 'https://etherscan.io/tx',
    sepolia:  'https://sepolia.etherscan.io/tx',
    holesky:  'https://holesky.etherscan.io/tx',
};
let EXPLORER_TX_BASE = NETWORK_EXPLORERS.ethereum;

function etherscanTx(hash) {
    if (!hash) return '—';
    const short = `${hash.slice(0, 10)}…`;
    return `<a href="${EXPLORER_TX_BASE}/${escapeHtml(hash)}" target="_blank" rel="noopener" style="color:var(--accent-primary); text-decoration:none; font-family:monospace; font-size:11px;">${short}</a>`;
}

function debounce(fn, delay) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), delay); };
}

// ---- pagination + filter state ----
let SENDS_OFFSET = 0;
let SENDS_PAGE_SIZE = 50;
let SENDS_STATUS_FILTER = '';
let SENDS_ADDRESS_FILTER = '';

// ---- sort state ----
let SORT_COL = 'created_at';
let SORT_DIR = 'desc';

// ---- KPI ----
async function loadWalletsKpi() {
    try {
        const wallets = await api('GET', '/wallets');
        const active = (wallets || []).filter(w => w.is_active).length;
        $('kpiWallets').textContent = `${active} / ${(wallets || []).length}`;
    } catch (e) {
        $('kpiWallets').textContent = '—';
    }
}

// ---- worker ----
async function refreshWorker() {
    const errEl = $('workerError');
    try {
        const s = await api('GET', '/worker');

        const badge = $('workerStatusBadge');
        const statusText = $('workerStatusText');
        if (s.running) {
            badge.className = 'worker-badge worker-badge-running';
            statusText.textContent = 'Running';
        } else {
            badge.className = 'worker-badge worker-badge-stopped';
            statusText.textContent = 'Stopped';
        }

        const modeLabel = s.mode === 'all' ? 'All Wallets' : s.mode === 'random' ? 'Random' : (s.mode || '—');
        $('wMode').textContent = modeLabel;
        $('wInterval').textContent = s.interval_seconds ? `${s.interval_seconds}s` : '—';
        $('wSent').textContent = String(s.sent_total ?? 0);
        $('wInFlight').textContent = String(s.in_flight ?? 0);
        $('wLastTick').textContent = s.last_tick_at ? new Date(s.last_tick_at).toLocaleTimeString() : '—';

        $('kpiPending').textContent = s.pending_sends ?? 0;
        $('kpiConfirmed').textContent = s.confirmed_total ?? 0;
        $('kpiFailed').textContent = s.failed_total ?? 0;

        if (s.last_error) {
            errEl.textContent = s.last_error;
            errEl.style.display = 'block';
        } else {
            errEl.style.display = 'none';
        }
    } catch (e) {
        errEl.textContent = `Failed to load worker status: ${e.message}`;
        errEl.style.display = 'block';
    }
}

// ---- client-side sort on current page ----
function sortData(items) {
    if (!SORT_COL) return items;
    return [...items].sort((a, b) => {
        let av = a[SORT_COL] ?? '';
        let bv = b[SORT_COL] ?? '';
        let cmp;
        if (typeof av === 'number' && typeof bv === 'number') {
            cmp = av - bv;
        } else if (SORT_COL === 'created_at') {
            cmp = new Date(av) - new Date(bv);
        } else {
            cmp = String(av).localeCompare(String(bv));
        }
        return SORT_DIR === 'asc' ? cmp : -cmp;
    });
}

function updateSortHeaders() {
    document.querySelectorAll('#sendsTable th.sortable').forEach(th => {
        th.classList.remove('sort-active', 'sort-asc', 'sort-desc');
        if (th.dataset.col === SORT_COL) {
            th.classList.add('sort-active', SORT_DIR === 'asc' ? 'sort-asc' : 'sort-desc');
        }
    });
}

// ---- dispatch history ----
async function loadSends() {
    const tbodyEl = $('sendsTbody');
    tbodyEl.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted); padding:24px;">Loading…</td></tr>';
    try {
        const qp = new URLSearchParams({
            limit: String(SENDS_PAGE_SIZE),
            offset: String(SENDS_OFFSET),
        });
        if (SENDS_STATUS_FILTER) qp.set('status', SENDS_STATUS_FILTER);
        if (SENDS_ADDRESS_FILTER) qp.set('to_address', SENDS_ADDRESS_FILTER);
        const data = await api('GET', `/sends?${qp}`);
        const items = sortData(data.items || []);
        const total = data.total;

        if (items.length === 0) {
            tbodyEl.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted); padding:24px;">No sends found.</td></tr>';
        } else {
            tbodyEl.innerHTML = items.map(s => `
                <tr>
                    <td><span class="addr" title="${escapeHtml(s.to_address)}">${escapeHtml(s.to_address)}</span></td>
                    <td><span class="mono" title="${escapeHtml(s.wallet_address || '')}">${shortAddr(s.wallet_address)}</span></td>
                    <td class="mono">${fmtNum(s.amount)} <span style="color:var(--text-muted);">${escapeHtml(s.token_symbol || '')}</span></td>
                    <td>${pill(s.status)}</td>
                    <td>${etherscanTx(s.tx_hash)}</td>
                    <td class="mono" style="text-align:center;">${s.attempts}</td>
                    <td style="font-size:11px; color:var(--text-secondary); white-space:nowrap;">${s.created_at ? new Date(s.created_at).toLocaleString() : '—'}</td>
                </tr>
            `).join('');
        }

        const from = total > 0 ? SENDS_OFFSET + 1 : 0;
        const to = Math.min(SENDS_OFFSET + SENDS_PAGE_SIZE, total);
        $('sendsPageInfo').textContent = total > 0 ? `${from}–${to}` : '0';
        $('dtInfo').textContent = total > 0
            ? `Showing ${from}–${to} of ${total.toLocaleString()} records`
            : 'No records found';
        $('sendsPrev').disabled = SENDS_OFFSET === 0;
        $('sendsNext').disabled = (SENDS_OFFSET + SENDS_PAGE_SIZE) >= total;
    } catch (e) {
        tbodyEl.innerHTML = `<tr><td colspan="7" style="text-align:center; color:var(--accent-danger); padding:24px;">${escapeHtml(e.message)}</td></tr>`;
    }
}

async function resetSends() {
    const statusFilter = $('sendsStatusFilter').value;
    const addressFilter = $('sendsAddressFilter').value.trim();
    const scopeDesc = addressFilter
        ? `sends for address ${addressFilter}${statusFilter ? ` with status "${statusFilter}"` : ''}`
        : statusFilter
            ? `all "${statusFilter}" sends`
            : 'ALL sends (except in-flight broadcasts)';

    if (!confirm(`This will delete ${scopeDesc} so they will be re-queued on the next worker tick.\n\nProceed?`)) return;

    try {
        const body = {};
        if (addressFilter) body.address = addressFilter;
        body.status = statusFilter || 'all';
        const result = await api('POST', '/sends/reset', body);
        alert(`Deleted ${result.deleted} send record(s).`);
        SENDS_OFFSET = 0;
        await loadSends();
        await refreshWorker();
    } catch (e) {
        alert(`Reset failed: ${e.message}`);
    }
}

// ---- init ----
document.addEventListener('DOMContentLoaded', () => {
    // Worker controls
    $('workerStartBtn').addEventListener('click', async () => {
        try {
            await api('POST', '/worker/start');
            await refreshWorker();
        } catch (e) {
            const errEl = $('workerError');
            errEl.textContent = e.message;
            errEl.style.display = 'block';
        }
    });
    $('workerStopBtn').addEventListener('click', async () => {
        try {
            await api('POST', '/worker/stop');
            await refreshWorker();
        } catch (e) {
            const errEl = $('workerError');
            errEl.textContent = e.message;
            errEl.style.display = 'block';
        }
    });
    $('workerRefreshBtn').addEventListener('click', refreshWorker);

    // Table controls
    $('sendsRefreshBtn').addEventListener('click', () => { SENDS_OFFSET = 0; loadSends(); });
    $('sendsStatusFilter').addEventListener('change', (e) => {
        SENDS_STATUS_FILTER = e.target.value;
        SENDS_OFFSET = 0;
        loadSends();
    });
    $('sendsAddressFilter').addEventListener('input', debounce((e) => {
        SENDS_ADDRESS_FILTER = e.target.value.trim();
        SENDS_OFFSET = 0;
        loadSends();
    }, 400));
    $('sendsResetBtn').addEventListener('click', resetSends);

    // Page size
    $('sendsPageSize').addEventListener('change', (e) => {
        SENDS_PAGE_SIZE = Number(e.target.value) || 50;
        SENDS_OFFSET = 0;
        loadSends();
    });

    // Pagination
    $('sendsPrev').addEventListener('click', () => {
        SENDS_OFFSET = Math.max(0, SENDS_OFFSET - SENDS_PAGE_SIZE);
        loadSends();
    });
    $('sendsNext').addEventListener('click', () => {
        SENDS_OFFSET += SENDS_PAGE_SIZE;
        loadSends();
    });

    // Sort headers
    document.querySelectorAll('#sendsTable th.sortable').forEach(th => {
        th.addEventListener('click', () => {
            const col = th.dataset.col;
            if (SORT_COL === col) {
                SORT_DIR = SORT_DIR === 'asc' ? 'desc' : 'asc';
            } else {
                SORT_COL = col;
                SORT_DIR = col === 'created_at' ? 'desc' : 'asc';
            }
            updateSortHeaders();
            loadSends();
        });
    });

    updateSortHeaders();

    // Initial data load
    loadWalletsKpi();
    refreshWorker();
    loadSends();
});
