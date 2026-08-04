from pydantic import BaseModel, HttpUrl
from typing import List, Optional
from datetime import datetime

class ScrapeSingleRequest(BaseModel):
    url: HttpUrl
    source_id: int

class ScrapeBatchRequest(BaseModel):
    index_url: HttpUrl
    source_id: int
    max_articles: int = 10

class ScrapeResult(BaseModel):
    url: str
    status: str # 'success' or 'failed'
    article_title: Optional[str] = None
    image_url: Optional[str] = None
    error_message: Optional[str] = None

class ScrapeBatchResponse(BaseModel):
    total_found: int
    total_scraped: int
    results: List[ScrapeResult]
