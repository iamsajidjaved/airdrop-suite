// Airdrop admin UI controller.
// All admin write endpoints accept a shared-secret X-Admin-Token header
// (saved to localStorage). Reads are public.

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
    return `<a href="${EXPLORER_TX_BASE}/${escapeHtml(hash)}" target="_blank" rel="noopener" style="color:var(--accent-primary); text-decoration:none; font-family:monospace; font-size:12px;">${short}</a>`;
}

function flash(el, msg, isError = false) {
    el.textContent = msg;
    el.style.color = isError ? 'var(--accent-danger)' : 'var(--accent-success)';
    setTimeout(() => { el.textContent = ''; }, 3000);
}

function debounce(fn, delay) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), delay); };
}

// ---- pagination state ----
let SENDS_OFFSET = 0;
const SENDS_LIMIT = 50;
let SENDS_STATUS_FILTER = '';
let SENDS_ADDRESS_FILTER = '';

// ---- load tokens into settings form ----
async function loadTokens() {
    try {
        const tokens = await api('GET', '/tokens', null, AIRDROP_API);
        const sel = $('cfgToken');
        const current = sel.value;
        // Keep the placeholder option
        while (sel.options.length > 1) sel.remove(1);
        (tokens || []).forEach(t => {
            const opt = document.createElement('option');
            opt.value = t.id;
            opt.textContent = `${t.symbol} (${t.network})`;
            sel.appendChild(opt);
        });
        if (current) sel.value = current;
    } catch (e) {
        console.warn('loadTokens:', e);
    }
}

// ---- load wallets count for KPI ----
async function loadWalletsKpi() {
    try {
        const wallets = await api('GET', '/wallets');
        const active = (wallets || []).filter(w => w.is_active).length;
        $('kpiWallets').textContent = `${active} / ${(wallets || []).length}`;
    } catch (e) {
        $('kpiWallets').textContent = '—';
    }
}

// ---- config ----
async function loadConfig() {
    try {
        const cfg = await api('GET', '/config');
        // Populate form
        const tokenSel = $('cfgToken');
        if (cfg.distribution_token_id) {
            tokenSel.value = String(cfg.distribution_token_id);
        }
        $('cfgAmount').value = cfg.distribution_amount || '1.0';
        const modeRadio = document.querySelector(`input[name="cfgMode"][value="${cfg.distribution_mode || 'all'}"]`);
        if (modeRadio) modeRadio.checked = true;
        $('cfgInterval').value = cfg.distribution_interval_seconds || 10;
        $('cfgRetries').value = cfg.distribution_max_retries || 3;

        // Update explorer base if needed
        if (cfg.eth_rpc_configured) {
            // Config doesn't expose network key directly, keep default
        }
    } catch (e) {
        console.warn('loadConfig:', e);
    }
}

async function saveConfig(e) {
    e.preventDefault();
    const statusEl = $('settingsStatus');
    statusEl.textContent = 'Saving…';
    statusEl.style.color = 'var(--text-muted)';
    try {
        const tokenId = Number($('cfgToken').value) || null;
        const payload = {
            distribution_mode: document.querySelector('input[name="cfgMode"]:checked').value,
            distribution_amount: $('cfgAmount').value,
            distribution_interval_seconds: Number($('cfgInterval').value) || 10,
            distribution_max_retries: Number($('cfgRetries').value) || 3,
        };
        if (tokenId) payload.distribution_token_id = tokenId;
        await api('PUT', '/config', payload);
        flash(statusEl, 'Saved');
    } catch (e) {
        flash(statusEl, `Error: ${e.message}`, true);
    }
}

// ---- worker ----
async function refreshWorker() {
    try {
        const s = await api('GET', '/worker');
        const kvEl = $('workerKv');
        kvEl.innerHTML = `
            <div class="kv-row"><span class="kv-key">Status</span><span class="kv-val">${s.running ? '<span class="dist-pill running">Running</span>' : '<span class="dist-pill paused">Stopped</span>'}</span></div>
            <div class="kv-row"><span class="kv-key">Mode</span><span class="kv-val">${escapeHtml(s.mode || '—')}</span></div>
            <div class="kv-row"><span class="kv-key">Interval</span><span class="kv-val">${s.interval_seconds ? s.interval_seconds + 's' : '—'}</span></div>
            <div class="kv-row"><span class="kv-key">Sent</span><span class="kv-val">${s.sent_total ?? 0}</span></div>
            <div class="kv-row"><span class="kv-key">In Flight</span><span class="kv-val">${s.in_flight ?? 0}</span></div>
            <div class="kv-row"><span class="kv-key">Last Tick</span><span class="kv-val">${s.last_tick_at ? new Date(s.last_tick_at).toLocaleTimeString() : '—'}</span></div>
        `;

        // Update KPI tiles
        $('kpiPending').textContent = s.pending_sends ?? 0;
        $('kpiConfirmed').textContent = s.confirmed_total ?? 0;
        $('kpiFailed').textContent = s.failed_total ?? 0;

        const errEl = $('workerError');
        if (s.last_error) {
            errEl.textContent = s.last_error;
            errEl.style.display = 'block';
        } else {
            errEl.style.display = 'none';
        }
    } catch (e) {
        $('workerKv').innerHTML = `<div style="color:var(--accent-danger); font-size:12px;">Failed to load worker status: ${escapeHtml(e.message)}</div>`;
    }
}

// ---- sends log ----
async function loadSends() {
    const tbodyEl = $('sendsTbody');
    tbodyEl.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted); padding:24px;">Loading…</td></tr>';
    try {
        const qp = new URLSearchParams({
            limit: String(SENDS_LIMIT),
            offset: String(SENDS_OFFSET),
        });
        if (SENDS_STATUS_FILTER) qp.set('status', SENDS_STATUS_FILTER);
        if (SENDS_ADDRESS_FILTER) qp.set('to_address', SENDS_ADDRESS_FILTER);
        const data = await api('GET', `/sends?${qp}`);
        const items = data.items || [];

        if (items.length === 0) {
            tbodyEl.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted); padding:24px;">No sends found.</td></tr>';
        } else {
            tbodyEl.innerHTML = items.map(s => `
                <tr>
                    <td><span class="mono" title="${escapeHtml(s.to_address)}">${escapeHtml(s.to_address)}</span></td>
                    <td><span class="mono" title="${escapeHtml(s.wallet_address || '')}">${shortAddr(s.wallet_address)}</span></td>
                    <td class="mono">${fmtNum(s.amount)} ${escapeHtml(s.token_symbol || '')}</td>
                    <td>${pill(s.status)}</td>
                    <td>${etherscanTx(s.tx_hash)}</td>
                    <td class="mono">${s.attempts}</td>
                    <td style="font-size:11px; color:var(--text-secondary);">${s.created_at ? new Date(s.created_at).toLocaleString() : '—'}</td>
                </tr>
            `).join('');
        }

        // Pagination
        const from = SENDS_OFFSET + 1;
        const to = Math.min(SENDS_OFFSET + SENDS_LIMIT, data.total);
        $('sendsPageInfo').textContent = data.total > 0 ? `${from}–${to} of ${data.total}` : '0 results';
        $('sendsPrev').disabled = SENDS_OFFSET === 0;
        $('sendsNext').disabled = (SENDS_OFFSET + SENDS_LIMIT) >= data.total;
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
        if (statusFilter) body.status = statusFilter; else body.status = 'all';
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
            $('workerError').textContent = e.message;
            $('workerError').style.display = 'block';
        }
    });
    $('workerStopBtn').addEventListener('click', async () => {
        try {
            await api('POST', '/worker/stop');
            await refreshWorker();
        } catch (e) {
            $('workerError').textContent = e.message;
            $('workerError').style.display = 'block';
        }
    });
    $('workerRefreshBtn').addEventListener('click', refreshWorker);

    // Settings form
    $('settingsForm').addEventListener('submit', saveConfig);

    // Sends log controls
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
    $('sendsPrev').addEventListener('click', () => {
        SENDS_OFFSET = Math.max(0, SENDS_OFFSET - SENDS_LIMIT);
        loadSends();
    });
    $('sendsNext').addEventListener('click', () => {
        SENDS_OFFSET += SENDS_LIMIT;
        loadSends();
    });

    // Initial load
    loadTokens().then(() => loadConfig());
    loadWalletsKpi();
    refreshWorker();
    loadSends();

    // Auto-refresh
    setInterval(refreshWorker, 5000);
    setInterval(loadSends, 8000);
});
