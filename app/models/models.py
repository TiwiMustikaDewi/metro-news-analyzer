from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, DateTime, Float
from sqlalchemy.orm import relationship
from datetime import datetime
from .base import Base


class Journalist(Base):
    __tablename__ = "journalists"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    media_name = Column(String(150), nullable=False)
    email = Column(String(150), unique=True, nullable=False)
    phone = Column(String(30), nullable=True)
    ticket_number = Column(String(20), unique=True, nullable=False)
    status = Column(String(50), default="pending")  # pending, verified, rejected
    admin_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    articles = relationship("Article", back_populates="journalist", cascade="all, delete-orphan")


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    type = Column(String(50), nullable=False)  # e.g., 'official', 'media'
    url = Column(String(255), nullable=True)

    # Relationships
    articles = relationship("Article", back_populates="source", cascade="all, delete-orphan")


class Article(Base):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    journalist_id = Column(Integer, ForeignKey("journalists.id"), nullable=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    url = Column(String(255), nullable=True)
    published_at = Column(DateTime, nullable=True)
    author = Column(String(100), nullable=True)
    has_image = Column(Boolean, default=False)
    image_url = Column(String(500), nullable=True)
    image_credits = Column(String(255), nullable=True)
    status = Column(String(50), default="pending")  # pending, patuh, pelanggaran
    is_submitted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    source = relationship("Source", back_populates="articles")
    journalist = relationship("Journalist", back_populates="articles")


class SimilarityResult(Base):
    __tablename__ = "similarity_results"

    id = Column(Integer, primary_key=True, index=True)
    article_1_id = Column(Integer, ForeignKey("articles.id"), nullable=False)
    article_2_id = Column(Integer, ForeignKey("articles.id"), nullable=False)
    similarity_score = Column(Float, nullable=False)  # 0.0 to 100.0
    reasons = Column(Text, nullable=True)             # JSON string array of reasons
    analyzed_at = Column(DateTime, default=datetime.utcnow)

