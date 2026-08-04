from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
import json
from app.database import get_db
from app.schemas.analyzer import AnalyzeRequest, AnalyzeResponse
from app.services.analyzer import analyze_article
from app.models.models import Article, Source, SimilarityResult
from sqlalchemy.orm import joinedload

router = APIRouter(
    prefix="/api/analyze",
    tags=["Analyzer (AI)"],
    responses={404: {"description": "Not found"}},
)

@router.get("/articles", tags=["Articles"])
def get_all_articles(db: Session = Depends(get_db)): 
    articles = db.query(Article).options(joinedload(Article.source)).filter(Article.is_submitted == True).order_by(Article.created_at.desc(), Article.published_at.desc()).all()
    result = []
    for art in articles:
        result.append({
            "id": art.id,
            "title": art.title,
            "url": art.url,
            "published_at": art.published_at,
            "created_at": art.created_at,
            "author": art.author,
            "has_image": art.has_image,
            "source_name": art.source.name,
            "source_type": art.source.type,
            "status": art.status
        })
    return result
    
@router.get("/results/{article_id}", tags=["Analyzer (AI)"])
def get_analysis_results(article_id: int, db: Session = Depends(get_db)):
    results = db.query(SimilarityResult).filter(
        SimilarityResult.article_1_id == article_id
    ).order_by(SimilarityResult.similarity_score.desc()).all()

    report = []
    for r in results:
        official_art = db.query(Article).filter(Article.id == r.article_2_id).first()
        if official_art:
            report.append({
                "official_article_id": official_art.id,
                "official_title": official_art.title,
                "official_url": official_art.url,
                "official_published_at": official_art.published_at,
                "official_image_url": official_art.image_url,
                "similarity_score": r.similarity_score,
                "reasons": "\n".join(json.loads(r.reasons)) if r.reasons else None
            }) 
    return report
                                                                      
@router.post("", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest, db: Session = Depends(get_db)):
    try:
        result = analyze_article(db, request.article_id, request.top_n)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Terjadi kesalahan saat analisis: {str(e)}")


@router.patch("/status/{article_id}", tags=["Articles"])
def update_article_status(article_id: int, status: str, db: Session = Depends(get_db)):
    """
    Memperbarui status kepatuhan berita (patuh, pelanggaran, pending).
    """
    if status not in ["pending", "patuh", "pelanggaran"]:
        raise HTTPException(status_code=400, detail="Status tidak valid. Gunakan 'pending', 'patuh', atau 'pelanggaran'.")
    
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="Artikel tidak ditemukan.")
    
    article.status = status
    db.commit()
    return {"status": "success", "message": f"Status artikel berhasil diubah menjadi {status}"}

class MarkSubmittedRequest(BaseModel):
    article_ids: list[int]

@router.post("/mark-submitted", tags=["Articles"])
def mark_submitted(request: MarkSubmittedRequest, db: Session = Depends(get_db)):
    db.query(Article).filter(Article.id.in_(request.article_ids)).update({"is_submitted": True}, synchronize_session=False)
    db.commit()
    return {"status": "success", "message": f"{len(request.article_ids)} articles marked as submitted"}
