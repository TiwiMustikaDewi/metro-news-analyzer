import time
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.models import Source
from app.services.scraper import scrape_batch_articles

def auto_scrape_job():
    """
    Tugas otomatis yang berjalan di latar belakang untuk melakukan scraping berita resmi.
    """
    print("[Scheduler] Memulai scraping otomatis harian...")
    db: Session = SessionLocal()
    try:
        # Ambil semua sumber bertipe 'official' (sumber berita resmi)
        official_sources = db.query(Source).filter(Source.type == "official").all()
        
        if not official_sources:
            print("[Scheduler] Tidak ada sumber berita resmi aktif untuk di-scrape.")
            return

        for source in official_sources:
            print(f"[Scheduler] Menjalankan scraping untuk: {source.name} ({source.url})")
            # Sedot maksimal 10 artikel terbaru dari web resmi
            results = scrape_batch_articles(db, source.url, source.id, max_articles=10)
            
            success_count = sum(1 for r in results if r.status == 'success')
            print(f"[Scheduler] Selesai scraping untuk {source.name}. Berhasil menyedot {success_count} berita baru.")
            
    except Exception as e:
        print(f"[Scheduler] Terjadi kesalahan saat scraping otomatis: {str(e)}")
    finally:
        db.close()

# Inisialisasi scheduler
scheduler = BackgroundScheduler()

def start_scheduler():
    db = SessionLocal()
    try:
        media_source = db.query(Source).filter(Source.type == "media").first()
        if not media_source:
            media_source = Source(id=2, name="Media Swasta", type="media", url="")
            db.add(media_source)
            db.commit()
            print("[Scheduler] Source 'Media Swasta' berhasil dibuat.")
    finally:
        db.close()

    if not scheduler.running:
        # 1. Berjalan otomatis setiap hari pukul 00:00 (Tengah Malam)
        scheduler.add_job(auto_scrape_job, 'cron', hour=0, minute=0, id='daily_news_scrape')
        
        # 2. Berjalan setiap 5 menit
        scheduler.add_job(auto_scrape_job, 'interval', minutes=5, id='demo_news_scrape')
        
        scheduler.start()
        print("[Scheduler] Penjadwal otomatis latar belakang berhasil diaktifkan!")
