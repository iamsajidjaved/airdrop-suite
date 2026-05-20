/* global-header.js — shared scanner controls injected into #globalHeaderActions on every page.
 *
 * Renders: network selector (dot + <select>), scan mode <select>, Run Scan button.
 * Network changes: DB-backed via PUT /api/airdrop/config.
 * Scan mode: persisted in localStorage.
 * iGaming / Both options are hidden when no brands are configured.
 *
 * Dispatches on document:
 *   "networkchange"  { detail: { network: string } }
 *   "scancomplete"   { detail: { result: object } }
 *
 * Exposes:
 *   window.GlobalHeader.activeNetwork          (string, updated after each network change)
 *   window.GlobalHeader.networks               (array, populated after loadNetworks)
 *   window.GlobalHeader.refreshScanModeOptions (fn, call with updated brands array to sync dropdown)
 */
(() => {
    const API = "/api/airdrop";
    const SCAN_MODE_KEY = "wallet_explorer_scan_mode";

    const escHtml = (s) => String(s ?? "")
        .replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

    async function apiCall(path, options = {}) {
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

    let networks = [];
    let activeNetwork = "ethereum";
    let hasBrands = false;

    function renderControls() {
        const container = document.getElementById("globalHeaderActions");
        if (!container) return;
        const savedMode = localStorage.getItem(SCAN_MODE_KEY) || "standard";
        container.innerHTML = `
            <div class="network-switch" title="Active blockchain network (applies globally)">
                <span class="network-switch-dot" id="globalNetworkDot"></span>
                <select id="globalNetworkSelect" class="network-select" aria-label="Active network">
                    <option value="">Loading…</option>
                </select>
            </div>
            <select id="globalScanModeSelect" class="network-select"
                    title="Scan mode" style="min-width:130px;">
                <option value="standard"${savedMode === "standard" ? " selected" : ""}>Standard</option>
                <option value="igaming" ${savedMode === "igaming"  ? " selected" : ""}>iGaming</option>
                <option value="both"    ${savedMode === "both"     ? " selected" : ""}>Both</option>
            </select>
            <button id="globalRunScanBtn" class="btn btn-primary btn-sm">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" stroke-width="2.5"
                     stroke-linecap="round" stroke-linejoin="round"
                     style="vertical-align:middle;margin-right:4px;">
                    <polygon points="5 3 19 12 5 21 5 3"/>
                </svg>
                Run Scan
            </button>`;
        attachListeners();
    }

    async function loadNetworks() {
        try { networks = await apiCall("/networks"); }
        catch {
            networks = [
                { key: "ethereum", label: "Ethereum Mainnet", chain_id: 1,       explorer: "https://etherscan.io" },
                { key: "sepolia",  label: "Sepolia Testnet",  chain_id: 11155111, explorer: "https://sepolia.etherscan.io" },
            ];
        }
        const sel = document.getElementById("globalNetworkSelect");
        if (!sel) return;
        sel.innerHTML = networks.map(n =>
            `<option value="${escHtml(n.key)}">${escHtml(n.label)}</option>`
        ).join("");
        sel.value = activeNetwork;
        window.GlobalHeader.networks = networks;
    }

    async function loadConfig() {
        try {
            const cfg = await apiCall("/config");
            if (cfg.active_network) activeNetwork = cfg.active_network;
        } catch { /* keep default */ }
        const sel = document.getElementById("globalNetworkSelect");
        if (sel) sel.value = activeNetwork;
        updateDot();
        window.GlobalHeader.activeNetwork = activeNetwork;
    }

    function updateDot() {
        const dot = document.getElementById("globalNetworkDot");
        if (!dot) return;
        dot.classList.remove("net-mainnet", "net-testnet");
        dot.classList.add(activeNetwork === "ethereum" ? "net-mainnet" : "net-testnet");
    }

    async function loadBrands() {
        try {
            const brands = await apiCall("/brands");
            hasBrands = Array.isArray(brands) && brands.length > 0;
        } catch {
            hasBrands = false;
        }
        applyScanModeVisibility();
    }

    function applyScanModeVisibility() {
        const modeSel = document.getElementById("globalScanModeSelect");
        if (!modeSel) return;
        const igamingOpt = modeSel.querySelector('option[value="igaming"]');
        const bothOpt    = modeSel.querySelector('option[value="both"]');
        if (!igamingOpt || !bothOpt) return;

        igamingOpt.hidden = !hasBrands;
        bothOpt.hidden    = !hasBrands;

        if (!hasBrands && (modeSel.value === "igaming" || modeSel.value === "both")) {
            modeSel.value = "standard";
            localStorage.setItem(SCAN_MODE_KEY, "standard");
        }
    }

    function refreshScanModeOptions(brandsArray) {
        hasBrands = Array.isArray(brandsArray) && brandsArray.length > 0;
        applyScanModeVisibility();
    }

    function attachListeners() {
        const netSel  = document.getElementById("globalNetworkSelect");
        const modeSel = document.getElementById("globalScanModeSelect");
        const runBtn  = document.getElementById("globalRunScanBtn");

        netSel.addEventListener("change", async (ev) => {
            const newNet = ev.target.value;
            if (!newNet || newNet === activeNetwork) return;
            try {
                await apiCall("/config", {
                    method: "PUT",
                    body: JSON.stringify({ active_network: newNet }),
                });
                activeNetwork = newNet;
                window.GlobalHeader.activeNetwork = activeNetwork;
                updateDot();
                document.dispatchEvent(new CustomEvent("networkchange", { detail: { network: activeNetwork } }));
            } catch (e) {
                alert(`Failed to switch network: ${e.message}`);
                ev.target.value = activeNetwork;
            }
        });

        modeSel.addEventListener("change", (ev) => {
            localStorage.setItem(SCAN_MODE_KEY, ev.target.value);
        });

        runBtn.addEventListener("click", async () => {
            const scanMode = modeSel.value || "standard";
            runBtn.disabled = true;
            const orig = runBtn.innerHTML;
            runBtn.innerHTML = '<span class="spinner"></span> Scanning…';
            try {
                const res = await apiCall(
                    `/monitor/run?scan_mode=${encodeURIComponent(scanMode)}`,
                    { method: "POST" }
                );
                document.dispatchEvent(new CustomEvent("scancomplete", { detail: { result: res } }));
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
            } catch (e) {
                alert(`Scan failed: ${e.message}`);
            } finally {
                runBtn.disabled = false;
                runBtn.innerHTML = orig;
            }
        });
    }

    window.GlobalHeader = { activeNetwork, networks: [], refreshScanModeOptions };

    (async () => {
        renderControls();
        await loadNetworks();
        await loadConfig();
        await loadBrands();
    })();
})();
