import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

logger = logging.getLogger(__name__)

def fetch_with_browser(url: str, timeout: int = 15) -> str | None:
    """Fetch a page using a headless Chrome browser.

    Returns the page source HTML if successful, otherwise ``None``.
    """
    try:
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        # Mimic a real browser User‑Agent
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.set_page_load_timeout(timeout)
        driver.get(url)
        html = driver.page_source
        return html
    except Exception as exc:
        logger.warning("Browser fetch failed for %s: %s", url, exc)
        return None
    finally:
        try:
            driver.quit()
        except Exception:
            pass
