from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.database import get_db
from app.schemas.scraper import ScrapeSingleRequest, ScrapeBatchRequest, ScrapeResult, ScrapeBatchResponse
from app.services.scraper import scrape_single_article, scrape_batch_articles
from app.services.analyzer import analyze_article, check_clickbait
from app.models.models import Article

router = APIRouter(
    prefix="/api/scraper",
    tags=["Scraper"],
    responses={404: {"description": "Not found"}},
)

@router.post("/scrape-single", response_model=ScrapeResult)
def scrape_single(request: ScrapeSingleRequest, db: Session = Depends(get_db)):
    """
    Scrape a single news article from a given URL and save it to the database.
    """
    result = scrape_single_article(db, str(request.url), request.source_id)
    return result

@router.post("/scrape-batch", response_model=ScrapeBatchResponse)
def scrape_batch(request: ScrapeBatchRequest, db: Session = Depends(get_db)):
    """
    Scrape multiple news articles from an index/homepage URL.
    """
    results = scrape_batch_articles(db, str(request.index_url), request.source_id, request.max_articles)
    
    success_count = sum(1 for r in results if r.status == 'success')
    
    return ScrapeBatchResponse(
        total_found=len(results),
        total_scraped=success_count,
        results=results
    )


class ScrapeAnalyzeRequest(BaseModel):
    url: str
    source_id: int = 2  # Default: Media Swasta
    top_n: int = 3


@router.post("/scrape-and-analyze")
def scrape_and_analyze(request: ScrapeAnalyzeRequest, db: Session = Depends(get_db)):
    """
    Endpoint gabungan untuk Laravel: scrape artikel, simpan ke DB, lalu langsung analisis AI.
    Mengembalikan metadata artikel + skor kemiripan tertinggi terhadap berita resmi.
    Digunakan oleh:
    - Laravel: saat wartawan submit berita portofolio
    - Laravel: saat publik/tamu cek orisinalitas berita
    """
    # 1. Scrape artikel
    scrape_result = scrape_single_article(db, request.url, request.source_id)

    # Ambil article_id dari DB (baik yang baru disimpan maupun yang sudah ada)
    article = db.query(Article).filter(Article.url == request.url).first()
    if not article:
        return {
            "status": "failed",
            "error": scrape_result.error_message or "Gagal mengambil artikel.",
            "article": None,
            "analysis": None
        }

    # Pastikan artikel ditandai sebagai submitted agar tampil di pengawasan admin
    if not article.is_submitted:
        article.is_submitted = True
        db.commit()

    # 2. Analisis AI dan Clickbait
    try:
        is_clickbait = check_clickbait(article.title, article.content)
        analysis = analyze_article(db, article.id, top_n=request.top_n)
        top_score = analysis.results[0].text_similarity_score if analysis.results else 0.0
        top_match = analysis.results[0] if analysis.results else None

        # Ambil debug details (matched_pairs + skor breakdown) jika tersedia
        debug_map = {}
        if hasattr(analysis, '_debug_details') and analysis._debug_details:
            for d in analysis._debug_details:
                debug_map[d["article_id"]] = d

        return {
            "status": "success",
            "article": {
                "id": article.id,
                "title": article.title,
                "url": article.url,
                "author": article.author,
                "published_at": article.published_at.isoformat() if article.published_at else None,
                "source_name": article.source.name if article.source else "Media Swasta",
                "image_url": article.image_url,
                "is_clickbait": is_clickbait,
            },
            "analysis": {
                "top_similarity_score": top_score,
                "is_passing": top_score < 40.0,
                "total_compared": analysis.total_compared,
                "top_matches": [
                    {
                        "article_id": r.article_id,
                        "title": r.article_title,
                        "url": r.article_url,
                        "published_at": r.published_at.isoformat() if r.published_at else None,
                        "text_similarity_score": r.text_similarity_score,
                        "image_similarity_score": r.image_similarity_score,
                        "image_is_identical": r.image_is_identical,
                        "matching_snippets": r.matching_snippets,
                        "reasons": r.reasons,
                        # Detail breakdown skor multi-dimensi
                        "score_breakdown": debug_map.get(r.article_id, {}).get("doc_score") and {
                            "doc_score": debug_map[r.article_id]["doc_score"],
                            "sentence_score": debug_map[r.article_id]["sentence_score"],
                            "time_penalty": debug_map[r.article_id]["time_penalty"],
                            "final_score": debug_map[r.article_id]["final_score"],
                        } or None,
                        # Pasangan kalimat yang mirip (untuk ditampilkan di UI)
                        "matched_pairs": r.matched_pairs,
                    }
                    for r in analysis.results
                ]
            }
        }
    except Exception as e:
        return {
            "status": "partial",
            "error": f"Artikel berhasil disimpan tapi analisis gagal: {str(e)}",
            "article": {
                "id": article.id,
                "title": article.title,
                "url": article.url,
                "author": article.author,
                "published_at": article.published_at.isoformat() if article.published_at else None,
                "is_clickbait": False,
            },
            "analysis": None
        }
