/* Scanner — KPI strip, status, iGaming brands, qualifying-transactions table.
 *
 * Network is now a global DB-backed setting (active_network in airdrop_config).
 * Changing it here saves to the API and takes effect on the next scan.
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
    let brands = [];
    let activeNetwork = "ethereum";
    let txOffset = 0;
    const TX_LIMIT = 25;
    let txTotal = 0;

    function explorerFor(net) {
        const n = networks.find(x => x.key === net);
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
        const sel = $("networkSelect");
        sel.innerHTML = networks.map(n =>
            `<option value="${escHtml(n.key)}">${escHtml(n.label)}</option>`
        ).join("");
    }

    function updateNetworkDot() {
        const dot = $("networkDot");
        if (!dot) return;
        dot.classList.remove("net-mainnet", "net-testnet");
        dot.classList.add(activeNetwork === "ethereum" ? "net-mainnet" : "net-testnet");
    }

    // Network selector: save to DB, reload data
    $("networkSelect").addEventListener("change", async (ev) => {
        const newNet = ev.target.value;
        if (!newNet || newNet === activeNetwork) return;
        try {
            await api("/config", {
                method: "PUT",
                body: JSON.stringify({ active_network: newNet }),
            });
            activeNetwork = newNet;
            updateNetworkDot();
            txOffset = 0;
            updateActiveTokensKpi();
            await Promise.all([loadStatus(), loadTx()]);
        } catch (e) {
            alert(`Failed to switch network: ${e.message}`);
            ev.target.value = activeNetwork; // revert
        }
    });

    // -------- config (threshold + active_network) --------
    async function loadConfig() {
        try {
            const cfg = await api("/config");
            const kpiTh = $("kpiThreshold");
            if (kpiTh) kpiTh.textContent = `$${Number(cfg.min_threshold_usd).toLocaleString()}`;
            // sync active network with DB value
            if (cfg.active_network && cfg.active_network !== activeNetwork) {
                activeNetwork = cfg.active_network;
            }
            const sel = $("networkSelect");
            if (sel) sel.value = activeNetwork;
            updateNetworkDot();
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

    // -------- iGaming Brands --------
    async function loadBrands() {
        try {
            brands = await api("/brands");
            renderBrands();
        } catch (e) {
            $("brandsTbody").innerHTML = `<tr><td colspan="6" style="color:var(--accent-danger);padding:16px;">${escHtml(e.message)}</td></tr>`;
        }
    }

    function renderBrands() {
        const tbody = $("brandsTbody");
        if (!brands.length) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:24px;">No brands configured. Click <strong>+ Add Brand</strong> to add one.</td></tr>`;
            return;
        }
        tbody.innerHTML = brands.map(b => `
            <tr>
                <td><strong>${escHtml(b.name)}</strong>${b.description ? `<br><small style="color:var(--text-muted)">${escHtml(b.description)}</small>` : ""}</td>
                <td><span class="addr mono" title="${escHtml(b.wallet_address)}">${fmtAddr(b.wallet_address)}</span></td>
                <td class="mono">${b.last_scanned_block ? b.last_scanned_block.toLocaleString() : "—"}</td>
                <td>${(b.transaction_count || 0).toLocaleString()}</td>
                <td><span class="status-badge ${b.is_active ? "badge-confirmed" : "badge-failed"}">${b.is_active ? "Active" : "Inactive"}</span></td>
                <td>
                    <button class="btn btn-secondary btn-sm" onclick="editBrand(${b.id})">Edit</button>
                    <button class="btn btn-sm" style="background:var(--accent-danger,#ef4444);color:#fff;margin-left:4px;" onclick="deleteBrand(${b.id},'${escHtml(b.name)}')">Delete</button>
                </td>
            </tr>`).join("");
    }

    // Brand modal helpers
    function openBrandModal(brand = null) {
        $("brandModalTitle").textContent = brand ? "Edit Brand" : "Add iGaming Brand";
        $("brandId").value = brand ? brand.id : "";
        $("brandName").value = brand ? brand.name : "";
        $("brandWallet").value = brand ? brand.wallet_address : "";
        $("brandDesc").value = brand ? (brand.description || "") : "";
        $("brandActive").checked = brand ? brand.is_active : true;
        $("brandWallet").readOnly = !!brand; // wallet address can't be changed after creation
        $("brandFormError").style.display = "none";
        $("brandModal").style.display = "flex";
        $("brandName").focus();
    }

    function closeBrandModal() {
        $("brandModal").style.display = "none";
    }

    $("addBrandBtn").addEventListener("click", () => openBrandModal());
    $("brandModalClose").addEventListener("click", closeBrandModal);
    $("brandCancelBtn").addEventListener("click", closeBrandModal);
    $("brandModal").addEventListener("click", (e) => { if (e.target === $("brandModal")) closeBrandModal(); });

    window.editBrand = function(id) {
        const brand = brands.find(b => b.id === id);
        if (brand) openBrandModal(brand);
    };

    window.deleteBrand = async function(id, name) {
        if (!confirm(`Delete brand "${name}"? This will also remove the FK link from iGaming transactions (they remain, but won't reference this brand).`)) return;
        try {
            await api(`/brands/${id}`, { method: "DELETE" });
            await loadBrands();
        } catch (e) {
            alert(`Failed to delete: ${e.message}`);
        }
    };

    $("brandSaveBtn").addEventListener("click", async () => {
        const id = $("brandId").value;
        const name = $("brandName").value.trim();
        const wallet = $("brandWallet").value.trim();
        const desc = $("brandDesc").value.trim();
        const active = $("brandActive").checked;
        const errEl = $("brandFormError");

        if (!name) { errEl.textContent = "Brand name is required."; errEl.style.display = "block"; return; }
        if (!id && !wallet) { errEl.textContent = "Wallet address is required."; errEl.style.display = "block"; return; }

        $("brandSaveBtn").disabled = true;
        errEl.style.display = "none";
        try {
            if (id) {
                await api(`/brands/${id}`, {
                    method: "PATCH",
                    body: JSON.stringify({ name, description: desc || null, is_active: active }),
                });
            } else {
                await api("/brands", {
                    method: "POST",
                    body: JSON.stringify({ name, wallet_address: wallet, description: desc || null, is_active: active }),
                });
            }
            closeBrandModal();
            await loadBrands();
        } catch (e) {
            errEl.textContent = e.message;
            errEl.style.display = "block";
        } finally {
            $("brandSaveBtn").disabled = false;
        }
    });

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

    // -------- monitor run --------
    $("runMonitorBtn").addEventListener("click", async () => {
        const btn = $("runMonitorBtn");
        const scanMode = $("scanModeSelect").value || "standard";
        btn.disabled = true;
        const orig = btn.innerHTML;
        btn.innerHTML = '<span class="spinner"></span> Scanning…';
        try {
            const res = await api(`/monitor/run?scan_mode=${encodeURIComponent(scanMode)}`, { method: "POST" });
            const parts = [
                `Scan complete (mode: ${scanMode}).`,
                `Inserted ${res.new_transfers_inserted} new transfers.`,
                `Total stored: ${res.total_transfers_stored}.`,
            ];
            if (res.tokens_scanned && res.tokens_scanned.length)
                parts.push(`Tokens: ${res.tokens_scanned.join(", ")}.`);
            if (res.brands_scanned && res.brands_scanned.length)
                parts.push(`Brands: ${res.brands_scanned.join(", ")}.`);
            if (res.errors && res.errors.length)
                parts.push(`\nNotes: ${res.errors.join("; ")}`);
            alert(parts.join("\n"));
            await Promise.all([loadStatus(), loadBrands(), loadTokens(), loadTx()]);
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
        await loadConfig();        // sets activeNetwork from DB, syncs select
        await Promise.all([loadStatus(), loadTokens(), loadBrands()]);
        await loadTx();
    })();
})();
