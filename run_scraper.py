#!/usr/bin/env python3
"""Run the OLX car scraper and save listings to SQLite."""
from scraper.spiders.olx_spider import OlxSpider, DatabasePipeline
from models import init_db

init_db()
pipeline = DatabasePipeline()
pipeline.open_spider(None)

spider = OlxSpider(max_pages=1)
seen = 0

for item in spider.start_requests():
    pipeline.process_item(item, None)
    seen += 1
    if seen % 5 == 0:
        print(f"  Scraped {seen} items...")

print(f"\nDone! Scraped {seen} listings total.")
