"""
LinkVault API - Professional Link Intelligence Platform
Extracts rich metadata, Open Graph data, Twitter Cards, structured data,
and performance insights from any URL.

Version: 1.0.0
Author: Your Name
"""

import re
import time
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urljoin
from collections import defaultdict
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Request, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("linkvault")

# ─────────────────────────────────────────────
# App Initialization
# ─────────────────────────────────────────────
app = FastAPI(
    title="LinkVault API",
    description=(
        "## 🔗 LinkVault — Professional Link Intelligence API\n\n"
        "Extract rich metadata, Open Graph tags, Twitter Cards, structured data, "
        "favicons, theme colors, and performance insights from any URL with a "
        "single API call.\n\n"
        "### Perfect For:\n"
        "- **Chat & Social Apps** — Show beautiful link preview cards\n"
        "- **SEO & Marketing Tools** — Audit how your links appear when shared\n"
        "- **Content Aggregators** — Auto-fetch titles, images & descriptions\n"
        "- **E-commerce** — Pull product images and descriptions from competitor URLs\n\n"
        "### Features:\n"
        "✅ Open Graph & Twitter Card extraction  \n"
        "✅ Full favicon resolution  \n"
        "✅ Theme color detection  \n"
        "✅ Language & charset detection  \n"
        "✅ Canonical URL resolution  \n"
        "✅ Robots & crawl policy reading  \n"
        "✅ Response time measurement  \n"
        "✅ In-memory caching (5-minute TTL)  \n"
        "✅ Rate limiting built-in  \n"
    ),
    version="1.0.0",
    contact={
        "name": "LinkVault Support",
        "url": "https://your-site.com/support",
        "email": "support@your-site.com",
    },
    license_info={
        "name": "Commercial License",
        "url": "https://your-site.com/terms",
    },
)

# ─────────────────────────────────────────────
# CORS Middleware (allows browser calls)
# ─────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
# Simple In-Memory Cache (5-minute TTL)
# ─────────────────────────────────────────────
cache: dict = {}
CACHE_TTL_SECONDS = 300  # 5 minutes

def get_cache_key(url: str) -> str:
    return hashlib.md5(url.lower().encode()).hexdigest()

def get_cached(url: str):
    key = get_cache_key(url)
    if key in cache:
        entry = cache[key]
        if time.time() < entry["expires"]:
            return entry["data"]
        else:
            del cache[key]
    return None

def set_cache(url: str, data: dict):
    key = get_cache_key(url)
    cache[key] = {"data": data, "expires": time.time() + CACHE_TTL_SECONDS}

# ─────────────────────────────────────────────
# Simple In-Memory Rate Limiter
# Per IP: 30 requests per minute
# ─────────────────────────────────────────────
rate_limit_store: dict = defaultdict(list)
RATE_LIMIT_MAX = 30
RATE_LIMIT_WINDOW = 60  # seconds

def check_rate_limit(ip: str) -> bool:
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    # Clean old entries
    rate_limit_store[ip] = [t for t in rate_limit_store[ip] if t > window_start]
    if len(rate_limit_store[ip]) >= RATE_LIMIT_MAX:
        return False
    rate_limit_store[ip].append(now)
    return True

# ─────────────────────────────────────────────
# Request Middleware — Rate Limiting + Timing
# ─────────────────────────────────────────────
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host
    if not check_rate_limit(client_ip):
        return JSONResponse(
            status_code=429,
            content={
                "success": False,
                "error": {
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": "You have exceeded the rate limit of 30 requests per minute.",
                    "retry_after_seconds": 60,
                },
            },
        )
    start_time = time.time()
    response = await call_next(request)
    process_time = round((time.time() - start_time) * 1000, 2)
    response.headers["X-Process-Time-Ms"] = str(process_time)
    response.headers["X-API-Version"] = "1.0.0"
    response.headers["X-Powered-By"] = "LinkVault"
    return response

# ─────────────────────────────────────────────
# Shared HTTP Client Config
# ─────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36 LinkVaultBot/1.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ─────────────────────────────────────────────
# Core Scraping Logic
# ─────────────────────────────────────────────
def extract_favicon(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    """Extract the best quality favicon from the page."""
    # Priority: apple-touch-icon > 32x32 icon > shortcut icon > /favicon.ico
    for rel in ["apple-touch-icon", "apple-touch-icon-precomposed"]:
        tag = soup.find("link", rel=lambda r: r and rel in r)
        if tag and tag.get("href"):
            return urljoin(base_url, tag["href"])
    for tag in soup.find_all("link", rel=lambda r: r and "icon" in r):
        sizes = tag.get("sizes", "")
        if "32x32" in sizes or "64x64" in sizes or "96x96" in sizes:
            href = tag.get("href")
            if href:
                return urljoin(base_url, href)
    tag = soup.find("link", rel="shortcut icon") or soup.find("link", rel="icon")
    if tag and tag.get("href"):
        return urljoin(base_url, tag["href"])
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}/favicon.ico"


def extract_theme_color(soup: BeautifulSoup) -> Optional[str]:
    tag = soup.find("meta", attrs={"name": "theme-color"})
    if tag:
        return tag.get("content")
    return None


def extract_open_graph(soup: BeautifulSoup) -> dict:
    og = {}
    for tag in soup.find_all("meta"):
        prop = tag.get("property", "") or tag.get("name", "")
        if prop.startswith("og:") or prop.startswith("twitter:"):
            content = tag.get("content")
            if content:
                og[prop] = content
    return og


def extract_structured_data(soup: BeautifulSoup) -> list:
    """Extract JSON-LD structured data blocks."""
    import json
    results = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            results.append(data)
        except Exception:
            pass
    return results


def clean_description(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500] if len(text) > 500 else text


def scrape_url(url: str) -> dict:
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    fetch_start = time.time()
    with httpx.Client(timeout=10, follow_redirects=True, headers=HEADERS) as client:
        response = client.get(url)
    fetch_time_ms = round((time.time() - fetch_start) * 1000, 2)

    final_url = str(response.url)
    status_code = response.status_code
    content_type = response.headers.get("content-type", "")

    soup = BeautifulSoup(response.text, "html.parser")
    og = extract_open_graph(soup)

    # Title — prioritize OG > Twitter > <title>
    title = (
        og.get("og:title")
        or og.get("twitter:title")
        or (soup.title.string.strip() if soup.title and soup.title.string else None)
    )

    # Description — prioritize OG > Twitter > meta description
    description_tag = soup.find("meta", attrs={"name": "description"})
    description = clean_description(
        og.get("og:description")
        or og.get("twitter:description")
        or (description_tag.get("content") if description_tag else None)
    )

    # Image
    image = og.get("og:image") or og.get("twitter:image")
    if image and not image.startswith("http"):
        image = urljoin(base_url, image)

    # Canonical URL
    canonical_tag = soup.find("link", rel="canonical")
    canonical_url = canonical_tag.get("href") if canonical_tag else final_url

    # Language
    html_tag = soup.find("html")
    language = html_tag.get("lang") if html_tag else None

    # Charset
    charset_tag = soup.find("meta", charset=True)
    charset = charset_tag.get("charset") if charset_tag else "UTF-8"

    # Keywords
    keywords_tag = soup.find("meta", attrs={"name": "keywords"})
    keywords_raw = keywords_tag.get("content") if keywords_tag else None
    keywords = [k.strip() for k in keywords_raw.split(",")] if keywords_raw else []

    # Robots
    robots_tag = soup.find("meta", attrs={"name": "robots"})
    robots = robots_tag.get("content") if robots_tag else None

    # Author
    author_tag = soup.find("meta", attrs={"name": "author"})
    author = author_tag.get("content") if author_tag else og.get("article:author")

    # Published time
    pub_time_tag = soup.find("meta", attrs={"property": "article:published_time"})
    pub_time = pub_time_tag.get("content") if pub_time_tag else og.get("article:published_time")

    # Site name
    site_name = og.get("og:site_name") or parsed.netloc

    # Content type (article, video, website, etc.)
    og_type = og.get("og:type", "website")

    favicon = extract_favicon(soup, base_url)
    theme_color = extract_theme_color(soup)
    structured_data = extract_structured_data(soup)

    return {
        "url": url,
        "final_url": final_url,
        "canonical_url": canonical_url,
        "domain": parsed.netloc,
        "site_name": site_name,
        "title": title,
        "description": description,
        "image": image,
        "favicon": favicon,
        "type": og_type,
        "language": language,
        "charset": charset,
        "theme_color": theme_color,
        "author": author,
        "published_time": pub_time,
        "keywords": keywords,
        "robots": robots,
        "open_graph": og,
        "structured_data": structured_data if structured_data else None,
        "http": {
            "status_code": status_code,
            "content_type": content_type,
            "fetch_time_ms": fetch_time_ms,
            "final_url_redirected": final_url != url,
        },
    }

# ─────────────────────────────────────────────
# Response Models (for documentation clarity)
# ─────────────────────────────────────────────
class ErrorResponse(BaseModel):
    success: bool = False
    error: dict

# ─────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────

@app.get(
    "/",
    summary="Health Check",
    description="Returns API status and version info. Use this to verify the API is running.",
    tags=["Status"],
)
def root():
    return {
        "success": True,
        "name": "LinkVault API",
        "version": "1.0.0",
        "status": "operational",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "docs": "/docs",
        "endpoints": {
            "preview": "/preview?url=https://example.com",
            "batch": "/batch?urls=https://example.com,https://github.com",
            "validate": "/validate?url=https://example.com",
        },
    }


@app.get(
    "/preview",
    summary="Extract Link Preview Metadata",
    description=(
        "Fetches a URL and returns rich metadata including title, description, "
        "image, favicon, Open Graph tags, Twitter Card data, structured data "
        "(JSON-LD), theme color, language, author, and HTTP performance metrics.\n\n"
        "Results are cached for **5 minutes** to ensure fast repeated lookups. "
        "Use `force_refresh=true` to bypass the cache."
    ),
    tags=["Core"],
    responses={
        200: {"description": "Metadata extracted successfully"},
        400: {"description": "Invalid or missing URL"},
        422: {"description": "URL could not be fetched"},
        429: {"description": "Rate limit exceeded"},
    },
)
def get_preview(
    url: str = Query(..., description="The full URL to extract metadata from. Must include http:// or https://", example="https://github.com"),
    force_refresh: bool = Query(False, description="Set to true to bypass the 5-minute cache and fetch fresh data"),
):
    # Basic URL validation
    if not url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_URL",
                "message": "URL must start with http:// or https://",
            },
        )

    # Check cache
    if not force_refresh:
        cached = get_cached(url)
        if cached:
            cached["cache"] = {"hit": True, "ttl_seconds": CACHE_TTL_SECONDS}
            return {"success": True, **cached}

    # Scrape
    try:
        data = scrape_url(url)
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "TIMEOUT",
                "message": "The target URL did not respond within 10 seconds.",
            },
        )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "FETCH_ERROR",
                "message": f"Could not reach the URL: {str(e)}",
            },
        )
    except Exception as e:
        logger.exception("Unexpected error during scrape")
        raise HTTPException(
            status_code=500,
            detail={
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred. Please try again.",
            },
        )

    set_cache(url, data)
    data["cache"] = {"hit": False, "ttl_seconds": CACHE_TTL_SECONDS}
    return {"success": True, **data}


@app.get(
    "/batch",
    summary="Batch Extract — Multiple URLs at Once",
    description=(
        "Extract metadata from **up to 5 URLs** in a single API call. "
        "Each URL is processed individually and returned as a list. "
        "Failed URLs are gracefully included with an error field rather than failing the whole request.\n\n"
        "Pass URLs as a comma-separated string: `?urls=https://a.com,https://b.com`"
    ),
    tags=["Core"],
)
def get_batch_preview(
    urls: str = Query(..., description="Comma-separated list of URLs (max 5)", example="https://github.com,https://apple.com"),
):
    url_list = [u.strip() for u in urls.split(",") if u.strip()]

    if len(url_list) == 0:
        raise HTTPException(status_code=400, detail={"code": "NO_URLS", "message": "Please provide at least one URL."})

    if len(url_list) > 5:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "TOO_MANY_URLS",
                "message": "Batch endpoint supports a maximum of 5 URLs per request.",
            },
        )

    results = []
    for url in url_list:
        if not url.startswith(("http://", "https://")):
            results.append({"url": url, "success": False, "error": {"code": "INVALID_URL", "message": "URL must start with http:// or https://"}})
            continue
        cached = get_cached(url)
        if cached:
            results.append({"success": True, "cache": {"hit": True}, **cached})
            continue
        try:
            data = scrape_url(url)
            set_cache(url, data)
            data["cache"] = {"hit": False}
            results.append({"success": True, **data})
        except Exception as e:
            results.append({"url": url, "success": False, "error": {"code": "FETCH_ERROR", "message": str(e)}})

    return {
        "success": True,
        "count": len(results),
        "results": results,
    }


@app.get(
    "/validate",
    summary="Validate URL — Check Reachability & Redirects",
    description=(
        "Performs a lightweight HEAD request on a URL to check if it is reachable, "
        "what status code it returns, and whether it redirects. "
        "Does **not** parse full metadata — use `/preview` for that.\n\n"
        "Ideal for validating user-submitted links in real time."
    ),
    tags=["Utilities"],
)
def validate_url(
    url: str = Query(..., description="The URL to validate", example="https://github.com"),
):
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail={"code": "INVALID_URL", "message": "URL must start with http:// or https://"})

    try:
        start = time.time()
        with httpx.Client(timeout=8, follow_redirects=True, headers=HEADERS) as client:
            response = client.head(url)
        elapsed = round((time.time() - start) * 1000, 2)

        return {
            "success": True,
            "url": url,
            "final_url": str(response.url),
            "reachable": True,
            "redirected": str(response.url) != url,
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type"),
            "response_time_ms": elapsed,
            "server": response.headers.get("server"),
        }
    except httpx.TimeoutException:
        return {"success": True, "url": url, "reachable": False, "reason": "timeout"}
    except httpx.RequestError as e:
        return {"success": True, "url": url, "reachable": False, "reason": str(e)}
