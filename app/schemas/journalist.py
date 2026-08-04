from pydantic import BaseModel, EmailStr
from typing import List, Optional
from datetime import datetime


class JournalistRegister(BaseModel):
    name: str
    media_name: str
    email: str
    phone: Optional[str] = None


class JournalistResponse(BaseModel):
    id: int
    name: str
    media_name: str
    email: str
    phone: Optional[str] = None
    ticket_number: str
    status: str
    admin_notes: Optional[str] = None
    created_at: Optional[datetime] = None


class PortfolioArticle(BaseModel):
    id: int
    title: str
    url: Optional[str] = None
    published_at: Optional[datetime] = None
    author: Optional[str] = None
    similarity_score: Optional[float] = None  # Skor kemiripan tertinggi terhadap berita resmi
    matched_article_title: Optional[str] = None
    matched_article_url: Optional[str] = None
    matching_snippets: List[str] = []
    is_passing: bool = True  # True jika skor < 40%


class PortfolioResponse(BaseModel):
    journalist: JournalistResponse
    articles: List[PortfolioArticle]
    total_articles: int
    total_passing: int  # Jumlah berita yang lolos (skor < 40%)
    is_portfolio_complete: bool  # True jika sudah ada 10 berita
    is_all_passing: bool  # True jika semua 10 berita lolos


class SubmitArticleRequest(BaseModel):
    ticket_number: str
    url: str


class AdminVerifyRequest(BaseModel):
    status: str  # verified atau rejected
    admin_notes: Optional[str] = None
