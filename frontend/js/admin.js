/* Airdrop Admin frontend logic */
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
        try {
            return new Date(iso).toISOString().replace("T", " ").slice(0, 19);
        } catch { return iso; }
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

    function flash(el, msg, isError = false) {
        el.textContent = msg;
        el.classList.toggle("error", isError);
        if (msg && !isError) {
            setTimeout(() => { if (el.textContent === msg) el.textContent = ""; }, 3000);
        }
    }

    // -------- state --------
    let tokens = [];
    let txOffset = 0;
    const TX_LIMIT = 25;
    let txTotal = 0;

    // -------- threshold --------
    async function loadThreshold() {
        try {
            const cfg = await api("/config");
            $("thresholdInput").value = cfg.min_threshold_usd;
            const kpiTh = $("kpiThreshold");
            if (kpiTh) kpiTh.textContent = `$${Number(cfg.min_threshold_usd).toLocaleString()}`;
        } catch (e) {
            flash($("thresholdStatus"), e.message, true);
        }
    }

    $("thresholdForm").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const val = parseFloat($("thresholdInput").value);
        if (!(val > 0)) {
            flash($("thresholdStatus"), "Threshold must be > 0", true);
            return;
        }
        try {
            await api("/config", { method: "PUT", body: JSON.stringify({ min_threshold_usd: val }) });
            flash($("thresholdStatus"), "Saved");
        } catch (e) {
            flash($("thresholdStatus"), e.message, true);
        }
    });

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
                    .map(k => `<span style="margin-right:14px;"><strong>${k}:</strong> <span class="mono">${blocks[k].toLocaleString()}</span></span>`)
                    .join("");
            }
        } catch (e) {
            $("statBlocks").textContent = e.message;
        }
    }

    // -------- tokens --------
    async function loadTokens() {
        try {
            tokens = await api("/tokens");
            const kpiAT = $("kpiActiveTokens");
            if (kpiAT) kpiAT.textContent = tokens.filter(t => t.is_active).length;
            renderTokens();
            renderTokenFilter();
        } catch (e) {
            $("tokensError").style.display = "block";
            $("tokensError").textContent = e.message;
        }
    }

    function renderTokens() {
        const tbody = $("tokensTbody");
        if (!tokens.length) {
            tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color: var(--text-muted); padding: 24px;">No tokens configured. Click "Add Token" to begin.</td></tr>`;
            return;
        }
        tbody.innerHTML = tokens.map(t => `
            <tr>
                <td><strong>${t.symbol}</strong></td>
                <td><span class="addr" title="${t.contract_address}">${fmtAddr(t.contract_address)}</span></td>
                <td>${t.decimals}</td>
                <td><span class="mono">${t.network}</span></td>
                <td><span class="pill ${t.is_active ? "pill-active" : "pill-inactive"}">${t.is_active ? "Active" : "Inactive"}</span></td>
                <td class="mono">${t.last_scanned_block ? Number(t.last_scanned_block).toLocaleString() : "—"}</td>
                <td>
                    <div class="row-actions">
                        <button class="row-btn" data-edit="${t.id}">Edit</button>
                        <button class="row-btn" data-block="${t.id}" title="Override last scanned block (set to 0 or any number to rescan from there)">Set block</button>
                        <button class="row-btn" data-toggle="${t.id}">${t.is_active ? "Disable" : "Enable"}</button>
                        <button class="row-btn danger" data-del="${t.id}">Delete</button>
                    </div>
                </td>
            </tr>
        `).join("");
    }

    function renderTokenFilter() {
        const sel = $("txTokenFilter");
        const current = sel.value;
        sel.innerHTML = '<option value="">All tokens</option>' +
            tokens.map(t => `<option value="${t.symbol}">${t.symbol}</option>`).join("");
        sel.value = current;
    }

    $("tokensTbody").addEventListener("click", async (ev) => {
        const t = ev.target;
        if (!(t instanceof HTMLElement)) return;
        const editId = t.dataset.edit;
        const toggleId = t.dataset.toggle;
        const delId = t.dataset.del;
        const blockId = t.dataset.block;

        if (editId) openTokenModal(tokens.find(x => x.id == editId));
        else if (blockId) {
            const tk = tokens.find(x => x.id == blockId);
            const cur = tk && tk.last_scanned_block ? tk.last_scanned_block : '';
            const ans = prompt(
                `Set "last scanned block" for ${tk ? tk.symbol : 'token'}.\n\n` +
                `• Enter a positive integer to resume scanning from that block.\n` +
                `• Enter 0 (or leave blank) to reset — the next scan starts from the chain head.\n\n` +
                `Current: ${cur || '(unset)'}`,
                String(cur || '')
            );
            if (ans === null) return;
            const trimmed = ans.trim();
            const payload = { last_scanned_block: trimmed === '' ? 0 : Number(trimmed) };
            if (!Number.isFinite(payload.last_scanned_block) || payload.last_scanned_block < 0) {
                alert('Block must be a non-negative integer.'); return;
            }
            try {
                await api(`/tokens/${blockId}`, { method: "PATCH", body: JSON.stringify(payload) });
                await loadTokens();
                await loadStatus();
            } catch (e) { alert(e.message); }
        } else if (toggleId) {
            const tk = tokens.find(x => x.id == toggleId);
            try {
                await api(`/tokens/${toggleId}`, { method: "PATCH", body: JSON.stringify({ is_active: !tk.is_active }) });
                await loadTokens();
            } catch (e) { alert(e.message); }
        } else if (delId) {
            if (!confirm("Delete this token? Fails if there are stored transactions.")) return;
            try {
                await api(`/tokens/${delId}`, { method: "DELETE" });
                await loadTokens();
            } catch (e) { alert(e.message); }
        }
    });

    // -------- token modal --------
    function openTokenModal(token) {
        $("tokenModalTitle").textContent = token ? "Edit Token" : "Add Token";
        $("tokenId").value = token ? token.id : "";
        $("tokenSymbol").value = token ? token.symbol : "";
        $("tokenContract").value = token ? token.contract_address : "";
        $("tokenDecimals").value = token ? token.decimals : 18;
        $("tokenNetwork").value = token ? token.network : "ethereum";
        $("tokenActive").checked = token ? token.is_active : true;
        $("tokenFormError").style.display = "none";
        $("tokenModal").style.display = "flex";
    }

    function closeTokenModal() { $("tokenModal").style.display = "none"; }

    $("addTokenBtn").addEventListener("click", () => openTokenModal(null));
    $("tokenCancelBtn").addEventListener("click", closeTokenModal);
    $("tokenModal").addEventListener("click", (ev) => {
        if (ev.target === $("tokenModal")) closeTokenModal();
    });

    $("tokenForm").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const id = $("tokenId").value;
        const payload = {
            symbol: $("tokenSymbol").value.trim(),
            contract_address: $("tokenContract").value.trim(),
            decimals: parseInt($("tokenDecimals").value, 10),
            network: $("tokenNetwork").value.trim() || "ethereum",
            is_active: $("tokenActive").checked,
        };
        try {
            if (id) {
                await api(`/tokens/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
            } else {
                await api("/tokens", { method: "POST", body: JSON.stringify(payload) });
            }
            closeTokenModal();
            await loadTokens();
        } catch (e) {
            $("tokenFormError").style.display = "block";
            $("tokenFormError").textContent = e.message;
        }
    });

    // -------- transactions --------
    async function loadTx() {
        const tokenSym = $("txTokenFilter").value;
        const params = new URLSearchParams({ limit: String(TX_LIMIT), offset: String(txOffset) });
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
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color: var(--text-muted); padding: 24px;">No transactions yet. Click "Run Monitor Now" to scan.</td></tr>`;
            return;
        }
        tbody.innerHTML = items.map(tx => {
            const etherscan = tx.tx_hash ? `https://etherscan.io/tx/${tx.tx_hash}` : "#";
            return `
                <tr>
                    <td class="mono">${fmtTime(tx.transferred_at)}</td>
                    <td><strong>${tx.token_symbol || "?"}</strong></td>
                    <td><strong>${fmtAmount(tx.amount)}</strong></td>
                    <td><span class="addr" title="${tx.from_address}">${fmtAddr(tx.from_address)}</span></td>
                    <td><span class="addr" title="${tx.to_address}">${fmtAddr(tx.to_address)}</span></td>
                    <td><a class="tx-link" href="${etherscan}" target="_blank" rel="noopener" title="${tx.tx_hash}">${fmtAddr(tx.tx_hash)}</a></td>
                </tr>
            `;
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
        if (txOffset + TX_LIMIT < txTotal) {
            txOffset += TX_LIMIT;
            loadTx();
        }
    });
    $("txTokenFilter").addEventListener("change", () => { txOffset = 0; loadTx(); });
    $("txRefreshBtn").addEventListener("click", () => loadTx());

    // -------- monitor run --------
    $("runMonitorBtn").addEventListener("click", async () => {
        const btn = $("runMonitorBtn");
        btn.disabled = true;
        const origText = btn.textContent;
        btn.textContent = "Scanning…";
        try {
            const res = await api("/monitor/run", { method: "POST" });
            const errs = res.errors && res.errors.length ? `\nErrors: ${res.errors.join("; ")}` : "";
            alert(`Inserted ${res.new_transfers_inserted} new transfers. Total: ${res.total_transfers_stored}.${errs}`);
            await Promise.all([loadStatus(), loadTokens(), loadTx()]);
        } catch (e) {
            alert(`Run failed: ${e.message}`);
        } finally {
            btn.disabled = false;
            btn.textContent = origText;
        }
    });

    // -------- init --------
    loadThreshold();
    loadStatus();
    loadTokens().then(loadTx);
})();
