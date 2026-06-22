import json
import os
import re
import sys
from datetime import datetime, timezone

import scrapy
from playwright.sync_api import sync_playwright

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from config import SOCARRAO_URL as _FALLBACK_SOCARRAO_URL
from models import CarListing, IgnoredListing, get_session


class SocarraoItem(scrapy.Item):
    olx_id = scrapy.Field()
    title = scrapy.Field()
    price = scrapy.Field()
    year = scrapy.Field()
    mileage = scrapy.Field()
    fuel = scrapy.Field()
    transmission = scrapy.Field()
    color = scrapy.Field()
    brand = scrapy.Field()
    model = scrapy.Field()
    city = scrapy.Field()
    state = scrapy.Field()
    image_urls = scrapy.Field()
    listing_url = scrapy.Field()
    source = scrapy.Field()
    motorpower = scrapy.Field()
    cartype = scrapy.Field()


class DatabasePipeline:
    def process_item(self, item, spider):
        oid = item.get("olx_id")
        if not oid:
            return item
        session = get_session()
        try:
            if session.get(IgnoredListing, oid):
                return item
            existing = session.query(CarListing).filter_by(olx_id=oid).first()
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


class SocarraoSpider(scrapy.Spider):
    name = "socarrao"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        saved_url = None
        _path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", ".current_url_socarrao")
        try:
            with open(os.path.normpath(_path)) as _f:
                saved_url = _f.read().strip()
        except (FileNotFoundError, OSError):
            pass
        self._start_url = kwargs.get("start_url") or saved_url or _FALLBACK_SOCARRAO_URL
        self._max_items = int(kwargs.get("max_items", 0))

    def start_requests(self):
        html = self._fetch_with_browser(self._start_url)
        if not html:
            return

        items = list(self._parse_listing(html))
        for item in items:
            yield item

        print(f"  Total: {len(items)} anúncio(s).", flush=True)
    
    def _fetch_with_browser(self, url):
        print(f"  Carregando: {url}", flush=True)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_viewport_size({"width": 1366, "height": 768})
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

                import time as _time
                _time.sleep(3)

                h1 = page.locator("h1").first.text_content() or ""
                import re as _re
                _total = _re.search(r"[\d.]+", h1.replace(".", ""))
                total_count = int(_total.group()) if _total else 0
                if total_count:
                    print(f"Total de anúncios: {total_count}", flush=True)
                else:
                    print(f"  H1: {h1.strip()[:80]}", flush=True)

                prev = 0
                same_count = 0
                last_html = None
                for scroll_round in range(50):
                    cards = page.locator(".vehicle-card")
                    current = cards.count()
                    if current > 0 and current != prev:
                        print(f"Scraped {current} items", flush=True)
                    if current >= self._max_items > 0:
                        print(f"\n    Atingido limite de {self._max_items} veículos.", flush=True)
                        break
                    if current == prev:
                        same_count += 1
                    else:
                        same_count = 0
                    if same_count >= 5:
                        print(f"\n    Nenhum novo veículo após {same_count} tentativas.", flush=True)
                        break
                    prev = current
                    if scroll_round % 10 == 0 and scroll_round > 0:
                        try:
                            last_html = page.content()
                        except Exception:
                            pass
                    try:
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    except Exception as _e:
                        print(f"  Navegador desconectado durante scroll: {_e}", flush=True)
                        break
                    _time.sleep(1.5)

                try:
                    html = page.content()
                    browser.close()
                    return html
                except Exception as _e:
                    print(f"  Capturando HTML do snapshot ({len(last_html or '')} chars)", flush=True)
                    try:
                        browser.close()
                    except Exception:
                        pass
                    return last_html
        except Exception as e:
            print(f"  ERRO no navegador: {e}", flush=True)
            return None

    def _parse_listing(self, html):
        from parsel import Selector
        sel = Selector(text=html)

        cards = sel.css(".vehicle-card")
        if not cards:
            print("  Nenhum card .vehicle-card encontrado.", flush=True)
            return

        print(f"  Cards encontrados: {len(cards)}", flush=True)

        for card in cards:
            item = self._item_from_card(card)
            if item:
                yield item

    def _item_from_card(self, card):
        detail_url = card.attrib.get("href") or card.css("a::attr(href)").get()
        oid = None
        if detail_url:
            m = re.search(r"/(\d+)/?$", detail_url)
            if m:
                oid = "SC" + m.group(1)
        if not oid:
            return None

        brand = card.css(".brand-model-formatter__brand::text").get("").strip()
        model = card.css(".brand-model-formatter__model::text").get("").strip()
        version = card.css(".vehicle-card__right--version::text").get("").strip()
        title = f"{brand} {model} {version}".strip() if version else f"{brand} {model}".strip()
        motorpower = None
        if version:
            for _m in re.findall(r'(\d+[.,]\d+)', version):
                try:
                    _v = float(_m.replace(",", "."))
                    if 0.5 <= _v <= 8.0:
                        motorpower = self._format_motorpower(_v)
                        break
                except ValueError:
                    pass
            if not motorpower:
                for _m in re.findall(r'\b(\d{3,4})\b', version):
                    try:
                        _v = int(_m)
                        if 900 <= _v <= 8000:
                            motorpower = self._format_motorpower(_v / 1000)
                            break
                    except ValueError:
                        pass

        specs = card.css(".vehicle-card__right--specs li::text").getall()
        specs = [s.strip() for s in specs]
        year = self._int(specs[0]) if len(specs) > 0 else None
        transmission = self._clean_transmission(specs[1]) if len(specs) > 1 else None
        mileage = self._parse_mileage(specs[2]) if len(specs) > 2 else None
        fuel = specs[3] if len(specs) > 3 else None
        if not transmission and title:
            _title_lower = title.lower()
            if " autom" in _title_lower or " aut." in _title_lower:
                transmission = "Automático"
            elif " mec" in _title_lower or " mec." in _title_lower or " manual" in _title_lower:
                transmission = "Manual"
            elif " semi" in _title_lower:
                transmission = "Semi-Automático"
        cartype = None
        if version:
            _v_lower = version.lower()
            if "sedan" in _v_lower or "sed." in _v_lower:
                cartype = "Sedã"
            elif "hatch" in _v_lower:
                cartype = "Hatch"
            elif "suv" in _v_lower:
                cartype = "SUV"
            elif "pick-up" in _v_lower or "pickup" in _v_lower:
                cartype = "Pick-up"
            elif "convers" in _v_lower:
                cartype = "Conversível"
            elif "coupe" in _v_lower or "coupé" in _v_lower:
                cartype = "Coupé"
            elif "perua" in _v_lower or "wagon" in _v_lower:
                cartype = "Perua"
            elif "minivan" in _v_lower or "van" in _v_lower:
                cartype = "Van/Utilitário"

        raw_image = card.css("img::attr(src)").get()
        if raw_image and "vehicle_sample" not in raw_image:
            clean = raw_image.split("?")[0]
            image_urls = json.dumps([clean])
        else:
            return None

        raw_json = self._extract_jsonld(card)
        price = None
        if raw_json:
            try:
                data = json.loads(raw_json)
                raw_price = (data.get("offers") or {}).get("price", "0")
                price = self._parse_price(raw_price)
                if not image_urls:
                    raw_image = data.get("image", "")
                    if raw_image:
                        image_urls = json.dumps([raw_image])
            except (json.JSONDecodeError, ValueError):
                pass
        if price is None:
            price_text = card.css(".vehicle-card__right--price *::text, .vehicle-card__priceSection--value *::text").getall()
            price = self._parse_price_from_text("".join(price_text))

        location = ""
        texts = card.css("::text").getall()
        for t in texts:
            t = t.strip()
            if " - " in t and len(t) < 60:
                parts = t.split(" - ")
                if len(parts) == 2 and len(parts[0]) > 3:
                    location = t
                    break

        city, state = None, None
        if location:
            parts = location.split(" - ")
            if len(parts) == 2:
                city, state = parts[0].strip(), parts[1].strip()

        listing_url = ""
        if detail_url:
            listing_url = "https://www.socarrao.com.br" + detail_url if detail_url.startswith("/") else detail_url

        return SocarraoItem(
            olx_id=oid,
            title=title,
            brand=brand,
            model=model,
            price=price,
            year=year,
            mileage=mileage,
            fuel=fuel,
            transmission=transmission,
            city=city,
            state=state,
            image_urls=image_urls,
            listing_url=listing_url,
            source="socarrao",
            motorpower=motorpower,
            cartype=cartype,
        )

    def _extract_jsonld(self, card):
        raw = card.get()
        m = re.search(r'(\{"@context":\s*["\']https?://schema\.org["\'\s,]*"@type":\s*"Vehicle".*?"offers".*?\})', raw, re.DOTALL)
        return m.group(1) if m else None

    def _parse_mileage(self, raw):
        if not raw:
            return None
        digits = re.sub(r"\D", "", raw)
        return int(digits) if digits else None

    @staticmethod
    def _format_motorpower(val):
        try:
            v = float(val)
            if 1.0 <= v <= 1.9:
                return f"{v:.1f}"
            elif 2.0 <= v <= 2.9:
                return "2.0 - 2.9"
            elif 3.0 <= v <= 3.9:
                return "3.0 - 3.9"
            elif 4.0 <= v:
                return "4.0 ou mais"
            return None
        except (ValueError, TypeError):
            return None

    def _parse_price(self, raw):
        if raw is None:
            return None
        if isinstance(raw, (int, float)):
            return int(raw) * 100
        m = re.search(r"[\d.]+", str(raw).replace(" ", ""))
        if m:
            clean = m.group(0).replace(".", "")
            try:
                return int(clean) * 100
            except ValueError:
                return None
        return None

    def _parse_price_from_text(self, text):
        m = re.search(r"R?\$?\s*([\d.]+)", text)
        if m:
            clean = m.group(1).replace(".", "")
            try:
                return int(clean) * 100
            except ValueError:
                return None
        return None

    def _clean_transmission(self, raw):
        if not raw:
            return None
        raw = raw.strip().lower()
        mapping = {
            "automático": "Automático",
            "automatico": "Automático",
            "manual": "Manual",
            "mecânico": "Manual",
            "mecanico": "Manual",
            "semi-automático": "Semi-Automático",
            "semi-automatico": "Semi-Automático",
            "automatizado": "Automatizado",
        }
        return mapping.get(raw, raw.capitalize())

    @staticmethod
    def _int(val):
        if not val:
            return None
        digits = re.sub(r"\D", "", str(val))
        return int(digits) if digits else None
