from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os
from app.routers.scraper import router as scraper_router
from app.routers.analyzer import router as analyzer_router
from app.routers.journalist import router as journalist_router
from app.scheduler import start_scheduler

app = FastAPI(
    title="Metro News Similarity Analyzer - AI Engine",
    description="REST API untuk scraping berita dan analisis kemiripan AI. Dikonsumsi oleh Laravel web app.",
    version="2.0.0",
)

# CORS: Izinkan Laravel (port 8080) dan browser lokal memanggil API ini
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://127.0.0.1:8080", "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# API Key Verification Middleware
@app.middleware("http")
async def verify_api_key(request: Request, call_next):
    # Exclude root and docs
    if request.url.path in ["/", "/docs", "/openapi.json"]:
        return await call_next(request)
        
    api_key = request.headers.get("X-API-Key")
    expected_api_key = os.getenv("FASTAPI_API_KEY")
    
    if not expected_api_key:
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error: FASTAPI_API_KEY is not configured in .env"})
    
    if api_key != expected_api_key:
        return JSONResponse(status_code=403, content={"detail": "Unauthorized: Invalid API Key"})
        
    response = await call_next(request)
    return response

app.include_router(scraper_router)
app.include_router(analyzer_router)
app.include_router(journalist_router)


@app.on_event("startup")
def on_startup():
    """
    Menjalankan scheduler otomatis saat aplikasi menyala.
    """
    start_scheduler()



@app.get("/health")
def health_check():
    return {
        "status": "healthy",
    }
