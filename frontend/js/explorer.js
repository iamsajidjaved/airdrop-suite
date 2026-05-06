// Explorer Dashboard JavaScript

// State management
let allTransactions = [];
let filteredTransactions = [];
let currentPage = 1;
let ITEMS_PER_PAGE = 50;
let currentSort = { field: 'timestamp', direction: 'desc' };

// Get params from URL
const urlParams = new URLSearchParams(window.location.search);
const walletAddress = urlParams.get('address');

// Read date range: prefer URL params, fall back to sessionStorage
// (sessionStorage is written by wallet.js before redirect as a safety net)
let fromDate = urlParams.get('from_date') || sessionStorage.getItem('wx_from_date') || null;
let toDate   = urlParams.get('to_date')   || sessionStorage.getItem('wx_to_date')   || null;

// Treat empty string as null
if (!fromDate) fromDate = null;
if (!toDate)   toDate   = null;

console.log('[explorer] URL search:', window.location.search);
console.log('[explorer] fromDate resolved:', fromDate, '| toDate resolved:', toDate);

// Clear sessionStorage after reading so stale dates don't persist on future visits
sessionStorage.removeItem('wx_from_date');
sessionStorage.removeItem('wx_to_date');

// DOM Elements
const summarySection = document.getElementById('summarySection');
const filtersSection = document.getElementById('filtersSection');
const transactionsSection = document.getElementById('transactionsSection');
const loadingState = document.getElementById('loadingState');
const errorState = document.getElementById('errorState');
const emptyState = document.getElementById('emptyState');

const walletAddressDisplay = document.getElementById('walletAddressDisplay');
const networksDisplay = document.getElementById('networksDisplay');
const totalTxDisplay = document.getElementById('totalTxDisplay');
const firstSeenDisplay = document.getElementById('firstSeenDisplay');
const lastActivityDisplay = document.getElementById('lastActivityDisplay');

const dateRangeBanner = document.getElementById('dateRangeBanner');
const directionFilter = document.getElementById('directionFilter');
const counterpartyFilter = document.getElementById('counterpartyFilter');
const counterpartySearch = document.getElementById('counterpartySearch');
const counterpartyTrigger = document.getElementById('counterpartyTrigger');
const counterpartyDropdown = document.getElementById('counterpartyDropdown');
const counterpartyOptions = document.getElementById('counterpartyOptions');
const counterpartySelected = document.getElementById('counterpartySelected');

const headerBackBtn = document.getElementById('headerBackBtn');
const resetFiltersBtn = document.getElementById('resetFiltersBtn');
const exportCsvBtn = document.getElementById('exportCsvBtn');
const perPageSelect = document.getElementById('perPageSelect');

const transactionsTBody = document.getElementById('transactionsTBody');
const filteredCount = document.getElementById('filteredCount');
const currentPageSpan = document.getElementById('currentPage');
const totalPagesSpan = document.getElementById('totalPages');
const prevPageBtn = document.getElementById('prevPageBtn');
const nextPageBtn = document.getElementById('nextPageBtn');

// Initialize
async function init() {
    if (!walletAddress) {
        window.location.href = '/';
        return;
    }

    await fetchTransactions();
}

// Fetch transactions from API
async function fetchTransactions() {
    showLoading();

    try {
        // Build URL with optional date range params
        const params = new URLSearchParams();
        if (fromDate) params.set('from_date', fromDate);
        if (toDate)   params.set('to_date',   toDate);
        const query = params.toString() ? `?${params.toString()}` : '';
        const url = `/api/transactions/${walletAddress}${query}`;
        console.log('[fetch] walletAddress:', walletAddress);
        console.log('[fetch] fromDate:', fromDate, '| toDate:', toDate);
        console.log('[fetch] API URL:', url);
        
        const response = await fetch(url);
        console.log('Response status:', response.status);
        
        if (!response.ok) {
            const errorText = await response.text();
            console.error('Error response:', errorText);
            throw new Error('Failed to fetch transactions');
        }

        const data = await response.json();
        console.log('Data received:', data);
        
        allTransactions = data.transactions;
        filteredTransactions = [...allTransactions];

        updateSummary(data);
        updateCounterpartyFilter();
        showDateRangeBanner();
        applyFilters(); // also calls updateSummaryFromFiltered()
        
        hideLoading();
        showContent();
    } catch (error) {
        console.error('Error fetching transactions:', error);
        showError('Failed to load transactions. Please try again.');
    }
}

// Update summary section — static parts (wallet address, networks) set once
function updateSummary(data) {
    walletAddressDisplay.textContent = data.wallet_address;

    const sidebarWalletAddr = document.getElementById('sidebarWalletAddr');
    if (sidebarWalletAddr) {
        sidebarWalletAddr.textContent = truncateAddress(data.wallet_address);
        sidebarWalletAddr.title = data.wallet_address;
    }

    const networkBadges = data.networks.map(net =>
        `<span class="badge badge-${net.toLowerCase()}">${net}</span>`
    ).join(' ');
    networksDisplay.innerHTML = networkBadges;
}

// Update summary stats that depend on the current filtered set
function updateSummaryFromFiltered() {
    const txs = filteredTransactions;

    totalTxDisplay.textContent = txs.length.toLocaleString();

    if (txs.length === 0) {
        firstSeenDisplay.textContent = '-';
        lastActivityDisplay.textContent = '-';
        return;
    }

    let minTs = Infinity;
    let maxTs = -Infinity;
    txs.forEach(tx => {
        const ts = Number(tx.timestamp);
        if (ts < minTs) minTs = ts;
        if (ts > maxTs) maxTs = ts;
    });

    firstSeenDisplay.textContent = formatTimestampDate(minTs);
    lastActivityDisplay.textContent = formatTimestampDate(maxTs);
}

// Format a unix timestamp to a readable date string
function formatTimestampDate(ts) {
    if (!ts || !isFinite(ts)) return '-';
    const d = new Date(ts * 1000);
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

// Update counterparty filter dropdown
function updateCounterpartyFilter() {
    const counterparties = new Set();
    
    allTransactions.forEach(tx => {
        // Only add non-empty addresses
        if (tx.from && tx.from.trim()) {
            counterparties.add(tx.from);
        }
        if (tx.to && tx.to.trim()) {
            counterparties.add(tx.to);
        }
    });

    // Remove the wallet address itself
    counterparties.delete(walletAddress);

    // Store all addresses for filtering
    window.allCounterparties = Array.from(counterparties).sort();
    
    // Initial render of all addresses
    renderCounterpartyOptions();
}

// Render counterparty options based on search
function renderCounterpartyOptions(searchTerm = '') {
    // Safety check: ensure allCounterparties exists
    if (!window.allCounterparties) {
        window.allCounterparties = [];
    }
    
    // Filter addresses by search term and remove any empty values
    const filteredAddresses = window.allCounterparties.filter(address => 
        address && 
        address.trim() && 
        address.toLowerCase().includes(searchTerm.toLowerCase())
    );
    
    // Start with "All Addresses" option
    counterpartyOptions.innerHTML = '<div class="dropdown-option" data-value="">All Addresses</div>';
    
    // Add filtered wallet addresses
    filteredAddresses.forEach(address => {
        const option = document.createElement('div');
        option.className = 'dropdown-option';
        option.setAttribute('data-value', address);
        option.textContent = truncateAddress(address);
        option.title = address; // Show full address on hover
        counterpartyOptions.appendChild(option);
    });
    
    // Attach click handlers to options
    attachOptionHandlers();
}

// Attach click handlers to dropdown options
function attachOptionHandlers() {
    const options = counterpartyOptions.querySelectorAll('.dropdown-option');
    options.forEach(option => {
        option.addEventListener('click', () => {
            const value = option.getAttribute('data-value');
            const text = option.textContent;
            
            // Update hidden input and selected text
            counterpartyFilter.value = value;
            counterpartySelected.textContent = text;
            
            // Update selected state
            options.forEach(opt => opt.classList.remove('selected'));
            option.classList.add('selected');
            
            // Close dropdown
            closeCounterpartyDropdown();
            
            // Apply filters
            applyFilters();
        });
    });
}

// Toggle dropdown
function toggleCounterpartyDropdown() {
    const isActive = counterpartyDropdown.classList.contains('active');
    if (isActive) {
        closeCounterpartyDropdown();
    } else {
        openCounterpartyDropdown();
    }
}

// Open dropdown
function openCounterpartyDropdown() {
    counterpartyDropdown.classList.add('active');
    counterpartyTrigger.classList.add('active');
    counterpartySearch.focus();
}

// Close dropdown
function closeCounterpartyDropdown() {
    counterpartyDropdown.classList.remove('active');
    counterpartyTrigger.classList.remove('active');
    counterpartySearch.value = '';
    renderCounterpartyOptions(); // Reset filter
}

// Show date range banner if dates were specified
function showDateRangeBanner() {
    if (!dateRangeBanner) return;
    if (fromDate || toDate) {
        const from = fromDate ? new Date(fromDate + 'T00:00:00').toLocaleDateString() : 'beginning';
        const to = toDate ? new Date(toDate + 'T00:00:00').toLocaleDateString() : 'today';
        dateRangeBanner.textContent = `📅 Showing transactions from ${from} to ${to}`;
        dateRangeBanner.style.display = 'block';
    } else {
        dateRangeBanner.style.display = 'none';
    }
}

// Apply filters — date range is enforced BOTH by the API and here client-side as a hard guarantee
function applyFilters() {
    // Convert date strings to UTC unix timestamps (seconds) for comparison
    // Using 'Z' suffix forces UTC interpretation in all browsers
    const fromTs = fromDate ? new Date(fromDate + 'T00:00:00Z').getTime() / 1000 : null;
    const toTs   = toDate   ? new Date(toDate   + 'T23:59:59Z').getTime() / 1000 : null;

    console.log('[filter] fromDate:', fromDate, '→ fromTs:', fromTs);
    console.log('[filter] toDate:',   toDate,   '→ toTs:',   toTs);

    filteredTransactions = allTransactions.filter(tx => {
        // Date range — hard local filter (guaranteed regardless of API behaviour)
        if (fromTs !== null && tx.timestamp < fromTs) return false;
        if (toTs   !== null && tx.timestamp > toTs)   return false;

        // Direction filter
        if (directionFilter && directionFilter.value && tx.direction !== directionFilter.value) {
            return false;
        }

        // Counterparty filter
        if (counterpartyFilter && counterpartyFilter.value) {
            if (tx.from !== counterpartyFilter.value && tx.to !== counterpartyFilter.value) {
                return false;
            }
        }

        return true;
    });

    console.log('[filter] allTransactions:', allTransactions.length, '→ filtered:', filteredTransactions.length);

    currentPage = 1;
    updateSummaryFromFiltered();
    renderTransactions();
}

// Sort transactions
function sortTransactions(field) {
    if (currentSort.field === field) {
        currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
    } else {
        currentSort.field = field;
        currentSort.direction = 'desc';
    }

    filteredTransactions.sort((a, b) => {
        let aVal = a[field];
        let bVal = b[field];

        // Handle numeric fields
        if (field === 'timestamp') {
            aVal = Number(aVal);
            bVal = Number(bVal);
        }

        // Handle string fields
        if (typeof aVal === 'string') {
            aVal = aVal.toLowerCase();
            bVal = bVal.toLowerCase();
        }

        if (currentSort.direction === 'asc') {
            return aVal > bVal ? 1 : -1;
        } else {
            return aVal < bVal ? 1 : -1;
        }
    });

    renderTransactions();
}

// Render transactions table
function renderTransactions() {
    const totalPages = Math.ceil(filteredTransactions.length / ITEMS_PER_PAGE);
    const startIndex = (currentPage - 1) * ITEMS_PER_PAGE;
    const endIndex = startIndex + ITEMS_PER_PAGE;
    const pageTransactions = filteredTransactions.slice(startIndex, endIndex);

    filteredCount.textContent = filteredTransactions.length.toLocaleString();
    currentPageSpan.textContent = currentPage;
    totalPagesSpan.textContent = totalPages || 1;

    prevPageBtn.disabled = currentPage === 1;
    nextPageBtn.disabled = currentPage >= totalPages;

    if (filteredTransactions.length === 0) {
        transactionsSection.style.display = 'none';
        emptyState.style.display = 'block';
        return;
    }

    emptyState.style.display = 'none';
    transactionsSection.style.display = 'block';

    transactionsTBody.innerHTML = pageTransactions.map((tx, index) => `
        <tr>
            <td>${startIndex + index + 1}</td>
            <td>${tx.datetime}</td>
            <td>
                <div class="address-cell">
                    <span class="address" title="${tx.from}">${truncateAddress(tx.from)}</span>
                    <button class="copy-btn" onclick="copyToClipboard('${tx.from}')" title="Copy address">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                        </svg>
                    </button>
                </div>
            </td>
            <td>
                <div class="address-cell">
                    <span class="address" title="${tx.to}">${truncateAddress(tx.to)}</span>
                    <button class="copy-btn" onclick="copyToClipboard('${tx.to}')" title="Copy address">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                        </svg>
                    </button>
                </div>
            </td>
            <td><strong title="${tx.amount}">${formatAmount(tx.amount)}</strong></td>
            <td>${formatTokenSymbol(tx.token_symbol, tx.token_contract)}</td>
            <td><span class="badge badge-${tx.direction}">${tx.direction}</span></td>
            <td class="explorer-cell">
                <a href="${getExplorerUrl(tx)}" target="_blank" class="explorer-link" title="View on ${tx.network === 'ERC' ? 'Etherscan' : 'TronScan'}">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
                        <polyline points="15 3 21 3 21 9"></polyline>
                        <line x1="10" y1="14" x2="21" y2="3"></line>
                    </svg>
                </a>
            </td>
        </tr>
    `).join('');
}

// Copy to clipboard function
function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        // Show a brief success notification
        const notification = document.createElement('div');
        notification.className = 'copy-notification';
        notification.textContent = 'Address copied!';
        document.body.appendChild(notification);
        
        setTimeout(() => {
            notification.classList.add('show');
        }, 10);
        
        setTimeout(() => {
            notification.classList.remove('show');
            setTimeout(() => notification.remove(), 300);
        }, 2000);
    }).catch(err => {
        console.error('Failed to copy:', err);
    });
}

// Make it globally available for onclick handlers
window.copyToClipboard = copyToClipboard;

// Export to Excel (CSV format, all filtered records)
function exportToCSV() {
    const headers = [
        '#', 'Date & Time', 'From Address', 'To Address',
        'Amount', 'Token', 'Direction', 'Explorer'
    ];

    const rows = filteredTransactions.map((tx, index) => [
        index + 1,
        tx.datetime,
        tx.from,
        tx.to,
        tx.amount,
        tx.token_symbol,
        tx.direction,
        getExplorerUrl(tx)
    ]);

    let csv = headers.join(',') + '\n';
    csv += rows.map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(',')).join('\n');

    const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `transactions_${walletAddress}_${Date.now()}.csv`;
    a.click();
    window.URL.revokeObjectURL(url);
}

// Helper functions
function truncateHash(hash) {
    return `${hash.substring(0, 10)}...${hash.substring(hash.length - 8)}`;
}

function truncateAddress(address) {
    if (address.length <= 16) return address;
    return `${address.substring(0, 8)}...${address.substring(address.length - 6)}`;
}

// Convert integer to Unicode superscript string
function toSuperscript(n) {
    const map = '⁰¹²³⁴⁵⁶⁷⁸⁹';
    return String(n).split('').map(c => map[+c]).join('');
}

function formatAmount(amount) {
    const str = String(amount).trim();

    // Use raw string to avoid float precision loss for very large numbers
    const dotIdx = str.indexOf('.');
    const intPart = dotIdx === -1 ? str : str.slice(0, dotIdx);
    const digits = intPart.replace(/^-/, '');

    // More than 15 integer digits → beyond float precision, render as scientific notation
    if (digits.length > 15) {
        const exp = digits.length - 1;
        const sig = digits[0] + (digits.length > 1 ? '.' + digits.slice(1, 4) : '');
        const trimmed = sig.replace(/\.?0+$/, '');
        return `~${trimmed} × 10${toSuperscript(exp)}`;
    }

    const num = parseFloat(str);
    if (isNaN(num)) return amount;
    if (num === 0) return '0';
    if (num < 0.00000001) return num.toExponential(4);
    if (num < 0.01) return num.toFixed(8).replace(/\.?0+$/, '');
    if (num < 1) return num.toFixed(6).replace(/\.?0+$/, '');

    return num.toLocaleString(undefined, {
        minimumFractionDigits: 0,
        maximumFractionDigits: 6
    });
}

function formatTokenSymbol(symbol, contractAddress) {
    // List of well-known tokens for validation
    const knownTokens = ['ETH', 'USDT', 'USDC', 'DAI', 'WETH', 'WBTC', 'UNI', 'LINK', 'MATIC', 'TRX', 'SHIB', 'PEPE'];
    const isKnown = knownTokens.includes(symbol.toUpperCase());
    
    // If it's a known token, return normally
    if (isKnown || symbol === 'ETH' || symbol === 'TRX') {
        return `<span class=\"badge\" style=\"background: rgba(255,255,255,0.1);\">${symbol}</span>`;
    }
    
    // For unknown tokens, add a subtle indicator
    return `<span class=\"badge\" style=\"background: rgba(255,255,255,0.1);\" title=\"Token: ${symbol}\\nContract: ${contractAddress || 'N/A'}\\n\\nNote: This is the token symbol from the smart contract. Verify legitimacy before interacting.\">${symbol}</span>`;
}

function getExplorerUrl(tx) {
    if (tx.network === 'ERC') {
        return `https://etherscan.io/tx/${tx.hash}`;
    } else {
        return `https://tronscan.org/#/transaction/${tx.hash}`;
    }
}

function showLoading() {
    loadingState.style.display = 'block';
    summarySection.style.display = 'none';
    filtersSection.style.display = 'none';
    transactionsSection.style.display = 'none';
    errorState.style.display = 'none';
    emptyState.style.display = 'none';
}

function hideLoading() {
    loadingState.style.display = 'none';
}

function showContent() {
    summarySection.style.display = 'block';
    filtersSection.style.display = 'block';
    transactionsSection.style.display = 'block';
}

function showError(message) {
    hideLoading();
    errorState.style.display = 'block';
    document.getElementById('errorMessage').textContent = message;
}

// Event listeners

// Reset filters button
if (resetFiltersBtn) {
    resetFiltersBtn.addEventListener('click', () => {
        directionFilter && (directionFilter.value = '');
        if (counterpartyFilter) counterpartyFilter.value = '';
        if (counterpartySelected) counterpartySelected.textContent = 'All Addresses';
        if (counterpartySearch) counterpartySearch.value = '';
        renderCounterpartyOptions();
        applyFilters();
    });
}

if (headerBackBtn) {
    headerBackBtn.addEventListener('click', () => {
        window.location.href = '/';
    });
}

if (exportCsvBtn) exportCsvBtn.addEventListener('click', exportToCSV);

if (perPageSelect) {
    perPageSelect.addEventListener('change', () => {
        ITEMS_PER_PAGE = parseInt(perPageSelect.value, 10);
        currentPage = 1;
        renderTransactions();
    });
}

if (prevPageBtn) {
    prevPageBtn.addEventListener('click', () => {
        if (currentPage > 1) {
            currentPage--;
            renderTransactions();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }
    });
}

if (nextPageBtn) {
    nextPageBtn.addEventListener('click', () => {
        const totalPages = Math.ceil(filteredTransactions.length / ITEMS_PER_PAGE);
        if (currentPage < totalPages) {
            currentPage++;
            renderTransactions();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }
    });
}

// Sort table headers
document.querySelectorAll('th[data-sort]').forEach(th => {
    th.addEventListener('click', () => {
        const field = th.dataset.sort;
        sortTransactions(field);
    });
});

// Direction filter - auto-apply (client-side only)
if (directionFilter) directionFilter.addEventListener('change', applyFilters);

// Custom dropdown handlers
if (counterpartyTrigger) {
    counterpartyTrigger.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleCounterpartyDropdown();
    });
}

// Counterparty search - with safety check
if (counterpartySearch) {
    counterpartySearch.addEventListener('input', (e) => {
        renderCounterpartyOptions(e.target.value);
    });
    
    // Prevent dropdown from closing when clicking search input
    counterpartySearch.addEventListener('click', (e) => {
        e.stopPropagation();
    });
}

// Close dropdown when clicking outside
document.addEventListener('click', (e) => {
    if (counterpartyDropdown && !counterpartyDropdown.contains(e.target) && e.target !== counterpartyTrigger) {
        closeCounterpartyDropdown();
    }
});

// Initialize on load
init();
