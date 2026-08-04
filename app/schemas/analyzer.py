from pydantic import BaseModel
from typing import List, Optional, Any
from datetime import datetime


class TextSimilarityResult(BaseModel):
    article_id: int
    article_title: str
    article_url: Optional[str] = None
    published_at: Optional[datetime] = None
    text_similarity_score: float  # 0.0 - 100.0 (skor akhir multi-dimensi)
    image_url: Optional[str] = None
    image_similarity_score: Optional[float] = None
    image_is_identical: Optional[bool] = None
    matching_snippets: List[str] = []  # Deskripsi pasangan kalimat yang mirip
    reasons: List[str] = []  # Alasan kenapa skornya tinggi/rendah
    matched_pairs: List[Any] = []  # Data spesifik pasangan kalimat untuk frontend


class AnalyzeRequest(BaseModel):
    article_id: int
    top_n: int = 5


class AnalyzeResponse(BaseModel):
    target_article_id: int
    target_article_title: str
    target_article_url: Optional[str] = None
    target_article_author: Optional[str] = None
    target_article_published_at: Optional[datetime] = None
    target_source_name: Optional[str] = None
    total_compared: int
    results: List[TextSimilarityResult]
    _debug_details: Optional[List[Any]] = None  # Metadata detail per match untuk frontend

    class Config:
        # Izinkan field dengan underscore prefix
        populate_by_name = True
