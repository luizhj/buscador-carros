import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

import scrapy

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from models import CarListing, get_session, init_db

SPIDER_MODULES = ["spiders"]


class CarItem(scrapy.Item):
    olx_id = scrapy.Field()
    title = scrapy.Field()
    price = scrapy.Field()
    year = scrapy.Field()
    mileage = scrapy.Field()
    fuel = scrapy.Field()
    transmission = scrapy.Field()
    color = scrapy.Field()
    neighborhood = scrapy.Field()
    zip_code = scrapy.Field()
    seller_name = scrapy.Field()
    city = scrapy.Field()
    state = scrapy.Field()
    description = scrapy.Field()
    image_urls = scrapy.Field()
    listing_url = scrapy.Field()
    listing_date = scrapy.Field()


class DatabasePipeline:
    def open_spider(self, spider):
        init_db()

    def process_item(self, item, spider):
        session = get_session()
        try:
            existing = (
                session.query(CarListing)
                .filter_by(olx_id=item.get("olx_id"))
                .first()
            )
            if existing:
                for key in item.fields:
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
    name = "olx"
    start_urls = [
        "https://www.olx.com.br/autos-e-pecas/carros-vans-e-utilitarios/estado-pr/regiao-de-curitiba-e-paranagua?pe=50000&sp=5&gb=1&gb=2&ics=1&ics=2&ics=5&cf=1&rs=2016"
    ]

    custom_settings = {
        "DOWNLOAD_DELAY": 2.0,
        "ROBOTSTXT_OBEY": True,
        "ITEM_PIPELINES": {"spiders.olx_spider.DatabasePipeline": 300},
        "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    total_pages = None

    def parse(self, response):
        data = self._parse_next_data(response)
        if not data:
            self.logger.warning("No __NEXT_DATA__ found on listing page")
            return

        props = data.get("props", {}).get("pageProps", {})
        ads = props.get("ads", [])
        page_index = props.get("pageIndex", 1)
        total_ads = props.get("totalOfAds", 0)
        page_size = props.get("pageSize", 50)

        if total_ads:
            self.total_pages = (int(total_ads) + int(page_size) - 1) // int(page_size)

        for ad in ads:
            item = self._item_from_listing_data(ad)
            ad_url = ad.get("url")
            if ad_url:
                yield scrapy.Request(
                    ad_url,
                    callback=self.parse_ad,
                    cb_kwargs={"item": item},
                )
            else:
                yield item

        if self.total_pages and page_index < self.total_pages:
            yield self._next_page_request(response.url, page_index)

    def parse_ad(self, response, item):
        desc = self._get_description(response)
        if desc:
            item["description"] = desc
        yield item

    def _get_description(self, response):
        data = self._parse_next_data(response)
        if data:
            props = data.get("props", {}).get("pageProps", {})
            ad_data = props.get("ad") or props.get("listing") or {}
            raw = ad_data.get("description") or ""
            if raw:
                return raw.strip()

        for sel in [
            'meta[name="description"]::attr(content)',
            'meta[property="og:description"]::attr(content)',
        ]:
            raw = response.css(sel).get()
            if raw:
                cleaned = raw.replace("&lt;br&gt;", "\n").replace("<br>", "\n").replace("<br/>", "\n")
                return cleaned.strip()

        for script in response.css("script[type='application/ld+json']"):
            try:
                obj = json.loads(script.css("::text").get())
                desc = (
                    (obj.get("makesOffer") or {}).get("itemOffered") or {}
                ).get("description") or ""
                if desc:
                    return desc.strip()
            except (json.JSONDecodeError, AttributeError):
                pass

        return None

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

        return CarItem(
            olx_id=str(ad.get("listId", "")),
            title=ad.get("subject", "").strip(),
            price=price,
            year=self._int(props.get("regdate")),
            mileage=self._int(props.get("mileage")),
            fuel=props.get("fuel"),
            transmission=props.get("gearbox"),
            color=props.get("carcolor"),
            neighborhood=neighborhood,
            seller_name=seller_name,
            city=city,
            state=state,
            image_urls=json.dumps(image_urls) if image_urls else None,
            listing_url=ad.get("url", ""),
            listing_date=listing_date,
        )

    def _next_page_request(self, current_url, current_page):
        parsed = urlparse(current_url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        qs["o"] = [str(current_page + 1)]
        new_query = urlencode(qs, doseq=True)
        next_url = urlunparse(parsed._replace(query=new_query))
        return scrapy.Request(next_url, callback=self.parse)

    def _parse_price(self, raw):
        m = re.search(r"[\d.]+", raw.replace(" ", ""))
        if m:
            clean = m.group(0).replace(".", "")
            try:
                return int(clean) * 100
            except ValueError:
                return None
        return None

    def _parse_next_data(self, response):
        text = response.css("script#__NEXT_DATA__::text").get()
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
