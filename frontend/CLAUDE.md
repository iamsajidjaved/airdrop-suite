# frontend/CLAUDE.md

Vanilla HTML / CSS / JS. **No build step, no framework, no npm.** FastAPI serves these files directly.

## Layout

```
frontend/
├── index.html       wallet input page (entry point at "/")
├── explorer.html    transaction dashboard (served at "/explorer")
├── admin.html       airdrop admin (served at "/admin/airdrop")
├── css/
│   ├── styles.css   shared styles, dark theme
│   └── admin.css    admin-specific overrides
└── js/
    ├── wallet.js    used by index.html — input validation + redirect to /explorer
    ├── explorer.js  used by explorer.html — fetch, filter, paginate, CSV export
    └── admin.js     used by admin.html — token CRUD, threshold config, monitor trigger
```

JS files map 1:1 to pages. Pages reference assets at `/static/...` (mount defined in `backend/main.py:50-53`).

## How serving works

- Page routes (`/`, `/explorer`, `/admin/airdrop`) return the corresponding HTML file via `FileResponse`. See `backend/main.py:56-80`.
- Everything else (`/static/css/...`, `/static/js/...`) is served by FastAPI's `StaticFiles` mount.
- A middleware adds `Cache-Control: no-store` to every `/static/*` response (`backend/main.py:41-48`). **This means you don't need to cache-bust during development** — every refresh fetches fresh JS/CSS. If you change something and don't see it, it's not a cache problem; check the network tab and console.

## State patterns

- **Cross-page state → URL query params.** `index.html` redirects to `/explorer?address=...&from_date=...&to_date=...`; `explorer.js` reads them on load.
- **Transient state → `sessionStorage`.** Used in a few places to remember filter selections within a tab.
- **No global store, no framework.** Each page's JS file owns its DOM and its `fetch` calls.

## API base

All `fetch` calls use relative paths (`/api/...`). The frontend and backend are served from the same origin in dev and prod, so no CORS concerns at runtime (the `*` CORS policy in `backend/main.py:30` is permissive for local debugging — see DEPLOYMENT.md before tightening it).

## Editing

1. Edit the HTML/CSS/JS file.
2. Refresh the browser. That's it.

If the dev server is running with `--reload`, only Python changes restart it; static files are live with no restart needed.

## Adding a new page

1. Create `frontend/<name>.html`. Reference its assets as `/static/css/...` and `/static/js/...`.
2. Add a route in `backend/main.py` that returns `FileResponse(frontend_path / "<name>.html")` (mirror the `/explorer` and `/admin/airdrop` handlers).
3. If you need page-specific JS, add `frontend/js/<name>.js` and a `<script src="/static/js/<name>.js">` tag.

## Things not to do

- Don't introduce a build tool, bundler, or framework without explicit ask. The whole point of the frontend is that there's nothing to build.
- Don't import npm packages — there's no `package.json` and no node setup. Use a CDN if you genuinely need a library, but prefer not to.
- Don't manually version asset URLs (`?v=2`). The no-cache middleware already solves that.
