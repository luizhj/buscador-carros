#!/usr/bin/env python3
"""Run the SóCarrão car scraper with Playwright browser and save listings to SQLite."""
import os
import sys
from datetime import datetime, timezone

from scraper.spiders.socarrao_spider import SocarraoSpider, DatabasePipeline
from models import init_db

start_time = datetime.now(timezone.utc)

init_db()

pipeline = DatabasePipeline()
print("Iniciando scraper SóCarrão...")
spider = SocarraoSpider()

for item in spider.start_requests():
    pipeline.process_item(item, None)

elapsed = round((datetime.now(timezone.utc) - start_time).total_seconds())
_m = elapsed // 60
_s = elapsed % 60
print(f"\nConcluído! ({_m:02d}:{_s:02d})")
