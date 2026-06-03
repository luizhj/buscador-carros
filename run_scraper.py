#!/usr/bin/env python3
"""Run the OLX car scraper and save listings to SQLite.

The spider uses cloudscraper (not Scrapy's async engine) to bypass
Cloudflare. So we iterate start_requests() directly instead of
using CrawlerProcess.
"""
from scraper.spiders.olx_spider import OlxSpider, DatabasePipeline
from models import init_db

init_db()
pipeline = DatabasePipeline()
pipeline.open_spider(None)

spider = OlxSpider()  # sem max_pages = vai até o fim
seen = 0

for item in spider.start_requests():
    pipeline.process_item(item, None)
    seen += 1
    if seen % 5 == 0:
        print(f"  Scraped {seen} items...")

print(f"\nDone! Scraped {seen} listings total.")
