/* Settings page — consolidates threshold, tokens, sender wallets, admin token, config */
(() => {
    const AIRDROP_API = "/api/airdrop";
    const DIST_API    = "/api/distribution";
    const NET_KEY     = "wallet_explorer_active_network";
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
    let activeNetwork = localStorage.getItem(NET_KEY) || "ethereum";

    async function loadNetworks() {
        try { networks = await airdropApi("/networks"); }
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
        sel.innerHTML = networks.map(n => `<option value="${n.key}">${escapeHtml(n.label)}</option>`).join("");
        sel.value = activeNetwork;
        updateNetworkDot();
        const tns = $("tokenNetwork");
        tns.innerHTML = networks.map(n => `<option value="${n.key}">${escapeHtml(n.label)}</option>`).join("");
    }

    function updateNetworkDot() {
        const dot = $("networkDot");
        if (!dot) return;
        dot.classList.remove("net-mainnet", "net-testnet");
        dot.classList.add(activeNetwork === "ethereum" ? "net-mainnet" : "net-testnet");
    }

    $("networkSelect").addEventListener("change", (ev) => {
        activeNetwork = ev.target.value;
        localStorage.setItem(NET_KEY, activeNetwork);
        updateNetworkDot();
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
        return tokens.filter(t => (t.network || "").toLowerCase() === activeNetwork);
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
        $("tokenNetwork").value = token ? token.network : activeNetwork;
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
            network: $("tokenNetwork").value.trim() || activeNetwork,
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
    async function loadWallets() {
        const tbody = $("walletsTbody");
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color: var(--text-muted); padding: 24px;">Loading…</td></tr>`;
        try {
            const wallets = await distApi("GET", "/wallets?include_balances=true");
            if (!wallets.length) {
                tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color: var(--text-muted); padding: 24px;">No wallets yet.</td></tr>`;
                return;
            }
            tbody.innerHTML = wallets.map(w => {
                const tokenBals = w.token_balances
                    ? Object.entries(w.token_balances).map(([sym, bal]) => `${escapeHtml(sym)}: ${fmtNum(bal)}`).join(" · ")
                    : "—";
                return `<tr>
                    <td><span class="addr">${escapeHtml(w.address)}</span></td>
                    <td>${escapeHtml(w.label || "")}</td>
                    <td>${w.is_active ? '<span class="dist-pill confirmed">yes</span>' : '<span class="dist-pill paused">no</span>'}</td>
                    <td class="mono">${w.eth_balance !== undefined && w.eth_balance !== null ? fmtNum(w.eth_balance) : "—"}</td>
                    <td class="mono">${tokenBals}</td>
                    <td class="row-actions">
                        <button class="row-btn" data-w-action="toggle" data-id="${w.id}" data-active="${w.is_active}">${w.is_active ? "Disable" : "Enable"}</button>
                        <button class="row-btn danger" data-w-action="delete" data-id="${w.id}">Delete</button>
                    </td>
                </tr>`;
            }).join("");
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
        loadConfig();
        loadWallets();
    });

    async function loadConfig() {
        try {
            const c = await distApi("GET", "/config");
            const cells = [
                ["ETH RPC", c.eth_rpc_configured ? '<span class="dist-pill confirmed">configured</span>' : '<span class="dist-pill failed">missing</span>'],
                ["KEK", c.kek_configured ? '<span class="dist-pill confirmed">configured</span>' : '<span class="dist-pill failed">missing</span>'],
                ["Admin auth", c.admin_token_required ? '<span class="dist-pill running">enabled</span>' : '<span class="dist-pill draft">disabled</span>'],
                ["Max gas (gwei)", fmtNum(c.max_gas_price_gwei, 2)],
                ["Per-wallet daily cap", c.per_wallet_daily_cap ? fmtNum(c.per_wallet_daily_cap) : "unlimited"],
                ["Max in-flight", c.max_inflight],
                ["Receipt poll", `${c.receipt_poll_seconds}s`],
                ["Max retries", c.max_retries_per_recipient],
            ];
            $("configBox").innerHTML = cells.map(([k, v]) =>
                `<div class="status-cell"><div class="status-label">${k}</div><div class="status-value small">${v}</div></div>`
            ).join("");
        } catch (e) {
            $("configBox").innerHTML = `<div class="admin-error">${escapeHtml(e.message)}</div>`;
        }
    }

    // ---------- init ----------
    (async () => {
        await loadNetworks();
        await Promise.all([loadThreshold(), loadTokens(), loadWallets(), loadConfig()]);
    })();
})();
