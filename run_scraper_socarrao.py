#!/usr/bin/env python3
"""Run the SóCarrão car scraper with Playwright browser and save listings to SQLite."""
import os
import sys
from datetime import datetime, timezone

from scraper.spiders.socarrao_spider import SocarraoSpider, DatabasePipeline
from models import init_db, get_session, CarListing

start_time = datetime.now(timezone.utc)

init_db()

session = get_session()
before_ids = {
    r[0] for r in
    session.query(CarListing.olx_id)
    .filter(CarListing.status == "active", CarListing.olx_id.isnot(None), CarListing.source == "socarrao")
    .all()
}
session.close()

pipeline = DatabasePipeline()
print("Iniciando scraper SóCarrão...")
spider = SocarraoSpider()

found_ids = set()
for item in spider.start_requests():
    pipeline.process_item(item, None)
    oid = item.get("olx_id")
    if oid:
        found_ids.add(oid)

deleted = before_ids - found_ids
if deleted:
    s2 = get_session()
    s2.query(CarListing).filter(CarListing.olx_id.in_(deleted), CarListing.olx_id.isnot(None), CarListing.source == "socarrao").update({"status": "deleted"}, synchronize_session=False)
    s2.commit()
    s2.close()
    print(f"  Marcados {len(deleted)} como excluídos (não encontrados)")

elapsed = round((datetime.now(timezone.utc) - start_time).total_seconds())
_m = elapsed // 60
_s = elapsed % 60
print(f"\nConcluído! ({_m:02d}:{_s:02d})")
