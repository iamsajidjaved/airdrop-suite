// Distribution admin UI controller.
// All admin write endpoints accept a shared-secret X-Admin-Token header
// (saved to localStorage). Reads are public.

const API = '/api/distribution';
const TOKEN_KEY = 'wallet_explorer_admin_token';

const $ = (id) => document.getElementById(id);

function getAdminToken() {
    return localStorage.getItem(TOKEN_KEY) || '';
}
function setAdminToken(t) {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else   localStorage.removeItem(TOKEN_KEY);
}

async function api(method, path, body = null) {
    const headers = { 'Content-Type': 'application/json' };
    const token = getAdminToken();
    if (token) headers['X-Admin-Token'] = token;
    const res = await fetch(API + path, {
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
function shortAddr(a) { return a ? `${a.slice(0, 6)}…${a.slice(-4)}` : ''; }
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
// Block explorer base URL — resolved once from /config on page load.
const NETWORK_EXPLORERS = {
    'ethereum': 'https://etherscan.io/tx',
    'sepolia':  'https://sepolia.etherscan.io/tx',
    'holesky':  'https://holesky.etherscan.io/tx',
};
let EXPLORER_TX_BASE = NETWORK_EXPLORERS['ethereum'];

async function loadConfig() {
    try {
        const cfg = await api('GET', '/config');
        if (cfg.network && NETWORK_EXPLORERS[cfg.network]) {
            EXPLORER_TX_BASE = NETWORK_EXPLORERS[cfg.network];
        }
    } catch (_) {}
}

function etherscanTx(hash) {
    return `<a class="tx-link" target="_blank" rel="noopener" href="${EXPLORER_TX_BASE}/${encodeURIComponent(hash)}">${shortAddr(hash)}</a>`;
}

// ---------- Worker / config ----------

async function refreshWorker() {
    try {
        const s = await api('GET', '/worker');
        $('workerError').style.display = 'none';
        const kpiI = $('kpiInFlight'); if (kpiI) kpiI.textContent = s.in_flight ?? 0;
        const cells = [
            ['Status', s.running ? '<span class="dist-pill running">running</span>' : '<span class="dist-pill paused">stopped</span>'],
            ['Interval', `${s.interval_seconds}s`],
            ['In flight', s.in_flight],
            ['Sent (since boot)', s.sent_total],
            ['Confirmed', s.confirmed_total],
            ['Failed', s.failed_total],
            ['Last tick', s.last_tick_at ? new Date(s.last_tick_at).toLocaleTimeString() : '—'],
            ['Last error', s.last_error ? `<span style="color:var(--accent-danger);">${escapeHtml(s.last_error)}</span>` : '—'],
        ];
        $('workerBox').innerHTML = cells.map(([k, v]) =>
            `<div class="status-cell"><div class="status-label">${k}</div><div class="status-value small">${v}</div></div>`
        ).join('');
    } catch (e) {
        $('workerError').textContent = e.message;
        $('workerError').style.display = 'block';
    }
}

async function refreshConfig() {
    // Configuration is now displayed on the Settings page; no-op here.
}

// ---------- Wallets (read-only on this page; managed in Settings) ----------

async function refreshWallets() {
    try {
        const wallets = await api('GET', `/wallets?include_balances=false`);
        WALLETS_CACHE = wallets;
        const kpiW = $('kpiWallets');
        if (kpiW) kpiW.textContent = wallets.filter(w => w.is_active).length;
    } catch (_e) {
        WALLETS_CACHE = [];
    }
}

async function handleWalletAction() { /* removed — see Settings */ }
function openWalletModal() { /* removed */ }
function closeWalletModal() { /* removed */ }
async function submitWallet() { /* removed */ }

// ---------- Campaigns ----------

let TOKENS_CACHE = [];
let WALLETS_CACHE = [];
let CURRENT_CAMPAIGN_ID = null;
let RECIP_OFFSET = 0;
const RECIP_LIMIT = 50;

async function loadTokens() {
    try {
        const tokens = await fetch('/api/airdrop/tokens').then(r => r.json());
        TOKENS_CACHE = tokens;
        const sel = $('campToken');
        sel.innerHTML = tokens.map(t =>
            `<option value="${t.id}">${escapeHtml(t.symbol)} — ${t.contract_address.slice(0, 8)}…</option>`
        ).join('');
    } catch (e) {
        console.warn('token load failed', e);
    }
}

async function refreshCampaigns() {
    const tbody = $('campaignsTbody');
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color: var(--text-muted); padding: 24px;">Loading…</td></tr>`;
    try {
        const campaigns = await api('GET', '/campaigns');
        const kpiAC = $('kpiActiveCampaigns');
        if (kpiAC) kpiAC.textContent = campaigns.filter(c => c.status === 'running').length;
        const kpiC = $('kpiConfirmed');
        if (kpiC) kpiC.textContent = campaigns.reduce((s, c) => s + ((c.counts && c.counts.confirmed) || 0), 0).toLocaleString();
        if (!campaigns.length) {
            tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color: var(--text-muted); padding: 24px;">No campaigns yet.</td></tr>`;
            return;
        }
        tbody.innerHTML = campaigns.map(c => {
            const counts = c.counts || {};
            const total = counts.total || 0;
            const done = (counts.confirmed || 0);
            const pct = total ? Math.round((done / total) * 100) : 0;
            const senderLabel = (c.sender_mode || 'multi') === 'single' ? 'single' : 'multi';
            const senderCount = (c.sender_wallet_ids && c.sender_wallet_ids.length)
                ? `${c.sender_wallet_ids.length} assigned`
                : 'all active';
            const confirmActions = [];
            if (c.status === 'ready' || c.status === 'paused') confirmActions.push(`<button class="row-btn" data-c-action="start" data-id="${c.id}">Start</button>`);
            if (c.status === 'running') confirmActions.push(`<button class="row-btn" data-c-action="pause" data-id="${c.id}">Pause</button>`);
            if (c.status === 'draft') confirmActions.push(`<button class="row-btn" data-c-action="build" data-id="${c.id}">Build recipients</button>`);
            confirmActions.push(`<button class="row-btn" data-c-action="rebuild" data-id="${c.id}">Rebuild</button>`);
            confirmActions.push(`<button class="row-btn" data-c-action="retry"  data-id="${c.id}">Retry failed</button>`);
            if (c.dry_run) confirmActions.push(`<button class="row-btn" data-c-action="undry" data-id="${c.id}">Disable dry‑run</button>`);
            confirmActions.push(`<button class="row-btn" data-c-action="open"  data-id="${c.id}">Open</button>`);

            return `<tr>
                <td class="mono">${c.id}</td>
                <td>${escapeHtml(c.name)}</td>
                <td>${escapeHtml(c.token_symbol || c.token_id)}</td>
                <td class="mono">${fmtNum(c.amount_per_recipient)}</td>
                <td>${pill(c.status)}</td>
                <td>
                    <div style="display:flex; flex-direction:column; gap:2px;">
                        <span class="dist-pill ${senderLabel === 'single' ? 'paused' : 'running'}">${senderLabel}</span>
                        <span style="font-size:10px; color:var(--text-muted);">${senderCount}${c.dry_run ? ' · <span style="color:#fbbf24;">dry‑run</span>' : ''}</span>
                    </div>
                </td>
                <td style="min-width: 160px;">
                    <div class="progress-bar"><div class="fill" style="width:${pct}%"></div></div>
                    <div style="font-size: 11px; color: var(--text-muted); margin-top:4px;">
                        ${done} confirmed / ${total} total · ${counts.failed || 0} failed
                    </div>
                </td>
                <td class="row-actions">${confirmActions.join('')}</td>
            </tr>`;
        }).join('');
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="8" class="admin-error">${escapeHtml(e.message)}</td></tr>`;
    }
}

async function handleCampaignAction(e) {
    const btn = e.target.closest('button[data-c-action]');
    if (!btn) return;
    const id = btn.dataset.id;
    const a = btn.dataset.cAction;
    try {
        if (a === 'start')   await api('POST', `/campaigns/${id}/start`);
        else if (a === 'pause') await api('POST', `/campaigns/${id}/pause`);
        else if (a === 'build' || a === 'rebuild') await api('POST', `/campaigns/${id}/build`);
        else if (a === 'retry') await api('POST', `/campaigns/${id}/retry-failed`);
        else if (a === 'undry') {
            if (!confirm('Disable dry‑run for this campaign? Real transactions will be broadcast when started.')) return;
            await api('PATCH', `/campaigns/${id}`, { dry_run: false });
        } else if (a === 'open') {
            return openCampaignDetail(Number(id));
        }
        await refreshCampaigns();
    } catch (err) {
        alert(err.message);
    }
}

function openCampaignModal() {
    $('campName').value = '';
    $('campAmount').value = '';
    $('campMaxTotal').value = '';
    $('filtToken').value = '';
    $('filtFrom').value = '';
    $('filtTo').value = '';
    $('filtMinUsd').value = '';
    $('filtLimit').value = '';
    $('campDryRun').checked = true;
    document.querySelectorAll('input[name="campSenderMode"]').forEach(r => { r.checked = (r.value === 'multi'); });
    renderCampaignWalletPicker();
    updateAmountHint();
    $('campFormError').style.display = 'none';
    $('campaignModal').style.display = 'flex';
}

function renderCampaignWalletPicker() {
    const box = $('campWallets');
    if (!box) return;
    if (!WALLETS_CACHE.length) {
        box.innerHTML = `<div style="color:var(--text-muted); font-size:12px;">No sender wallets configured. Add one first — the campaign will fall back to all active wallets if none are assigned.</div>`;
        return;
    }
    box.innerHTML = WALLETS_CACHE.map(w => `
        <label class="wallet-pick-item">
            <input type="checkbox" data-wid="${w.id}" ${w.is_active ? '' : 'disabled'}>
            <div>
                <div class="addr">${escapeHtml(w.address)}</div>
                <div style="font-size:11px; color:var(--text-muted);">${escapeHtml(w.label || '')}${w.is_active ? '' : ' · inactive'}</div>
            </div>
        </label>
    `).join('');
}

function selectedSenderWalletIds() {
    return Array.from(document.querySelectorAll('#campWallets input[type=checkbox]:checked'))
        .map(el => Number(el.dataset.wid))
        .filter(n => Number.isFinite(n));
}

function updateAmountHint() {
    const hint = $('campAmountHint');
    if (!hint) return;
    const tokenId = Number($('campToken').value);
    const tok = TOKENS_CACHE.find(t => t.id === tokenId);
    if (!tok) { hint.textContent = ' '; return; }
    const min = Math.pow(10, -tok.decimals);
    hint.innerHTML = `<strong>${escapeHtml(tok.symbol)}</strong> has <strong>${tok.decimals}</strong> decimals · minimum representable unit = <span class="mono">${min.toFixed(tok.decimals)}</span>`;
}
function closeCampaignModal() { $('campaignModal').style.display = 'none'; }

async function submitCampaign(e) {
    e.preventDefault();
    const err = $('campFormError');
    err.style.display = 'none';
    try {
        const filter = {
            token_symbol: $('filtToken').value.trim() || null,
            from_date:    $('filtFrom').value || null,
            to_date:      $('filtTo').value   || null,
            min_amount_usd: $('filtMinUsd').value ? Number($('filtMinUsd').value) : null,
            limit:          $('filtLimit').value  ? Number($('filtLimit').value)  : null,
            exclude_addresses: [],
        };
        Object.keys(filter).forEach(k => filter[k] === null && delete filter[k]);
        const senderModeEl = document.querySelector('input[name="campSenderMode"]:checked');
        const payload = {
            name: $('campName').value.trim(),
            token_id: Number($('campToken').value),
            amount_per_recipient: $('campAmount').value,
            recipient_filter: filter,
            max_total_amount: $('campMaxTotal').value || null,
            dry_run: $('campDryRun').checked,
            sender_mode: senderModeEl ? senderModeEl.value : 'multi',
            sender_wallet_ids: selectedSenderWalletIds(),
        };
        if (!payload.max_total_amount) delete payload.max_total_amount;
        await api('POST', '/campaigns', payload);
        closeCampaignModal();
        await refreshCampaigns();
    } catch (e2) {
        err.textContent = e2.message;
        err.style.display = 'block';
    }
}

// ---------- Campaign detail ----------

async function openCampaignDetail(id) {
    CURRENT_CAMPAIGN_ID = id;
    RECIP_OFFSET = 0;
    $('campaignDetailCard').style.display = 'block';
    try {
        const c = await api('GET', `/campaigns/${id}`);
        $('detailTitle').textContent = `Campaign #${c.id}: ${c.name}`;
        const counts = c.counts || {};
        $('detailSub').innerHTML =
            `${escapeHtml(c.token_symbol || '')} · ${fmtNum(c.amount_per_recipient)} per recipient · ${pill(c.status)} · ${c.dry_run ? '<span class="dist-pill draft">dry‑run</span>' : '<span class="dist-pill ready">live</span>'} · ` +
            `total ${counts.total || 0} (pending ${counts.pending || 0} · sent ${counts.sent || 0} · confirmed ${counts.confirmed || 0} · failed ${counts.failed || 0})`;
        await refreshRecipients();
        $('campaignDetailCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (e) {
        $('detailSub').textContent = e.message;
    }
}

function closeCampaignDetail() {
    CURRENT_CAMPAIGN_ID = null;
    $('campaignDetailCard').style.display = 'none';
}

async function refreshRecipients() {
    if (CURRENT_CAMPAIGN_ID === null) return;
    const tbody = $('recipientsTbody');
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color: var(--text-muted); padding: 24px;">Loading…</td></tr>`;
    try {
        const status = $('detailStatusFilter').value;
        const qp = new URLSearchParams({ limit: String(RECIP_LIMIT), offset: String(RECIP_OFFSET) });
        if (status) qp.set('status', status);
        const data = await api('GET', `/campaigns/${CURRENT_CAMPAIGN_ID}/recipients?${qp}`);
        if (!data.items.length) {
            tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color: var(--text-muted); padding: 24px;">No recipients in this view.</td></tr>`;
        } else {
            tbody.innerHTML = data.items.map(r => `
                <tr>
                    <td class="mono">${r.id}</td>
                    <td><span class="addr">${escapeHtml(r.address)}</span></td>
                    <td class="mono">${fmtNum(r.amount)}</td>
                    <td>${pill(r.status)}</td>
                    <td class="mono">${r.attempts}</td>
                    <td>${r.last_tx_hash ? etherscanTx(r.last_tx_hash) : '—'}</td>
                    <td class="mono" style="max-width: 280px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeHtml(r.last_error || '')}">${escapeHtml(r.last_error || '')}</td>
                </tr>`).join('');
        }
        const from = data.total === 0 ? 0 : RECIP_OFFSET + 1;
        const to = Math.min(RECIP_OFFSET + RECIP_LIMIT, data.total);
        $('recipPageInfo').textContent = `${from}–${to} of ${data.total}`;
        $('recipPrev').disabled = RECIP_OFFSET <= 0;
        $('recipNext').disabled = RECIP_OFFSET + RECIP_LIMIT >= data.total;
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="7" class="admin-error">${escapeHtml(e.message)}</td></tr>`;
    }
}

// ---------- Wire-up ----------

document.addEventListener('DOMContentLoaded', () => {
    $('workerStartBtn').addEventListener('click', async () => {
        try { await api('POST', '/worker/start'); await refreshWorker(); }
        catch (e) { alert(e.message); }
    });
    $('workerStopBtn').addEventListener('click', async () => {
        try { await api('POST', '/worker/stop'); await refreshWorker(); }
        catch (e) { alert(e.message); }
    });
    $('workerRefreshBtn').addEventListener('click', refreshWorker);

    $('addCampaignBtn').addEventListener('click', openCampaignModal);
    $('campCancelBtn').addEventListener('click', closeCampaignModal);
    $('campaignForm').addEventListener('submit', submitCampaign);
    $('campaignsTbody').addEventListener('click', handleCampaignAction);
    $('campToken').addEventListener('change', updateAmountHint);
    const minBtn = $('campAmountMinBtn');
    if (minBtn) minBtn.addEventListener('click', () => {
        const tokenId = Number($('campToken').value);
        const tok = TOKENS_CACHE.find(t => t.id === tokenId);
        if (!tok) { alert('Pick a token first.'); return; }
        $('campAmount').value = Math.pow(10, -tok.decimals).toFixed(tok.decimals);
    });

    $('detailRefreshBtn').addEventListener('click', () => { RECIP_OFFSET = 0; refreshRecipients(); });
    $('detailCloseBtn').addEventListener('click', closeCampaignDetail);
    $('detailStatusFilter').addEventListener('change', () => { RECIP_OFFSET = 0; refreshRecipients(); });
    $('recipPrev').addEventListener('click', () => { RECIP_OFFSET = Math.max(0, RECIP_OFFSET - RECIP_LIMIT); refreshRecipients(); });
    $('recipNext').addEventListener('click', () => { RECIP_OFFSET += RECIP_LIMIT; refreshRecipients(); });

    loadConfig();
    refreshWorker();
    refreshWallets();
    loadTokens().then(refreshCampaigns);

    // Periodic refresh of worker + campaigns while page is open.
    setInterval(refreshWorker, 5000);
    setInterval(refreshCampaigns, 8000);
    setInterval(() => { if (CURRENT_CAMPAIGN_ID !== null) refreshRecipients(); }, 8000);
});
