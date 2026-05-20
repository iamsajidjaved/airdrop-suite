/* Scanner — KPI strip, status, qualifying-transactions table.
 *
 * Network is now a global DB-backed setting (active_network in airdrop_config).
 * Changing it here saves to the API and takes effect on the next scan.
 * iGaming brand management has moved to /admin/settings#brands.
 */
(() => {
    const API = "/api/airdrop";

    // -------- helpers --------
    const $ = (id) => document.getElementById(id);
    const fmtAddr = (a) => a ? `${a.slice(0, 8)}…${a.slice(-6)}` : "—";
    const fmtAmount = (s) => {
        const n = Number(s);
        if (!isFinite(n)) return s;
        return n.toLocaleString(undefined, { maximumFractionDigits: 4 });
    };
    const fmtTime = (iso) => {
        if (!iso) return "—";
        try { return new Date(iso).toISOString().replace("T", " ").slice(0, 19); }
        catch { return iso; }
    };
    const escHtml = (s) => String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");

    async function api(path, options = {}) {
        const res = await fetch(API + path, {
            headers: { "Content-Type": "application/json" },
            ...options,
        });
        if (res.status === 204) return null;
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            const msg = data.detail || res.statusText || "Request failed";
            throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
        }
        return data;
    }

    // -------- state --------
    let tokens = [];
    let networks = [];
    let activeNetwork = (window.GlobalHeader && window.GlobalHeader.activeNetwork) || "ethereum";
    let txOffset = 0;
    const TX_LIMIT = 25;
    let txTotal = 0;

    function explorerFor(net) {
        const n = networks.find(x => x.key === net);
        return n ? n.explorer : "https://etherscan.io";
    }

    // -------- networks (data only — UI is owned by global-header.js) --------
    async function loadNetworks() {
        try { networks = await api("/networks"); }
        catch {
            networks = [
                { key: "ethereum", label: "Ethereum Mainnet", chain_id: 1, explorer: "https://etherscan.io" },
                { key: "sepolia",  label: "Sepolia Testnet",  chain_id: 11155111, explorer: "https://sepolia.etherscan.io" },
            ];
        }
    }

    // -------- config (threshold KPI — network is managed by global-header.js) --------
    async function loadConfig() {
        try {
            const cfg = await api("/config");
            const kpiTh = $("kpiThreshold");
            if (kpiTh) kpiTh.textContent = `$${Number(cfg.min_threshold_usd).toLocaleString()}`;
            // sync local activeNetwork from GlobalHeader (which read it from DB)
            activeNetwork = (window.GlobalHeader && window.GlobalHeader.activeNetwork) || activeNetwork;
        } catch { /* KPI optional */ }
    }

    // -------- status --------
    async function loadStatus() {
        try {
            const s = await api("/status");
            $("statTotal").textContent = s.total_transfers.toLocaleString();
            $("statLastRun").textContent = fmtTime(s.last_run_timestamp);
            const kpiTT = $("kpiTotalTransfers"); if (kpiTT) kpiTT.textContent = s.total_transfers.toLocaleString();
            const kpiLR = $("kpiLastRun"); if (kpiLR) kpiLR.textContent = fmtTime(s.last_run_timestamp);

            // Per-token blocks
            const blocks = s.last_block_per_token || {};
            const tkeys = Object.keys(blocks);
            $("statBlocks").innerHTML = tkeys.length
                ? tkeys.map(k => `<span class="block-chip"><strong>${escHtml(k)}</strong> <span class="mono">${blocks[k].toLocaleString()}</span></span>`).join(" ")
                : "No tokens";

            // Per-brand blocks
            const bblocks = s.last_block_per_brand || {};
            const bkeys = Object.keys(bblocks);
            $("statBrandBlocks").innerHTML = bkeys.length
                ? bkeys.map(k => `<span class="block-chip"><strong>${escHtml(k)}</strong> <span class="mono">${bblocks[k].toLocaleString()}</span></span>`).join(" ")
                : "No brands";

            // Mode breakdown
            const mb = s.scan_mode_breakdown || {};
            $("statModeBreakdown").innerHTML = `
                <span class="block-chip"><strong>Standard</strong> <span class="mono">${(mb.standard || 0).toLocaleString()}</span></span>
                <span class="block-chip" style="background:var(--kpi-violet,#e9d5ff)"><strong>iGaming</strong> <span class="mono">${(mb.igaming || 0).toLocaleString()}</span></span>`;
        } catch (e) {
            $("statBlocks").textContent = e.message;
        }
    }

    // -------- tokens (read-only here, used by KPI + tx filter) --------
    async function loadTokens() {
        try {
            tokens = await api("/tokens");
            renderTokenFilter();
            updateActiveTokensKpi();
        } catch { /* optional */ }
    }

    function updateActiveTokensKpi() {
        const kpi = $("kpiActiveTokens");
        if (kpi) kpi.textContent = tokens.filter(t => t.is_active).length;
    }

    function renderTokenFilter() {
        const sel = $("txTokenFilter");
        const current = sel.value;
        sel.innerHTML = '<option value="">All tokens</option>' +
            tokens.map(t => `<option value="${escHtml(t.symbol)}">${escHtml(t.symbol)}</option>`).join("");
        sel.value = current;
    }

    // -------- transactions --------
    async function loadTx() {
        const tokenSym = $("txTokenFilter").value;
        const modeFilter = $("txModeFilter").value;
        const params = new URLSearchParams({ limit: String(TX_LIMIT), offset: String(txOffset) });
        if (tokenSym) params.set("token", tokenSym);
        if (modeFilter) params.set("scan_mode", modeFilter);
        try {
            const res = await api("/transactions?" + params.toString());
            txTotal = res.total;
            renderTx(res.items);
            renderPagination();
        } catch (e) {
            $("txTbody").innerHTML = `<tr><td colspan="7" style="color:var(--accent-danger);padding:16px;">${escHtml(e.message)}</td></tr>`;
        }
    }

    function modeBadge(mode, brandName) {
        if (mode === "igaming") {
            const label = brandName ? `iGaming · ${brandName}` : "iGaming";
            return `<span class="status-badge badge-broadcast" title="${escHtml(label)}" style="font-size:11px;max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escHtml(label)}</span>`;
        }
        return `<span class="status-badge badge-confirmed" style="font-size:11px;">Standard</span>`;
    }

    function renderTx(items) {
        const tbody = $("txTbody");
        if (!items.length) {
            tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:24px;">No transactions yet. Click "Run Scan" to fetch.</td></tr>`;
            return;
        }
        const explorer = explorerFor(activeNetwork);
        tbody.innerHTML = items.map(tx => {
            const url = tx.tx_hash ? `${explorer}/tx/${tx.tx_hash}` : "#";
            return `
                <tr>
                    <td class="mono">${fmtTime(tx.transferred_at)}</td>
                    <td>${modeBadge(tx.scan_mode, tx.brand_name)}</td>
                    <td><strong>${escHtml(tx.token_symbol || "?")}</strong></td>
                    <td><strong>${fmtAmount(tx.amount)}</strong></td>
                    <td><span class="addr" title="${escHtml(tx.from_address)}">${fmtAddr(tx.from_address)}</span></td>
                    <td><span class="addr" title="${escHtml(tx.to_address)}">${fmtAddr(tx.to_address)}</span></td>
                    <td><a class="tx-link" href="${url}" target="_blank" rel="noopener" title="${escHtml(tx.tx_hash || '')}">${fmtAddr(tx.tx_hash)}</a></td>
                </tr>`;
        }).join("");
    }

    function renderPagination() {
        const start = txTotal === 0 ? 0 : txOffset + 1;
        const end = Math.min(txOffset + TX_LIMIT, txTotal);
        $("txPageInfo").textContent = `${start}–${end} of ${txTotal.toLocaleString()}`;
        $("txPrev").disabled = txOffset === 0;
        $("txNext").disabled = end >= txTotal;
    }

    $("txPrev").addEventListener("click", () => { txOffset = Math.max(0, txOffset - TX_LIMIT); loadTx(); });
    $("txNext").addEventListener("click", () => { if (txOffset + TX_LIMIT < txTotal) { txOffset += TX_LIMIT; loadTx(); } });
    $("txTokenFilter").addEventListener("change", () => { txOffset = 0; loadTx(); });
    $("txModeFilter").addEventListener("change", () => { txOffset = 0; loadTx(); });
    $("txRefreshBtn").addEventListener("click", () => loadTx());

    // -------- react to global header events --------
    document.addEventListener("networkchange", async (ev) => {
        activeNetwork = ev.detail.network;
        txOffset = 0;
        updateActiveTokensKpi();
        await Promise.all([loadStatus(), loadTx()]);
    });

    document.addEventListener("scancomplete", async () => {
        await Promise.all([loadStatus(), loadTokens(), loadTx()]);
    });

    // -------- init --------
    (async () => {
        await loadNetworks();   // populates local networks[] for explorerFor()
        await loadConfig();     // reads threshold KPI + syncs activeNetwork from GlobalHeader
        await Promise.all([loadStatus(), loadTokens()]);
        await loadTx();
    })();
})();
