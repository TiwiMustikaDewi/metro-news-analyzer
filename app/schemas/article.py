from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class ArticleBase(BaseModel):
    source_id: int
    title: str
    content: str
    url: Optional[str] = None
    author: Optional[str] = None
    has_image: bool = False
    image_url: Optional[str] = None
    image_credits: Optional[str] = None
    published_at: Optional[datetime] = None

class ArticleCreate(ArticleBase):
    pass

class Article(ArticleBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
