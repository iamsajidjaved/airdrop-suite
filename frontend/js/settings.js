/* Settings page — consolidates threshold, tokens, sender wallets, admin token, config */
(() => {
    const AIRDROP_API = "/api/airdrop";
    const DIST_API    = "/api/distribution";
    const TOKEN_KEY   = "wallet_explorer_admin_token";

    const $ = (id) => document.getElementById(id);

    // ---------- shared helpers ----------
    const fmtAddr = (a) => a ? `${a.slice(0, 8)}…${a.slice(-6)}` : "—";
    const fmtNum = (v, dp = 6) => {
        if (v === null || v === undefined || v === "") return "—";
        const n = Number(v);
        if (!isFinite(n)) return String(v);
        if (n === 0) return "0";
        if (Math.abs(n) < 1) return n.toFixed(Math.min(dp, 6));
        return n.toLocaleString(undefined, { maximumFractionDigits: dp });
    };
    const escapeHtml = (s) => String(s ?? "")
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;").replaceAll("'", "&#39;");

    function flash(el, msg, isError = false) {
        if (!el) return;
        el.textContent = msg;
        el.classList.toggle("error", isError);
        if (msg && !isError) {
            setTimeout(() => { if (el.textContent === msg) el.textContent = ""; }, 3000);
        }
    }

    function getAdminToken() { return localStorage.getItem(TOKEN_KEY) || ""; }
    function setAdminToken(t) { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); }

    async function airdropApi(path, options = {}) {
        const res = await fetch(AIRDROP_API + path, {
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

    async function distApi(method, path, body = null) {
        const headers = { "Content-Type": "application/json" };
        const t = getAdminToken();
        if (t) headers["X-Admin-Token"] = t;
        const res = await fetch(DIST_API + path, {
            method, headers,
            body: body !== null ? JSON.stringify(body) : undefined,
        });
        if (res.status === 204) return null;
        let data;
        try { data = await res.json(); } catch { data = null; }
        if (!res.ok) {
            const detail = (data && (data.detail || data.error)) || res.statusText;
            throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
        }
        return data;
    }

    // ---------- tabs ----------
    const tabs = document.querySelectorAll(".settings-tab");
    const sections = {
        threshold: $("settings-threshold"),
        tokens:    $("settings-tokens"),
        wallets:   $("settings-wallets"),
        advanced:  $("settings-advanced"),
        filters:   $("settings-filters"),
        airdrop:   $("settings-airdrop"),
        brands:    $("settings-brands"),
    };

    function setTab(name) {
        Object.entries(sections).forEach(([k, el]) => {
            if (el) el.style.display = (k === name) ? "" : "none";
        });
        tabs.forEach(t => t.classList.toggle("active", t.dataset.tab === name));
        const url = new URL(window.location.href);
        url.hash = name;
        history.replaceState(null, "", url);
    }
    tabs.forEach(t => t.addEventListener("click", () => setTab(t.dataset.tab)));
    const initialTab = (window.location.hash || "").replace(/^#/, "") || "threshold";
    if (sections[initialTab]) setTab(initialTab);

    // =====================================================================
    // THRESHOLD
    // =====================================================================
    async function loadThreshold() {
        try {
            const cfg = await airdropApi("/config");
            $("thresholdInput").value = cfg.min_threshold_usd;
        } catch (e) {
            flash($("thresholdStatus"), e.message, true);
        }
    }

    $("thresholdForm").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const val = parseFloat($("thresholdInput").value);
        if (!(val > 0)) { flash($("thresholdStatus"), "Threshold must be > 0", true); return; }
        try {
            await airdropApi("/config", { method: "PUT", body: JSON.stringify({ min_threshold_usd: val }) });
            flash($("thresholdStatus"), "Saved");
            loadThreshold();
        } catch (e) {
            flash($("thresholdStatus"), e.message, true);
        }
    });

    // =====================================================================
    // TOKENS
    // =====================================================================
    let tokens = [];
    let networks = [];

    async function loadNetworks() {
        try { networks = await airdropApi("/networks"); }
        catch {
            networks = [
                { key: "ethereum", label: "Ethereum Mainnet", chain_id: 1, explorer: "https://etherscan.io" },
                { key: "sepolia",  label: "Sepolia Testnet",  chain_id: 11155111, explorer: "https://sepolia.etherscan.io" },
            ];
        }
        // Populate modal network select only — header selector is owned by global-header.js
        const tns = $("tokenNetwork");
        if (tns) tns.innerHTML = networks.map(n => `<option value="${n.key}">${escapeHtml(n.label)}</option>`).join("");
    }

    // Re-filter tokens table when the global header network changes
    document.addEventListener("networkchange", () => {
        renderTokens();
    });

    async function loadTokens() {
        try { tokens = await airdropApi("/tokens"); renderTokens(); }
        catch (e) {
            $("tokensError").style.display = "block";
            $("tokensError").textContent = e.message;
        }
    }

    function tokensForActive() {
        const net = (window.GlobalHeader && window.GlobalHeader.activeNetwork) || "ethereum";
        return tokens.filter(t => (t.network || "").toLowerCase() === net);
    }

    function renderTokens() {
        const tbody = $("tokensTbody");
        const list = tokensForActive();
        if (!list.length) {
            tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color: var(--text-muted); padding: 24px;">No tokens for this network. Click "+ Add Token" to add one.</td></tr>`;
            return;
        }
        tbody.innerHTML = list.map(t => {
            const netLabel = (networks.find(n => n.key === t.network) || {}).label || t.network;
            return `
            <tr>
                <td><strong>${escapeHtml(t.symbol)}</strong></td>
                <td><span class="addr" title="${escapeHtml(t.contract_address)}">${fmtAddr(t.contract_address)}</span></td>
                <td>${t.decimals}</td>
                <td><span class="net-pill net-${escapeHtml(t.network)}">${escapeHtml(netLabel)}</span></td>
                <td><span class="pill ${t.is_active ? "pill-active" : "pill-inactive"}">${t.is_active ? "Active" : "Disabled"}</span></td>
                <td class="mono">${t.last_scanned_block ? Number(t.last_scanned_block).toLocaleString() : "—"}</td>
                <td>
                    <div class="row-actions">
                        <button class="row-btn" data-edit="${t.id}">Edit</button>
                        <button class="row-btn" data-block="${t.id}" title="Override last scanned block">Block</button>
                        <button class="row-btn" data-toggle="${t.id}">${t.is_active ? "Disable" : "Enable"}</button>
                        <button class="row-btn danger" data-del="${t.id}">Delete</button>
                    </div>
                </td>
            </tr>`;
        }).join("");
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
                `• Enter 0 (or leave blank) to reset.\n\nCurrent: ${cur || '(unset)'}`,
                String(cur || '')
            );
            if (ans === null) return;
            const trimmed = ans.trim();
            const payload = { last_scanned_block: trimmed === '' ? 0 : Number(trimmed) };
            if (!Number.isFinite(payload.last_scanned_block) || payload.last_scanned_block < 0) {
                alert('Block must be a non-negative integer.'); return;
            }
            try { await airdropApi(`/tokens/${blockId}`, { method: "PATCH", body: JSON.stringify(payload) }); await loadTokens(); }
            catch (e) { alert(e.message); }
        } else if (toggleId) {
            const tk = tokens.find(x => x.id == toggleId);
            try { await airdropApi(`/tokens/${toggleId}`, { method: "PATCH", body: JSON.stringify({ is_active: !tk.is_active }) }); await loadTokens(); }
            catch (e) { alert(e.message); }
        } else if (delId) {
            if (!confirm("Delete this token? Fails if there are stored transactions.")) return;
            try { await airdropApi(`/tokens/${delId}`, { method: "DELETE" }); await loadTokens(); }
            catch (e) { alert(e.message); }
        }
    });

    function openTokenModal(token) {
        $("tokenModalTitle").textContent = token ? "Edit Token" : "Add Token";
        $("tokenId").value = token ? token.id : "";
        $("tokenSymbol").value = token ? token.symbol : "";
        $("tokenContract").value = token ? token.contract_address : "";
        $("tokenDecimals").value = token ? token.decimals : 6;
        $("tokenNetwork").value = token ? token.network
            : ((window.GlobalHeader && window.GlobalHeader.activeNetwork) || "ethereum");
        $("tokenActive").checked = token ? token.is_active : true;
        $("tokenFormError").style.display = "none";
        $("tokenModal").style.display = "flex";
    }
    function closeTokenModal() { $("tokenModal").style.display = "none"; }

    $("addTokenBtn").addEventListener("click", () => openTokenModal(null));
    $("tokenCancelBtn").addEventListener("click", closeTokenModal);
    $("tokenModal").addEventListener("click", (ev) => { if (ev.target === $("tokenModal")) closeTokenModal(); });

    $("tokenForm").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const id = $("tokenId").value;
        const payload = {
            symbol: $("tokenSymbol").value.trim(),
            contract_address: $("tokenContract").value.trim(),
            decimals: parseInt($("tokenDecimals").value, 10),
            network: $("tokenNetwork").value.trim() || (window.GlobalHeader && window.GlobalHeader.activeNetwork) || "ethereum",
            is_active: $("tokenActive").checked,
        };
        try {
            if (id) await airdropApi(`/tokens/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
            else    await airdropApi("/tokens", { method: "POST", body: JSON.stringify(payload) });
            closeTokenModal();
            await loadTokens();
        } catch (e) {
            $("tokenFormError").style.display = "block";
            $("tokenFormError").textContent = e.message;
        }
    });

    // =====================================================================
    // SENDER WALLETS
    // =====================================================================
    function fmtBalanceTimestamp(iso) {
        if (!iso) return "never";
        const d = new Date(iso);
        if (isNaN(d.getTime())) return "—";
        return d.toLocaleString();
    }

    function renderWalletRow(w) {
        const tokenBals = w.token_balances && Object.keys(w.token_balances).length
            ? Object.entries(w.token_balances).map(([sym, bal]) => `${escapeHtml(sym)}: ${fmtNum(bal)}`).join(" · ")
            : "—";
        const ethCell = (w.eth_balance !== undefined && w.eth_balance !== null) ? fmtNum(w.eth_balance) : "—";
        const ts = fmtBalanceTimestamp(w.balances_updated_at);
        return `<tr data-wallet-id="${w.id}">
            <td><span class="addr">${escapeHtml(w.address)}</span></td>
            <td>${escapeHtml(w.label || "")}</td>
            <td>${w.is_active ? '<span class="dist-pill confirmed">yes</span>' : '<span class="dist-pill paused">no</span>'}</td>
            <td class="mono">${ethCell}</td>
            <td>
                <div class="mono">${tokenBals}</div>
                <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">as of ${escapeHtml(ts)}</div>
            </td>
            <td class="row-actions">
                <button class="row-btn" data-w-action="refresh" data-id="${w.id}">Refresh</button>
                <button class="row-btn" data-w-action="toggle" data-id="${w.id}" data-active="${w.is_active}">${w.is_active ? "Disable" : "Enable"}</button>
                <button class="row-btn danger" data-w-action="delete" data-id="${w.id}">Delete</button>
            </td>
        </tr>`;
    }

    async function loadWallets() {
        const tbody = $("walletsTbody");
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color: var(--text-muted); padding: 24px;">Loading…</td></tr>`;
        try {
            const wallets = await distApi("GET", "/wallets");
            if (!wallets.length) {
                tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color: var(--text-muted); padding: 24px;">No wallets yet.</td></tr>`;
                return;
            }
            tbody.innerHTML = wallets.map(renderWalletRow).join("");
        } catch (e) {
            tbody.innerHTML = `<tr><td colspan="6" class="admin-error">${escapeHtml(e.message)}</td></tr>`;
        }
    }

    $("walletsTbody").addEventListener("click", async (ev) => {
        const btn = ev.target.closest("button[data-w-action]");
        if (!btn) return;
        const id = btn.dataset.id;
        const action = btn.dataset.wAction;
        try {
            if (action === "refresh") {
                btn.disabled = true;
                const original = btn.textContent;
                btn.textContent = "Refreshing…";
                try {
                    const updated = await distApi("POST", `/wallets/${id}/refresh`);
                    const row = document.querySelector(`tr[data-wallet-id="${id}"]`);
                    if (row) row.outerHTML = renderWalletRow(updated);
                } finally {
                    // If the row was replaced, the old button is gone — nothing to restore.
                    if (document.body.contains(btn)) {
                        btn.disabled = false;
                        btn.textContent = original;
                    }
                }
                return;
            }
            if (action === "toggle") {
                const isActive = btn.dataset.active === "true";
                await distApi("PATCH", `/wallets/${id}`, { is_active: !isActive });
            } else if (action === "delete") {
                if (!confirm("Delete this wallet? Encrypted key will be removed.")) return;
                await distApi("DELETE", `/wallets/${id}`);
            }
            await loadWallets();
        } catch (e) { alert(e.message); }
    });

    function openWalletModal() {
        $("walletPrivateKey").value = "";
        $("walletLabel").value = "";
        $("walletFormError").style.display = "none";
        $("walletModal").style.display = "flex";
    }
    function closeWalletModal() { $("walletModal").style.display = "none"; }

    $("addWalletBtn").addEventListener("click", openWalletModal);
    $("walletCancelBtn").addEventListener("click", closeWalletModal);
    $("walletModal").addEventListener("click", (ev) => { if (ev.target === $("walletModal")) closeWalletModal(); });

    $("walletForm").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const err = $("walletFormError");
        err.style.display = "none";
        try {
            await distApi("POST", "/wallets", {
                private_key: $("walletPrivateKey").value.trim(),
                label: $("walletLabel").value.trim() || null,
            });
            closeWalletModal();
            await loadWallets();
        } catch (e2) {
            err.textContent = e2.message;
            err.style.display = "block";
        }
    });

    // =====================================================================
    // ADVANCED — admin token + config
    // =====================================================================
    $("adminTokenInput").value = getAdminToken();
    $("adminTokenSave").addEventListener("click", () => {
        setAdminToken($("adminTokenInput").value.trim());
        flash($("adminTokenStatus"), "Saved");
        loadWallets();
    });

    // =====================================================================
    // FILTERS — quality filter settings + address blocklist
    // =====================================================================

    async function loadFilters() {
        try {
            const f = await airdropApi("/filters");
            $("filterEnabled").checked              = f.quality_filter_enabled;
            $("filterContractCheckEnabled").checked = f.quality_contract_check_enabled;
            $("filterContractConcurrency").value    = f.quality_contract_check_concurrency;
            $("filterAggregatorThreshold").value    = f.quality_per_run_aggregator_drop_threshold;
            $("filterMaxInbound").value             = f.quality_max_inbound_count;
            $("filterMaxSenders").value             = f.quality_max_distinct_senders;
            $("filterDormantDays").value            = f.quality_dormant_singleton_days;
            $("filterCrossTokenThreshold").value    = f.quality_cross_token_aggregator_threshold;
        } catch (e) {
            flash($("filtersStatus"), e.message, true);
        }
    }

    $("filtersForm").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const payload = {
            quality_filter_enabled:                    $("filterEnabled").checked,
            quality_contract_check_enabled:            $("filterContractCheckEnabled").checked,
            quality_contract_check_concurrency:        parseInt($("filterContractConcurrency").value, 10),
            quality_per_run_aggregator_drop_threshold: parseInt($("filterAggregatorThreshold").value, 10),
            quality_max_inbound_count:                 parseInt($("filterMaxInbound").value, 10),
            quality_max_distinct_senders:              parseInt($("filterMaxSenders").value, 10),
            quality_dormant_singleton_days:            parseInt($("filterDormantDays").value, 10),
            quality_cross_token_aggregator_threshold:  parseInt($("filterCrossTokenThreshold").value, 10),
        };
        const headers = { "Content-Type": "application/json" };
        const t = getAdminToken();
        if (t) headers["X-Admin-Token"] = t;
        try {
            const res = await fetch(AIRDROP_API + "/filters", {
                method: "PUT", headers, body: JSON.stringify(payload),
            });
            if (!res.ok) {
                const d = await res.json().catch(() => ({}));
                throw new Error(d.detail || res.statusText);
            }
            flash($("filtersStatus"), "Saved");
            await loadFilters();
        } catch (e) {
            flash($("filtersStatus"), e.message, true);
        }
    });

    // ---- Blocklist ----
    let _blocklistOffset = 0;
    const BLOCKLIST_LIMIT = 50;

    async function loadBlocklist(offset = 0) {
        _blocklistOffset = offset;
        const tbody = $("blocklistTbody");
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color: var(--text-muted); padding:16px;">Loading…</td></tr>`;
        try {
            const data = await airdropApi(`/quality/blocklist?limit=${BLOCKLIST_LIMIT}&offset=${offset}`);
            if (!data.items.length) {
                tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color: var(--text-muted); padding:16px;">No blocklist entries.</td></tr>`;
            } else {
                tbody.innerHTML = data.items.map(b => `
                    <tr>
                        <td class="mono">${escapeHtml(b.address)}</td>
                        <td>${escapeHtml(b.reason)}</td>
                        <td>${escapeHtml(b.source)}</td>
                        <td><span class="net-pill net-${escapeHtml(b.network)}">${escapeHtml(b.network)}</span></td>
                        <td class="mono">${b.added_at ? b.added_at.slice(0, 10) : "—"}</td>
                        <td class="row-actions">
                            <button class="row-btn danger" data-bl-del="${escapeHtml(b.address)}" data-bl-net="${escapeHtml(b.network)}">Remove</button>
                        </td>
                    </tr>`).join("");
            }
            const pg = $("blocklistPagination");
            const total = data.total;
            const page  = Math.floor(offset / BLOCKLIST_LIMIT) + 1;
            const totalPages = Math.ceil(total / BLOCKLIST_LIMIT) || 1;
            pg.innerHTML = `
                <button class="btn btn-secondary btn-sm" ${offset === 0 ? "disabled" : ""} data-bl-page="prev">Prev</button>
                <span style="font-size:13px; color:var(--text-muted);">Page ${page} of ${totalPages} (${total} total)</span>
                <button class="btn btn-secondary btn-sm" ${offset + BLOCKLIST_LIMIT >= total ? "disabled" : ""} data-bl-page="next">Next</button>`;
            $("blocklistError").style.display = "none";
        } catch (e) {
            tbody.innerHTML = `<tr><td colspan="6" class="admin-error">${escapeHtml(e.message)}</td></tr>`;
        }
    }

    $("blocklistPagination").addEventListener("click", (ev) => {
        const btn = ev.target.closest("[data-bl-page]");
        if (!btn || btn.disabled) return;
        if (btn.dataset.blPage === "prev") loadBlocklist(_blocklistOffset - BLOCKLIST_LIMIT);
        else loadBlocklist(_blocklistOffset + BLOCKLIST_LIMIT);
    });

    $("blocklistTbody").addEventListener("click", async (ev) => {
        const btn = ev.target.closest("[data-bl-del]");
        if (!btn) return;
        const addr = btn.dataset.blDel;
        const net  = btn.dataset.blNet;
        if (!confirm(`Remove ${addr} from the blocklist (${net})?`)) return;
        const headers = {};
        const t = getAdminToken();
        if (t) headers["X-Admin-Token"] = t;
        try {
            const res = await fetch(
                `${AIRDROP_API}/quality/blocklist?address=${encodeURIComponent(addr)}&network=${encodeURIComponent(net)}`,
                { method: "DELETE", headers }
            );
            if (!res.ok) {
                const d = await res.json().catch(() => ({}));
                throw new Error(d.detail || res.statusText);
            }
            await loadBlocklist(_blocklistOffset);
        } catch (e) { alert(e.message); }
    });

    function openBlocklistModal() {
        $("blAddress").value = "";
        $("blReason").value = "";
        $("blNetwork").innerHTML = networks.map(n =>
            `<option value="${escapeHtml(n.key)}">${escapeHtml(n.label)}</option>`
        ).join("");
        $("blNetwork").value = "ethereum";
        $("blocklistFormError").style.display = "none";
        $("blocklistModal").style.display = "flex";
    }
    function closeBlocklistModal() { $("blocklistModal").style.display = "none"; }

    $("addBlocklistBtn").addEventListener("click", openBlocklistModal);
    $("blCancelBtn").addEventListener("click", closeBlocklistModal);
    $("blocklistModal").addEventListener("click", (ev) => {
        if (ev.target === $("blocklistModal")) closeBlocklistModal();
    });

    $("blocklistForm").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const addr   = $("blAddress").value.trim();
        const reason = $("blReason").value.trim();
        const net    = $("blNetwork").value;
        const headers = {};
        const t = getAdminToken();
        if (t) headers["X-Admin-Token"] = t;
        try {
            const res = await fetch(
                `${AIRDROP_API}/quality/blocklist?address=${encodeURIComponent(addr)}&reason=${encodeURIComponent(reason)}&network=${encodeURIComponent(net)}&source=manual`,
                { method: "POST", headers }
            );
            if (!res.ok) {
                const d = await res.json().catch(() => ({}));
                throw new Error(d.detail || res.statusText);
            }
            closeBlocklistModal();
            await loadBlocklist(0);
        } catch (e) {
            $("blocklistFormError").textContent = e.message;
            $("blocklistFormError").style.display = "block";
        }
    });

    // =====================================================================
    // iGAMING BRANDS
    // =====================================================================
    let brands = [];

    async function loadBrands() {
        try {
            brands = await airdropApi("/brands");
            renderBrands();
            if (window.GlobalHeader && window.GlobalHeader.refreshScanModeOptions)
                window.GlobalHeader.refreshScanModeOptions(brands);
        } catch (e) {
            $("brandsTbody").innerHTML = `<tr><td colspan="6" style="color:var(--accent-danger);padding:16px;">${escapeHtml(e.message)}</td></tr>`;
        }
    }

    function renderBrands() {
        const tbody = $("brandsTbody");
        if (!brands.length) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:24px;">No brands configured. Click <strong>+ Add Brand</strong> to add one.</td></tr>`;
            return;
        }
        const fmtAddrShort = (a) => a ? `${a.slice(0, 8)}…${a.slice(-6)}` : "—";
        tbody.innerHTML = brands.map(b => `
            <tr>
                <td><strong>${escapeHtml(b.name)}</strong>${b.description ? `<br><small style="color:var(--text-muted)">${escapeHtml(b.description)}</small>` : ""}</td>
                <td><span class="addr mono" title="${escapeHtml(b.wallet_address)}">${fmtAddrShort(b.wallet_address)}</span></td>
                <td class="mono">${b.last_scanned_block ? Number(b.last_scanned_block).toLocaleString() : "—"}</td>
                <td>${(b.transaction_count || 0).toLocaleString()}</td>
                <td><span class="pill ${b.is_active ? "pill-active" : "pill-inactive"}">${b.is_active ? "Active" : "Inactive"}</span></td>
                <td>
                    <div class="row-actions">
                        <button class="row-btn" data-brand-edit="${b.id}">Edit</button>
                        <button class="row-btn danger" data-brand-del="${b.id}" data-brand-name="${escapeHtml(b.name)}">Delete</button>
                    </div>
                </td>
            </tr>`).join("");
    }

    function openBrandModal(brand = null) {
        $("brandModalTitle").textContent = brand ? "Edit Brand" : "Add iGaming Brand";
        $("brandId").value = brand ? brand.id : "";
        $("brandName").value = brand ? brand.name : "";
        $("brandWallet").value = brand ? brand.wallet_address : "";
        $("brandDesc").value = brand ? (brand.description || "") : "";
        $("brandActive").checked = brand ? brand.is_active : true;
        $("brandWallet").readOnly = !!brand;
        $("brandFormError").style.display = "none";
        $("brandModal").style.display = "flex";
        $("brandName").focus();
    }
    function closeBrandModal() { $("brandModal").style.display = "none"; }

    $("addBrandBtn").addEventListener("click", () => openBrandModal());
    $("brandCancelBtn").addEventListener("click", closeBrandModal);
    $("brandModal").addEventListener("click", (ev) => { if (ev.target === $("brandModal")) closeBrandModal(); });

    $("brandsTbody").addEventListener("click", async (ev) => {
        const editBtn = ev.target.closest("[data-brand-edit]");
        const delBtn  = ev.target.closest("[data-brand-del]");
        if (editBtn) {
            const brand = brands.find(b => b.id == editBtn.dataset.brandEdit);
            if (brand) openBrandModal(brand);
        } else if (delBtn) {
            const name = delBtn.dataset.brandName;
            const id   = delBtn.dataset.brandDel;
            if (!confirm(`Delete brand "${name}"? This will remove the FK link from iGaming transactions (they remain, but won't reference this brand).`)) return;
            try {
                await airdropApi(`/brands/${id}`, { method: "DELETE" });
                await loadBrands();
            } catch (e) { alert(`Failed to delete: ${e.message}`); }
        }
    });

    $("brandSaveBtn").addEventListener("click", async () => {
        const id     = $("brandId").value;
        const name   = $("brandName").value.trim();
        const wallet = $("brandWallet").value.trim();
        const desc   = $("brandDesc").value.trim();
        const active = $("brandActive").checked;
        const errEl  = $("brandFormError");

        if (!name) { errEl.textContent = "Brand name is required."; errEl.style.display = "block"; return; }
        if (!id && !wallet) { errEl.textContent = "Wallet address is required."; errEl.style.display = "block"; return; }

        $("brandSaveBtn").disabled = true;
        errEl.style.display = "none";
        try {
            if (id) {
                await airdropApi(`/brands/${id}`, {
                    method: "PATCH",
                    body: JSON.stringify({ name, description: desc || null, is_active: active }),
                });
            } else {
                await airdropApi("/brands", {
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

    // =====================================================================
    // AIRDROP CONFIG — distribution worker settings
    // =====================================================================
    async function loadTokensForDist() {
        try {
            const tkns = await airdropApi("/tokens");
            const sel = $("cfgToken");
            sel.innerHTML = '<option value="">— select token —</option>' +
                (tkns || []).map(t =>
                    `<option value="${escapeHtml(String(t.id))}">${escapeHtml(t.symbol)} (${escapeHtml(t.network)})</option>`
                ).join("");
        } catch (e) {
            console.warn("loadTokensForDist:", e);
        }
    }

    async function loadDistConfig() {
        try {
            const cfg = await distApi("GET", "/config");
            if (cfg.distribution_token_id) $("cfgToken").value = String(cfg.distribution_token_id);
            $("cfgAmount").value = cfg.distribution_amount || "1.0";
            const modeRadio = document.querySelector(`input[name="cfgMode"][value="${cfg.distribution_mode || "all"}"]`);
            if (modeRadio) modeRadio.checked = true;
            $("cfgInterval").value = cfg.distribution_interval_seconds || 10;
            $("cfgRetries").value = cfg.distribution_max_retries || 3;
        } catch (e) {
            flash($("airdropCfgStatus"), e.message, true);
        }
    }

    $("airdropCfgForm").addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const tokenId = Number($("cfgToken").value) || null;
        const payload = {
            distribution_mode: document.querySelector('input[name="cfgMode"]:checked').value,
            distribution_amount: $("cfgAmount").value,
            distribution_interval_seconds: Number($("cfgInterval").value) || 10,
            distribution_max_retries: Number($("cfgRetries").value) || 3,
        };
        if (tokenId) payload.distribution_token_id = tokenId;
        try {
            await distApi("PUT", "/config", payload);
            flash($("airdropCfgStatus"), "Saved");
        } catch (e) {
            flash($("airdropCfgStatus"), e.message, true);
        }
    });

    // ---------- init ----------
    (async () => {
        await loadNetworks();
        let blocklistLoaded = false;
        let airdropCfgLoaded = false;
        let brandsLoaded = false;

        tabs.forEach(tab => tab.addEventListener("click", async () => {
            if (tab.dataset.tab === "filters" && !blocklistLoaded) {
                blocklistLoaded = true;
                await loadBlocklist(0);
            }
            if (tab.dataset.tab === "airdrop" && !airdropCfgLoaded) {
                airdropCfgLoaded = true;
                await loadTokensForDist();
                await loadDistConfig();
            }
            if (tab.dataset.tab === "brands" && !brandsLoaded) {
                brandsLoaded = true;
                await loadBrands();
            }
        }));

        await Promise.all([loadThreshold(), loadTokens(), loadWallets(), loadFilters()]);

        const initHash = (window.location.hash || "").replace(/^#/, "");
        if (initHash === "filters" && !blocklistLoaded) {
            blocklistLoaded = true;
            await loadBlocklist(0);
        }
        if (initHash === "airdrop" && !airdropCfgLoaded) {
            airdropCfgLoaded = true;
            await loadTokensForDist();
            await loadDistConfig();
        }
        if (initHash === "brands" && !brandsLoaded) {
            brandsLoaded = true;
            await loadBrands();
        }
    })();
})();
