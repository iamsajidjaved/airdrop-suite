"""FastAPI application entry point"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from backend.routers import transactions, airdrop, distribution
from backend.config import settings
from backend.services.distribution_worker import worker as distribution_worker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The airdrop Scanner runs ONLY when the operator clicks
    # "Run Scan" in the admin UI (POST /api/airdrop/monitor/run). No
    # background loop, cron, or scheduled task is started on boot.
    # Distribution worker is opt-in (default off). Start only when explicitly enabled.
    if settings.distribution_worker_enabled:
        distribution_worker.start()
    try:
        yield
    finally:
        await distribution_worker.stop()


# Create FastAPI app
app = FastAPI(
    title="Wallet Explorer API",
    description="Multi-Network Wallet Transaction Explorer (ERC + TRC)",
    version="1.0.0",
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(transactions.router)
app.include_router(airdrop.router)
app.include_router(distribution.router)

# Middleware: disable caching on all static JS/CSS so browsers always fetch fresh files
@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# Mount static files
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")


@app.get("/")
async def root():
    """Serve the main wallet input page"""
    index_path = frontend_path / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "Wallet Explorer API", "docs": "/docs"}


@app.get("/explorer")
async def explorer():
    """Serve the explorer dashboard page"""
    explorer_path = frontend_path / "explorer.html"
    if explorer_path.exists():
        return FileResponse(explorer_path)
    return {"message": "Explorer page not found"}


@app.get("/admin/airdrop")
async def admin_airdrop():
    """Serve the airdrop admin page"""
    admin_path = frontend_path / "admin.html"
    if admin_path.exists():
        return FileResponse(admin_path)
    return {"message": "Admin page not found"}


@app.get("/admin/distribution")
async def admin_distribution():
    """Serve the distribution admin page"""
    page = frontend_path / "distribution.html"
    if page.exists():
        return FileResponse(page)
    return {"message": "Distribution admin page not found"}


@app.get("/admin/settings")
async def admin_settings():
    """Serve the consolidated settings page"""
    page = frontend_path / "settings.html"
    if page.exists():
        return FileResponse(page)
    return {"message": "Settings page not found"}


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "Wallet Explorer API",
        "version": "1.0.0"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=True
    )
