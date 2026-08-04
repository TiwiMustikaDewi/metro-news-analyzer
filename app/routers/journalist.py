import random
import string
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.models.models import Journalist, Article, Source, SimilarityResult
from app.schemas.journalist import (
    JournalistRegister, JournalistResponse, PortfolioResponse,
    PortfolioArticle, SubmitArticleRequest, AdminVerifyRequest
)
from app.services.scraper import scrape_single_article
from app.services.analyzer import analyze_article

router = APIRouter(
    prefix="/api/journalist",
    tags=["Journalist (Wartawan)"],
    responses={404: {"description": "Not found"}},
)


def _generate_ticket_number() -> str:
    """Menghasilkan nomor tiket unik 8 karakter (huruf besar + angka)."""
    chars = string.ascii_uppercase + string.digits
    return "MN-" + "".join(random.choices(chars, k=6))


# ============================================================
# REGISTRASI WARTAWAN
# ============================================================
@router.post("/register", response_model=JournalistResponse)
def register_journalist(data: JournalistRegister, db: Session = Depends(get_db)):
    """Mendaftarkan wartawan baru dan menghasilkan nomor tiket unik."""
    # Cek apakah email sudah terdaftar
    existing = db.query(Journalist).filter(Journalist.email == data.email).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Email sudah terdaftar dengan nomor tiket: {existing.ticket_number}"
        )

    # Buat nomor tiket unik
    ticket = _generate_ticket_number()
    while db.query(Journalist).filter(Journalist.ticket_number == ticket).first():
        ticket = _generate_ticket_number()

    journalist = Journalist(
        name=data.name,
        media_name=data.media_name,
        email=data.email,
        phone=data.phone,
        ticket_number=ticket,
        status="pending"
    )
    db.add(journalist)
    db.commit()
    db.refresh(journalist)

    return JournalistResponse(
        id=journalist.id,
        name=journalist.name,
        media_name=journalist.media_name,
        email=journalist.email,
        phone=journalist.phone,
        ticket_number=journalist.ticket_number,
        status=journalist.status,
        created_at=journalist.created_at
    )


# ============================================================
# SUBMIT BERITA (Wartawan submit URL berita untuk dianalisis)
# ============================================================
@router.post("/submit-article")
def submit_article(data: SubmitArticleRequest, db: Session = Depends(get_db)):
    """Wartawan mengirimkan URL berita. Sistem akan scrape, simpan, dan analisis otomatis."""
    # Validasi nomor tiket
    journalist = db.query(Journalist).filter(
        Journalist.ticket_number == data.ticket_number
    ).first()
    if not journalist:
        raise HTTPException(status_code=404, detail="Nomor tiket tidak ditemukan.")

    # Pastikan ada source 'media' untuk berita swasta
    media_source = db.query(Source).filter(Source.type == "media").first()
    if not media_source:
        media_source = Source(
            name="Media Swasta",
            type="media",
            url=""
        )
        db.add(media_source)
        db.commit()
        db.refresh(media_source)

    # Scrape berita
    scrape_result = scrape_single_article(db, data.url, media_source.id)

    if scrape_result.status == "success":
        # Hubungkan artikel dengan wartawan
        article = db.query(Article).filter(Article.url == data.url).first()
        if article:
            article.journalist_id = journalist.id
            db.commit()

            # Jalankan analisis AI otomatis
            try:
                analysis = analyze_article(db, article.id, top_n=3)
                top_score = analysis.results[0].text_similarity_score if analysis.results else 0
                return {
                    "status": "success",
                    "message": "Berita berhasil di-submit dan dianalisis.",
                    "article_id": article.id,
                    "article_title": article.title,
                    "similarity_score": top_score,
                    "is_passing": top_score < 40.0
                }
            except Exception as e:
                return {
                    "status": "success",
                    "message": f"Berita tersimpan, tapi analisis gagal: {str(e)}",
                    "article_id": article.id,
                    "article_title": article.title,
                    "similarity_score": None,
                    "is_passing": None
                }
    elif scrape_result.error_message == "Article already exists in database":
        # Jika sudah ada, hubungkan ke wartawan jika belum
        article = db.query(Article).filter(Article.url == data.url).first()
        if article and not article.journalist_id:
            article.journalist_id = journalist.id
            db.commit()
        if article:
            # Ambil skor kemiripan yang sudah ada
            sim = db.query(SimilarityResult).filter(
                SimilarityResult.article_1_id == article.id
            ).order_by(SimilarityResult.similarity_score.desc()).first()
            top_score = sim.similarity_score if sim else 0
            return {
                "status": "success",
                "message": "Berita sudah ada di database dan telah dihubungkan ke akun Anda.",
                "article_id": article.id,
                "article_title": article.title,
                "similarity_score": top_score,
                "is_passing": top_score < 40.0
            }

    raise HTTPException(
        status_code=400,
        detail=f"Gagal mengambil berita: {scrape_result.error_message}"
    )


# ============================================================
# PORTOFOLIO WARTAWAN (10 berita terakhir dalam 1 bulan)
# ============================================================
@router.get("/{ticket}/portfolio", response_model=PortfolioResponse)
def get_portfolio(ticket: str, db: Session = Depends(get_db)):
    """Mengambil portofolio 10 berita terakhir wartawan dalam 1 bulan terakhir."""
    journalist = db.query(Journalist).filter(
        Journalist.ticket_number == ticket
    ).first()
    if not journalist:
        raise HTTPException(status_code=404, detail="Nomor tiket tidak ditemukan.")

    # Ambil berita wartawan dalam 1 bulan terakhir, maksimal 10
    one_month_ago = datetime.utcnow() - timedelta(days=30)
    articles = (
        db.query(Article)
        .filter(
            Article.journalist_id == journalist.id,
            Article.created_at >= one_month_ago
        )
        .order_by(Article.created_at.desc())
        .limit(10)
        .all()
    )

    portfolio_articles = []
    total_passing = 0

    for art in articles:
        # Ambil skor kemiripan tertinggi dari tabel similarity_results
        top_sim = db.query(SimilarityResult).filter(
            SimilarityResult.article_1_id == art.id
        ).order_by(SimilarityResult.similarity_score.desc()).first()

        score = top_sim.similarity_score if top_sim else 0.0
        is_passing = score < 40.0
        if is_passing:
            total_passing += 1

        # Ambil info artikel resmi yang paling mirip
        matched_title = None
        matched_url = None
        snippets = []
        if top_sim:
            matched_art = db.query(Article).filter(
                Article.id == top_sim.article_2_id
            ).first()
            if matched_art:
                matched_title = matched_art.title
                matched_url = matched_art.url

        portfolio_articles.append(PortfolioArticle(
            id=art.id,
            title=art.title,
            url=art.url,
            published_at=art.published_at,
            author=art.author,
            similarity_score=score,
            matched_article_title=matched_title,
            matched_article_url=matched_url,
            matching_snippets=snippets,
            is_passing=is_passing
        ))

    is_complete = len(portfolio_articles) >= 10
    is_all_passing = is_complete and total_passing >= 10

    return PortfolioResponse(
        journalist=JournalistResponse(
            id=journalist.id,
            name=journalist.name,
            media_name=journalist.media_name,
            email=journalist.email,
            phone=journalist.phone,
            ticket_number=journalist.ticket_number,
            status=journalist.status,
            admin_notes=journalist.admin_notes,
            created_at=journalist.created_at
        ),
        articles=portfolio_articles,
        total_articles=len(portfolio_articles),
        total_passing=total_passing,
        is_portfolio_complete=is_complete,
        is_all_passing=is_all_passing
    )


# ============================================================
# CEK STATUS VERIFIKASI
# ============================================================
@router.get("/{ticket}/status")
def get_status(ticket: str, db: Session = Depends(get_db)):
    """Mengecek status verifikasi wartawan berdasarkan nomor tiket."""
    journalist = db.query(Journalist).filter(
        Journalist.ticket_number == ticket
    ).first()
    if not journalist:
        raise HTTPException(status_code=404, detail="Nomor tiket tidak ditemukan.")

    return {
        "name": journalist.name,
        "media_name": journalist.media_name,
        "ticket_number": journalist.ticket_number,
        "status": journalist.status,
        "admin_notes": journalist.admin_notes,
        "created_at": journalist.created_at
    }


# ============================================================
# ADMIN: LIST SEMUA WARTAWAN
# ============================================================
@router.get("/admin/list")
def admin_list_journalists(db: Session = Depends(get_db)):
    """[Admin] Mengambil daftar seluruh wartawan beserta ringkasan portofolionya."""
    journalists = db.query(Journalist).order_by(Journalist.created_at.desc()).all()

    result = []
    for j in journalists:
        # Hitung jumlah berita dan yang lolos
        one_month_ago = datetime.utcnow() - timedelta(days=30)
        articles = (
            db.query(Article)
            .filter(
                Article.journalist_id == j.id,
                Article.created_at >= one_month_ago
            )
            .limit(10)
            .all()
        )

        total_articles = len(articles)
        total_passing = 0
        for art in articles:
            top_sim = db.query(SimilarityResult).filter(
                SimilarityResult.article_1_id == art.id
            ).order_by(SimilarityResult.similarity_score.desc()).first()
            score = top_sim.similarity_score if top_sim else 0.0
            if score < 40.0:
                total_passing += 1

        result.append({
            "id": j.id,
            "name": j.name,
            "media_name": j.media_name,
            "email": j.email,
            "ticket_number": j.ticket_number,
            "status": j.status,
            "admin_notes": j.admin_notes,
            "total_articles": total_articles,
            "total_passing": total_passing,
            "is_portfolio_complete": total_articles >= 10,
            "is_all_passing": total_articles >= 10 and total_passing >= 10,
            "created_at": j.created_at
        })

    return result


# ============================================================
# ADMIN: VERIFIKASI / TOLAK WARTAWAN
# ============================================================
@router.patch("/admin/{journalist_id}/verify")
def admin_verify_journalist(
    journalist_id: int,
    data: AdminVerifyRequest,
    db: Session = Depends(get_db)
):
    """[Admin] Memutuskan verifikasi wartawan: verified atau rejected."""
    if data.status not in ["verified", "rejected"]:
        raise HTTPException(
            status_code=400,
            detail="Status harus 'verified' atau 'rejected'."
        )

    journalist = db.query(Journalist).filter(Journalist.id == journalist_id).first()
    if not journalist:
        raise HTTPException(status_code=404, detail="Wartawan tidak ditemukan.")

    journalist.status = data.status
    journalist.admin_notes = data.admin_notes
    db.commit()

    return {
        "status": "success",
        "message": f"Status wartawan {journalist.name} berhasil diubah menjadi '{data.status}'."
    }
