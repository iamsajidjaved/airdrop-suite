# frontend/CLAUDE.md

Vanilla HTML / CSS / JS. **No build step, no framework, no npm.** FastAPI serves these files directly.

## Layout

```
frontend/
├── index.html          wallet input page (entry point at "/")
├── explorer.html       transaction dashboard (served at "/explorer")
├── admin.html          scanner admin — iGaming brands, qualifying transactions (served at "/admin/scanner")
├── distribution.html   airdrop campaigns admin (served at "/admin/airdrop")
├── settings.html       settings — tokens, thresholds, distribution wallets (served at "/admin/settings")
├── css/
│   ├── styles.css      shared styles, dark theme
│   └── admin.css       admin-specific overrides
└── js/
    ├── wallet.js       used by index.html — input validation + redirect to /explorer
    ├── explorer.js     used by explorer.html — fetch, filter, paginate, CSV export
    ├── admin.js        used by admin.html — scanner status, iGaming brands CRUD, transactions
    ├── distribution.js used by distribution.html — campaign + send management
    └── settings.js     used by settings.html — token CRUD, threshold, wallets
```

JS files map 1:1 to pages. Pages reference assets at `/static/...` (mount defined in `backend/main.py`).

## How serving works

- Page routes (`/`, `/explorer`, `/admin/scanner`, etc.) return the corresponding HTML file via `FileResponse`. See `backend/main.py`.
- Everything else (`/static/css/...`, `/static/js/...`) is served by FastAPI's `StaticFiles` mount.
- A middleware adds `Cache-Control: no-store` to every `/static/*` response (`backend/main.py:58-66`). **This means you don't need to cache-bust during development** — every refresh fetches fresh JS/CSS. If you change something and don't see it, it's not a cache problem; check the network tab and console.

## State patterns

- **Cross-page state → URL query params.** `index.html` redirects to `/explorer?address=...&from_date=...&to_date=...`; `explorer.js` reads them on load.
- **Transient state → `sessionStorage`.** Used in a few places to remember filter selections within a tab.
- **No global store, no framework.** Each page's JS file owns its DOM and its `fetch` calls.

## API base

All `fetch` calls use relative paths (`/api/...`). The frontend and backend are served from the same origin in dev and prod, so no CORS concerns at runtime (the `*` CORS policy in `backend/main.py` is permissive for local debugging — tighten it before deploying publicly).

## Key functions in admin.js

| Function | What it does |
| --- | --- |
| `loadNetworks()` | Fetches `GET /api/airdrop/networks` and populates the network selector |
| `loadConfig()` | Fetches `GET /api/airdrop/config` and syncs `active_network` to the UI select |
| `loadStatus()` | Fetches `GET /api/airdrop/status`, renders per-token blocks, per-brand blocks, mode breakdown |
| `loadBrands()` | Fetches `GET /api/airdrop/brands`, renders the iGaming brands table |
| `openBrandModal(brand?)` | Opens the add/edit brand modal (null = add mode) |
| `loadTokens()` | Fetches `GET /api/airdrop/tokens`, updates KPI strip and token filter dropdown |
| `loadTx()` | Fetches `GET /api/airdrop/transactions` with current filter/page state |
| `modeBadge(mode, brandName)` | Returns a colored HTML badge for "Standard" or "iGaming · BrandName" |
| `window.editBrand(id)` | Table row action — opens edit modal pre-populated |
| `window.deleteBrand(id, name)` | Table row action — confirms then calls `DELETE /api/airdrop/brands/{id}` |

Network change saves to DB: `PUT /api/airdrop/config { active_network: newNet }`, then reloads status + transactions. Scan mode dropdown (Standard / iGaming / Both) is passed as `?scan_mode=` on the `POST /api/airdrop/monitor/run` call.

## Editing

1. Edit the HTML/CSS/JS file.
2. Refresh the browser. That's it.

If the dev server is running with `--reload`, only Python changes restart it; static files are live with no restart needed.

## Adding a new page

1. Create `frontend/<name>.html`. Reference its assets as `/static/css/...` and `/static/js/...`.
2. Add a route in `backend/main.py` that returns `FileResponse(frontend_path / "<name>.html")`.
3. If you need page-specific JS, add `frontend/js/<name>.js` and a `<script src="/static/js/<name>.js">` tag.
4. Add a sidebar link in the relevant HTML files (all pages share the same sidebar markup).

## Things not to do

- Don't introduce a build tool, bundler, or framework without explicit ask. The whole point of the frontend is that there's nothing to build.
- Don't import npm packages — there's no `package.json` and no node setup. Use a CDN if you genuinely need a library, but prefer not to.
- Don't manually version asset URLs (`?v=2`). The no-cache middleware already solves that.
