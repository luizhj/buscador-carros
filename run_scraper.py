#!/usr/bin/env python3
"""Run the OLX car scraper and save listings to SQLite."""
import os
from scraper.spiders.olx_spider import OlxSpider, DatabasePipeline
from models import init_db
from config import START_URL

# se SCRAPE_URL foi passada via env, sobrescreve START_URL
url = os.environ.get("SCRAPE_URL") or START_URL
# se .current_url existir, usa ele (sincroniza com a web)
_current_file = os.path.join(os.path.dirname(__file__), ".current_url")
if os.path.exists(_current_file):
    with open(_current_file) as _f:
        _u = _f.read().strip()
        if _u:
            url = _u

init_db()
pipeline = DatabasePipeline()
pipeline.open_spider(None)

spider = OlxSpider(start_url=url)
seen = 0

for item in spider.start_requests():
    pipeline.process_item(item, None)
    seen += 1
    if seen % 5 == 0:
        print(f"  Scraped {seen} items...")

print(f"\nDone! Scraped {seen} listings total.")
