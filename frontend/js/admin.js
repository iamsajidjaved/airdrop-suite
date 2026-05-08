/* Scanner — KPI strip, scanner status, qualifying-transactions table.
 * Threshold + token management have moved to the Settings page.
 */
(() => {
    const API = "/api/airdrop";
    const NET_KEY = "wallet_explorer_active_network";

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
    let activeNetwork = localStorage.getItem(NET_KEY) || "ethereum";
    let txOffset = 0;
    const TX_LIMIT = 25;
    let txTotal = 0;

    function explorerFor(network) {
        const n = networks.find(x => x.key === network);
        return n ? n.explorer : "https://etherscan.io";
    }

    // -------- networks --------
    async function loadNetworks() {
        try { networks = await api("/networks"); }
        catch {
            networks = [
                { key: "ethereum", label: "Ethereum Mainnet", chain_id: 1, explorer: "https://etherscan.io" },
                { key: "sepolia",  label: "Sepolia Testnet",  chain_id: 11155111, explorer: "https://sepolia.etherscan.io" },
            ];
        }
        if (!networks.find(n => n.key === activeNetwork)) {
            activeNetwork = networks[0]?.key || "ethereum";
        }
        const sel = $("networkSelect");
        sel.innerHTML = networks.map(n => `<option value="${n.key}">${n.label}</option>`).join("");
        sel.value = activeNetwork;
        updateNetworkDot();
    }

    function updateNetworkDot() {
        const dot = $("networkDot");
        if (!dot) return;
        dot.classList.remove("net-mainnet", "net-testnet");
        dot.classList.add(activeNetwork === "ethereum" ? "net-mainnet" : "net-testnet");
    }

    $("networkSelect").addEventListener("change", async (ev) => {
        activeNetwork = ev.target.value;
        localStorage.setItem(NET_KEY, activeNetwork);
        updateNetworkDot();
        txOffset = 0;
        renderTokenFilter();
        updateActiveTokensKpi();
        await loadTx();
    });

    // -------- threshold KPI --------
    async function loadThreshold() {
        try {
            const cfg = await api("/config");
            const kpiTh = $("kpiThreshold");
            if (kpiTh) kpiTh.textContent = `$${Number(cfg.min_threshold_usd).toLocaleString()}`;
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
            const blocks = s.last_block_per_token || {};
            const keys = Object.keys(blocks);
            if (!keys.length) {
                $("statBlocks").textContent = "No tokens";
            } else {
                $("statBlocks").innerHTML = keys
                    .map(k => `<span class="block-chip"><strong>${k}</strong> <span class="mono">${blocks[k].toLocaleString()}</span></span>`)
                    .join(" ");
            }
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
        } catch { /* token list optional */ }
    }

    function tokensForActive() {
        return tokens.filter(t => (t.network || "").toLowerCase() === activeNetwork);
    }

    function updateActiveTokensKpi() {
        const kpi = $("kpiActiveTokens");
        if (kpi) kpi.textContent = tokensForActive().filter(t => t.is_active).length;
    }

    function renderTokenFilter() {
        const sel = $("txTokenFilter");
        const current = sel.value;
        const list = tokensForActive();
        sel.innerHTML = '<option value="">All tokens</option>' +
            list.map(t => `<option value="${t.symbol}">${t.symbol}</option>`).join("");
        sel.value = current;
    }

    // -------- transactions --------
    async function loadTx() {
        const tokenSym = $("txTokenFilter").value;
        const params = new URLSearchParams({
            limit: String(TX_LIMIT),
            offset: String(txOffset),
            network: activeNetwork,
        });
        if (tokenSym) params.set("token", tokenSym);
        try {
            const res = await api("/transactions?" + params.toString());
            txTotal = res.total;
            renderTx(res.items);
            renderPagination();
        } catch (e) {
            $("txTbody").innerHTML = `<tr><td colspan="6" style="color: var(--accent-danger); padding: 16px;">${e.message}</td></tr>`;
        }
    }

    function renderTx(items) {
        const tbody = $("txTbody");
        if (!items.length) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color: var(--text-muted); padding: 24px;">No transactions yet. Click "Run Scan" to fetch.</td></tr>`;
            return;
        }
        const explorer = explorerFor(activeNetwork);
        tbody.innerHTML = items.map(tx => {
            const url = tx.tx_hash ? `${explorer}/tx/${tx.tx_hash}` : "#";
            return `
                <tr>
                    <td class="mono">${fmtTime(tx.transferred_at)}</td>
                    <td><strong>${tx.token_symbol || "?"}</strong></td>
                    <td><strong>${fmtAmount(tx.amount)}</strong></td>
                    <td><span class="addr" title="${tx.from_address}">${fmtAddr(tx.from_address)}</span></td>
                    <td><span class="addr" title="${tx.to_address}">${fmtAddr(tx.to_address)}</span></td>
                    <td><a class="tx-link" href="${url}" target="_blank" rel="noopener" title="${tx.tx_hash}">${fmtAddr(tx.tx_hash)}</a></td>
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

    $("txPrev").addEventListener("click", () => {
        txOffset = Math.max(0, txOffset - TX_LIMIT);
        loadTx();
    });
    $("txNext").addEventListener("click", () => {
        if (txOffset + TX_LIMIT < txTotal) { txOffset += TX_LIMIT; loadTx(); }
    });
    $("txTokenFilter").addEventListener("change", () => { txOffset = 0; loadTx(); });
    $("txRefreshBtn").addEventListener("click", () => loadTx());

    // -------- monitor run --------
    $("runMonitorBtn").addEventListener("click", async () => {
        const btn = $("runMonitorBtn");
        btn.disabled = true;
        const orig = btn.innerHTML;
        btn.innerHTML = '<span class="spinner"></span> Scanning…';
        try {
            const res = await api(`/monitor/run?network=${encodeURIComponent(activeNetwork)}`, { method: "POST" });
            const errs = res.errors && res.errors.length ? `\n\nNotes: ${res.errors.join("; ")}` : "";
            alert(`Scan complete on ${activeNetwork.toUpperCase()}.\n\nInserted ${res.new_transfers_inserted} new transfers.\nTotal stored: ${res.total_transfers_stored}.${errs}`);
            await Promise.all([loadStatus(), loadTokens(), loadTx()]);
        } catch (e) {
            alert(`Scan failed: ${e.message}`);
        } finally {
            btn.disabled = false;
            btn.innerHTML = orig;
        }
    });

    // -------- init --------
    (async () => {
        await loadNetworks();
        await Promise.all([loadThreshold(), loadStatus(), loadTokens()]);
        await loadTx();
    })();
})();
