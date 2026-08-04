import requests
import cloudscraper
from bs4 import BeautifulSoup
import trafilatura
from sqlalchemy.orm import Session
from datetime import datetime
from urllib.parse import urlparse, urljoin
import socket
import ipaddress
from app.models.models import Article
from app.schemas.scraper import ScrapeResult

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7',
}

def is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False
        # Resolve to IP
        ip = socket.gethostbyname(hostname)
        ip_obj = ipaddress.ip_address(ip)
        if ip_obj.is_private or ip_obj.is_loopback:
            return False
        return True
    except Exception:
        return False

def scrape_single_article(db: Session, url: str, source_id: int) -> ScrapeResult:
    try:
        url = url.strip()

        if not is_safe_url(url):
            return ScrapeResult(url=url, status="failed", error_message="URL tidak valid atau tidak diizinkan demi keamanan.")

        # 1. Cek apakah artikel sudah ada di Database -> Jika ada, gunakan yang sudah tersimpan!
        existing = db.query(Article).filter(Article.url == url).first()
        if existing:
            return ScrapeResult(
                url=url,
                status="success",
                article_title=existing.title,
                image_url=existing.image_url,
                fetch_method="existing"
            )

        # 2. Ambil isi HTML berita menggunakan cloudscraper untuk bypass Anti-Bot/WAF
        html_content = None
        fetch_method = "cloudscraper"
        try:
            scraper = cloudscraper.create_scraper(browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            })
            res = scraper.get(url, headers=HEADERS, timeout=15)
            if res.status_code == 200:
                html_content = res.text
        except Exception as e:
            logger = __import__('logging').getLogger(__name__)
            logger.warning(f"Cloudscraper failed for {url}: {e}")

        # Fallback to headless browser if Cloudscraper did not retrieve content
        if not html_content:
            fetch_method = "selenium"
            logger = __import__('logging').getLogger(__name__)
            logger.info(f"Falling back to Selenium browser for {url}")
            from app.services.browser import fetch_with_browser
            html_content = fetch_with_browser(url, timeout=15)
        
        logger = __import__('logging').getLogger(__name__)
        logger.info(f"Fetched content using {fetch_method} for {url}")

        # Jika cloudscraper gagal, langsung kembalikan error (jangan gunakan trafilatura.fetch_url karena sering hang di Windows tanpa timeout)
        if not html_content:
            return ScrapeResult(url=url, status="failed", error_message="Tidak dapat mengunduh konten dari URL yang diberikan. Akses diblokir oleh situs.")

        # 3. Ekstrak data teks & metadata dari HTML
        extracted = trafilatura.extract(
            html_content,
            include_images=True,
            output_format="json",
            with_metadata=True
        )

        title = "Berita Tanpa Judul"
        content = ""
        author = None
        date_str = None
        image_url = None

        if extracted:
            import json
            data = json.loads(extracted)
            title = data.get('title') or title
            content = data.get('raw_text', '') or data.get('text', '')
            author = data.get('author')
            date_str = data.get('date')
            image_url = data.get('image')

        # If extracted content is too short, attempt a second fallback with Selenium (maybe JS rendered)
        if not content or len(content.strip()) < 200:
            logger = __import__('logging').getLogger(__name__)
            logger.info(f"Content too short after extraction, retrying Selenium for {url}")
            from app.services.browser import fetch_with_browser
            html_content = fetch_with_browser(url, timeout=20)
            # Re-run extraction on the new HTML
            extracted = trafilatura.extract(
                html_content,
                include_images=True,
                output_format="json",
                with_metadata=True
            )
            if extracted:
                import json
                data = json.loads(extracted)
                title = data.get('title') or title
                content = data.get('raw_text', '') or data.get('text', '')
                author = data.get('author')
                date_str = data.get('date')
                image_url = data.get('image')
            # If still insufficient, let the later short‑content guard handle the error

        # Fallback ekstraksi judul & konten via BeautifulSoup jika trafilatura parsial
        if not content or len(content.strip()) < 50:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Cari Judul
            if title == "Berita Tanpa Judul" or not title:
                h1_tag = soup.find('h1')
                if h1_tag:
                    title = h1_tag.get_text().strip()
                elif soup.title:
                    title = soup.title.get_text().strip()

            # Cari Paragraf Konten Berita
            paragraphs = [p.get_text().strip() for p in soup.find_all('p') if len(p.get_text().strip()) > 20]
            content = "\n\n".join(paragraphs)

        if not content or len(content.strip()) < 50:
            return ScrapeResult(url=url, status="failed", error_message="Konten berita terlalu singkat atau tidak dapat dibaca dari halaman web tersebut.")

        published_at = None
        if date_str:
            try:
                published_at = datetime.fromisoformat(date_str)
            except ValueError:
                pass

        # 4. Simpan Artikel Baru ke PostgreSQL Database
        db_article = Article(
            source_id=source_id,
            title=title,
            content=content,
            url=url,
            author=author,
            published_at=published_at,
            has_image=bool(image_url),
            image_url=image_url
        )
        db.add(db_article)
        db.commit()
        db.refresh(db_article)

        return ScrapeResult(url=url, status="success", article_title=title, image_url=image_url)

    except Exception as e:
        return ScrapeResult(url=url, status="failed", error_message=f"Kesalahan sistem scraper: {str(e)}")

def scrape_batch_articles(db: Session, index_url: str, source_id: int, max_articles: int = 10) -> list[ScrapeResult]:
    try:
        scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
        response = scraper.get(index_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        article_links = set()
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            if href.startswith('/'):
                href = urljoin(index_url, href)
            
            if index_url in href or href.startswith('http'):
                if not any(x in href.lower() for x in ['#', '/category/', '/tag/', '/author/', '/page/']):
                    if len(href) > len(index_url) + 10:
                        article_links.add(href)

        article_links = list(article_links)[:max_articles]
        
        results = []
        for link in article_links:
            res = scrape_single_article(db, link, source_id)
            results.append(res)
            
        return results

    except Exception as e:
        return [ScrapeResult(url=index_url, status="failed", error_message=f"Gagal mengambil index berita: {str(e)}")]
