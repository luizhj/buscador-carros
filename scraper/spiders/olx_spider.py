import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

import cloudscraper  # bypasses Cloudflare (Scrapy's Twisted client is blocked)
import scrapy

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from config import START_URL, START_PAGE, MAX_PAGES
from models import CarListing, IgnoredListing, BlacklistRule, Brand, get_session, init_db


def _cidades_permitidas():
    path = os.path.join(_project_root, "cidades_permitidas.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _compound_models():
    path = os.path.join(_project_root, "models_compostos.json")
    try:
        with open(path) as f:
            return {(e["first"], e["second"]): e["model"] for e in json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

class CarItem(scrapy.Item):
    olx_id = scrapy.Field()
    title = scrapy.Field()
    price = scrapy.Field()
    year = scrapy.Field()
    mileage = scrapy.Field()
    fuel = scrapy.Field()
    transmission = scrapy.Field()
    color = scrapy.Field()
    cartype = scrapy.Field()
    motorpower = scrapy.Field()
    neighborhood = scrapy.Field()
    zip_code = scrapy.Field()
    seller_name = scrapy.Field()
    seller_type = scrapy.Field()
    brand = scrapy.Field()
    model = scrapy.Field()
    city = scrapy.Field()
    state = scrapy.Field()
    description = scrapy.Field()
    image_urls = scrapy.Field()
    listing_url = scrapy.Field()
    listing_date = scrapy.Field()
    car_steering = scrapy.Field()
    car_features = scrapy.Field()


class DatabasePipeline:
    def open_spider(self, spider):
        init_db()

    def process_item(self, item, spider):
        olx_id = item.get("olx_id")
        if not olx_id:
            return item
        session = get_session()
        try:
            if session.get(IgnoredListing, olx_id):
                return item
            existing = session.query(CarListing).filter_by(olx_id=olx_id).first()
            if existing:
                edited_fields = json.loads(existing.edited) if existing.edited else []
                for key in item.fields:
                    if key in edited_fields:
                        continue
                    val = item.get(key)
                    if val is not None:
                        setattr(existing, key, val)
                existing.updated_at = datetime.now(timezone.utc)
            else:
                session.add(CarListing(**{k: v for k, v in item.items() if v is not None}))
            session.commit()
        finally:
            session.close()
        return item


class OlxSpider(scrapy.Spider):
    """OLX car listings spider.

    Uses cloudscraper instead of Scrapy's async HTTP client because OLX
    uses Cloudflare protection that blocks Scrapy's Twisted-based engine.
    The spider is a generator-based design (start_requests yields items
    synchronously), so it runs outside Scrapy's CrawlerProcess.
    Use run_scraper.py to execute.
    """
    name = "olx"
    custom_settings = {
        "ITEM_PIPELINES": {"scraper.spiders.olx_spider.DatabasePipeline": 300},
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http = cloudscraper.create_scraper()
        self._delay = 1.5
        self.max_pages = kwargs.get("max_pages") or MAX_PAGES
        custom_url = kwargs.get("start_url") or START_URL
        self._start_url = custom_url if START_PAGE <= 1 else custom_url + "&o=" + str(START_PAGE)
        self._blacklist = []
        session = get_session()
        try:
            self._blacklist = session.query(BlacklistRule).all()
        finally:
            session.close()

    def start_requests(self):
        url = self._start_url
        page = 0

        while url:
            page += 1
            if self.max_pages and page > self.max_pages:
                break
            print(f"  Page {page} — fetching...")
            resp = self._http.get(url, timeout=30)
            if resp.status_code != 200:
                print(f"  ERROR: Listing page returned {resp.status_code}")
                break

            items = self._parse_listing(resp.text, url)
            for item in items:
                yield item

            url = self._next_listing_url(url, resp.text)
            if url:
                print(f"  Next page → sleeping {self._delay:.1f}s...")
                time.sleep(self._delay)

    def _parse_listing(self, html, current_url):
        data = self._parse_next_data(html)
        if not data:
            self.logger.warning("No __NEXT_DATA__ found on listing page")
            return []

        props = data.get("props", {}).get("pageProps", {})
        ads = props.get("ads", [])
        if not hasattr(self, "_total_printed"):
            total = props.get("totalOfAds", 0)
            if total:
                print(f"Total de anúncios: {total}")
            self._save_brands(props)
            self._total_printed = True

        for ad in ads:
            title = (ad.get("subject") or "")
            lower = title.lower()
            if "*" in title or "retirada de peça" in lower or "entrada" in lower or "parcelas" in lower or "sucata" in lower:
                continue
            item = self._item_from_listing_data(ad)
            if self._is_blacklisted(item):
                continue
            allowed = _cidades_permitidas()
            if allowed and item.get("city") and item["city"] not in allowed:
                continue
            yield item

    def _next_listing_url(self, current_url, html):
        data = self._parse_next_data(html)
        if not data:
            return None
        props = data.get("props", {}).get("pageProps", {})
        page_index = props.get("pageIndex", 1)
        total_ads = props.get("totalOfAds", 0)
        page_size = props.get("pageSize", 50)
        if not total_ads:
            return None
        total_pages = (int(total_ads) + int(page_size) - 1) // int(page_size)
        if page_index >= total_pages:
            return None
        parsed = urlparse(current_url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        qs["o"] = [str(page_index + 1)]
        return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))

    def _item_from_listing_data(self, ad):
        props = {}
        for p in ad.get("properties", []):
            props[p["name"]] = p["value"]

        price = self._parse_price(ad.get("priceValue") or ad.get("price", ""))

        images = ad.get("images", [])
        image_urls = [img["original"] for img in images if img.get("original")] if images else None

        listing_date = None
        raw_date = ad.get("date")
        if raw_date:
            try:
                listing_date = datetime.fromtimestamp(int(raw_date), tz=timezone.utc).isoformat()
            except (ValueError, OSError):
                pass

        seller_name = None
        olx_pay = ad.get("olxPay") or {}
        if olx_pay.get("transactionalSellerName"):
            seller_name = olx_pay["transactionalSellerName"]

        loc = ad.get("locationDetails") or {}
        city = loc.get("municipality")
        state = loc.get("uf")
        neighborhood = loc.get("neighbourhood")

        title = ad.get("subject", "").strip()
        parts = title.split()
        brand = props.get("vehicle_brand") or (parts[0] if parts else None)
        model = None
        if len(parts) > 1:
            # descobre onde começa o modelo (pula palavras extras da marca)
            idx = 1
            brand_words = brand.lower().split() if brand else []
            for i in range(len(parts)):
                match_len = len(brand_words)
                if i + match_len <= len(parts):
                    chunk = " ".join(parts[i:i+match_len]).lower()
                    if chunk == brand.lower():
                        idx = i + match_len
                        break
            _suffixes = {"motors", "do", "das", "dos", "da"}
            while idx < len(parts) and parts[idx].lower() in _suffixes:
                idx += 1
            if idx < len(parts):
                raw = parts[idx:]
                compound = _compound_models()
                if len(raw) > 1 and (raw[0], raw[1]) in compound:
                    model = compound[(raw[0], raw[1])]
                else:
                    model = raw[0]

        return CarItem(
            olx_id=str(ad.get("listId", "")),
            title=title,
            brand=brand,
            model=model,
            price=price,
            year=self._int(props.get("regdate")),
            mileage=self._int(props.get("mileage")),
            fuel=props.get("fuel"),
            transmission=props.get("gearbox"),
            color=props.get("carcolor"),
            cartype=props.get("cartype"),
            motorpower=props.get("motorpower"),
            neighborhood=neighborhood,
            zip_code=props.get("cep"),
            seller_name=seller_name,
            seller_type="profissional" if ad.get("professionalAd") else "particular",
            city=city,
            state=state,
            image_urls=json.dumps(image_urls) if image_urls else None,
            listing_url=ad.get("url", ""),
            listing_date=listing_date,
            car_steering=props.get("car_steering"),
            car_features=json.dumps([f.strip() for f in props.get("car_features", "").split(",") if f.strip()]) if props.get("car_features") else None,
        )

    def _is_blacklisted(self, item):
        for rule in self._blacklist:
            if rule.brand and item.get("brand") != rule.brand:
                continue
            if rule.model and item.get("model") != rule.model:
                continue
            if rule.motorpower and item.get("motorpower") != rule.motorpower:
                continue
            if rule.transmission and item.get("transmission") != rule.transmission:
                continue
            return True
        return False

    def _save_brands(self, props):
        ft = props.get("filtersTemplate", {})
        for tmpl in ft.get("template", []):
            if tmpl.get("id") == "brands":
                session = get_session()
                try:
                    server_values = []
                    for comp in tmpl.get("props", {}).get("components", []):
                        ds = comp.get("props", {}).get("datasource", {})
                        server_values.extend(ds.get("serverValues", []))
                    count = 0
                    for sv in server_values:
                        oid = sv.get("value")
                        if oid and oid.isdigit():
                            existing = session.get(Brand, int(oid))
                            if not existing:
                                session.add(Brand(
                                    olx_id=int(oid),
                                    name=sv.get("label", ""),
                                    slug=sv.get("extraData", {}).get("friendlyPath"),
                                ))
                                count += 1
                    session.commit()
                    print(f"  Marcas salvas: {len(server_values)} marcas ({count} novas)")
                finally:
                    session.close()

    def _parse_price(self, raw):
        m = re.search(r"[\d.]+", raw.replace(" ", ""))
        if m:
            clean = m.group(0).replace(".", "")
            try:
                return int(clean) * 100
            except ValueError:
                return None
        return None

    def _parse_next_data(self, html):
        from parsel import Selector
        sel = Selector(text=html)
        text = sel.css("script#__NEXT_DATA__::text").get()
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def _int(val):
        if not val:
            return None
        digits = re.sub(r"\D", "", str(val))
        return int(digits) if digits else None
