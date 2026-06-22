#!/usr/bin/env python3
"""Run the OLX car scraper and save listings to SQLite."""
import os
import time
from datetime import datetime, timezone
from scraper.spiders.olx_spider import OlxSpider, DatabasePipeline
from models import init_db, get_session, CarListing
from config import START_URL

_start_time = time.time()
url = os.environ.get("SCRAPE_URL") or START_URL
_current_file = os.path.join(os.path.dirname(__file__), ".current_url")
if os.path.exists(_current_file):
    with open(_current_file) as _f:
        _u = _f.read().strip()
        if _u:
            url = _u

init_db()

# coleta ids ativos antes do scrape (apenas OLX)
session = get_session()
before_ids = {
    r[0] for r in
    session.query(CarListing.olx_id)
    .filter(
        CarListing.status == "active",
        CarListing.olx_id.isnot(None),
        (CarListing.source == "olx") | (CarListing.source.is_(None)),
    )
    .all()
}
session.close()

pipeline = DatabasePipeline()
pipeline.open_spider(None)

spider = OlxSpider(start_url=url)
seen = 0
found_ids = set()

for item in spider.start_requests():
    pipeline.process_item(item, None)
    oid = item.get("olx_id")
    if oid:
        found_ids.add(oid)
    seen += 1
    if seen % 5 == 0:
        print(f"  Scraped {seen} items...")

# marca como deleted os que estavam ativos e não foram encontrados
deleted = before_ids - found_ids
if deleted:
    session2 = get_session()
    session2.query(CarListing).filter(
        CarListing.olx_id.in_(deleted),
        CarListing.olx_id.isnot(None),
        (CarListing.source == "olx") | (CarListing.source.is_(None)),
    ).update({"status": "deleted"}, synchronize_session=False)
    session2.commit()
    session2.close()
    print(f"  Marcados {len(deleted)} como excluídos (não encontrados)")

_elapsed = round(time.time() - _start_time)
_m = _elapsed // 60
_s = _elapsed % 60
print(f"\nDone! Scraped {seen} listings total. ({_m:02d}:{_s:02d})")

# salva resultado para exibição na web
import json
result = {"count": seen, "finished_at": datetime.now().astimezone().isoformat(), "elapsed": _elapsed}
with open(os.path.join(os.path.dirname(__file__), ".last_scrape.json"), "w") as f:
    json.dump(result, f)
