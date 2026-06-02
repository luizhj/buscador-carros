# OLX Car Scraper — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a web scraper that downloads OLX car listings into SQLite and displays them in a filterable web interface.

**Architecture:** Scrapy spider crawls OLX listings and individual ad pages, extracting complete car data. A Scrapy pipeline saves to SQLite via SQLAlchemy. Flask app reads the same database and renders a Bootstrap table with search/filter controls.

**Tech Stack:** Python 3, Scrapy, Flask, SQLAlchemy, SQLite, Bootstrap 5

---

## File Structure

```
buscador-carros/
├── requirements.txt          # Dependências
├── models.py                 # SQLAlchemy models (compartilhado)
├── scraper/
│   ├── scrapy.cfg            # Config Scrapy
│   └── spiders/
│       ├── __init__.py
│       └── olx_spider.py     # Spider + pipeline
├── web/
│   ├── app.py                # Flask app
│   └── templates/
│       └── index.html        # Tabela Bootstrap com filtros
└── run_scraper.py            # Script conveniência
```

---

### Task 1: Dependências e scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `scraper/spiders/__init__.py`

- [ ] Criar diretórios: `scraper/spiders/`, `web/templates/`
- [ ] Criar `requirements.txt`:

```
scrapy>=2.11
flask>=3.0
sqlalchemy>=2.0
```

- [ ] Criar `scraper/spiders/__init__.py` vazio

---

### Task 2: Modelo do banco

**Files:**
- Create: `models.py`

- [ ] Criar `models.py` com SQLAlchemy:

```python
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone

engine = create_engine("sqlite:///car_listings.db")
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class CarListing(Base):
    __tablename__ = "car_listings"

    id = Column(Integer, primary_key=True)
    olx_id = Column(String, unique=True, nullable=True)
    title = Column(String)
    price = Column(Integer)
    year = Column(Integer)
    mileage = Column(Integer)
    fuel = Column(String)
    transmission = Column(String)
    color = Column(String)
    neighborhood = Column(String)
    zip_code = Column(String)
    seller_name = Column(String)
    city = Column(String)
    state = Column(String)
    description = Column(Text)
    image_urls = Column(Text)
    listing_url = Column(String)
    listing_date = Column(String)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


def init_db():
    Base.metadata.create_all(engine)


def get_session():
    return SessionLocal()
```

---

### Task 3: Scrapy spider + pipeline

**Files:**
- Create: `scraper/scrapy.cfg`
- Create: `scraper/spiders/olx_spider.py`

**scrapy.cfg:**
```ini
[settings]
default = olx_spider
```

**olx_spider.py:**

```python
import json
import re
from datetime import datetime, timezone

import scrapy
from models import CarListing, get_session, init_db


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
        "ITEM_PIPELINES": {"__main__.DatabasePipeline": 300},
        "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    def parse(self, response):
        for ad in response.css("a[data-ds-component='ad-card-link']"):
            yield response.follow(ad, callback=self.parse_ad)

        next_page = response.css("a[rel='next']::attr(href)").get()
        if next_page:
            yield response.follow(next_page, callback=self.parse)

    def parse_ad(self, response):
        olx_id = re.search(r"/(\d+)\.htm", response.url)
        olx_id = olx_id.group(1) if olx_id else None

        title = response.css("h1::text").get("").strip()
        price_text = response.css("h2::text").re_first(r"R\$ ?([\d.]+)")
        price = int(price_text.replace(".", "")) * 100 if price_text else None

        props = {}
        for row in response.css("table tr"):
            cells = row.css("th, td")
            if len(cells) >= 2:
                key = cells[0].css("::text").get("").strip().lower()
                val = cells[1].css("::text").get("").strip()
                props[key] = val

        desc = "".join(
            response.css('div[data-ds-component="ad-description"] p::text').getall()
        ).strip()

        images = response.css(
            'img[data-ds-component="ad-gallery-image"]::attr(src)'
        ).getall()

        yield CarItem(
            olx_id=olx_id,
            title=title,
            price=price,
            year=_int(props.get("ano")),
            mileage=_int(props.get("quilometragem")),
            fuel=props.get("combustível"),
            transmission=props.get("câmbio"),
            color=props.get("cor"),
            neighborhood=props.get("bairro"),
            zip_code=props.get("cep"),
            seller_name=props.get("anunciante"),
            city=props.get("cidade"),
            state=props.get("estado"),
            description=desc,
            image_urls=json.dumps(images) if images else None,
            listing_url=response.url,
            listing_date=props.get("data"),
        )


def _int(val):
    if not val:
        return None
    digits = re.sub(r"\D", "", val)
    return int(digits) if digits else None
```

---

### Task 4: Interface web

**Files:**
- Create: `web/app.py`
- Create: `web/templates/index.html`

**app.py:**

```python
from flask import Flask, render_template, request
from models import CarListing, get_session, init_db

app = Flask(__name__)


@app.route("/")
def index():
    session = get_session()
    try:
        q = session.query(CarListing)

        if s := request.args.get("q"):
            q = q.filter(CarListing.title.ilike(f"%{s}%"))
        if p_min := request.args.get("price_min"):
            q = q.filter(CarListing.price >= int(p_min) * 100)
        if p_max := request.args.get("price_max"):
            q = q.filter(CarListing.price <= int(p_max) * 100)
        if y_min := request.args.get("year_min"):
            q = q.filter(CarListing.year >= int(y_min))
        if y_max := request.args.get("year_max"):
            q = q.filter(CarListing.year <= int(y_max))
        if km_max := request.args.get("km_max"):
            q = q.filter(CarListing.mileage <= int(km_max))
        if city := request.args.get("city"):
            q = q.filter(CarListing.city.ilike(f"%{city}%"))

        listings = q.order_by(CarListing.created_at.desc()).all()
        cities = [
            c[0]
            for c in session.query(CarListing.city)
            .distinct()
            .order_by(CarListing.city)
            .all()
            if c[0]
        ]
        return render_template(
            "index.html", listings=listings, cities=cities, request=request
        )
    finally:
        session.close()


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
```

**index.html:**

```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Carros OLX</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
<div class="container py-4">
  <h1 class="mb-4">Carros — OLX</h1>

  <form class="row g-2 mb-4 p-3 bg-white rounded shadow-sm">
    <div class="col-md-3">
      <input name="q" class="form-control" placeholder="Buscar..." value="{{ request.args.get('q', '') }}">
    </div>
    <div class="col-md-2">
      <input name="price_min" class="form-control" placeholder="Preço min" value="{{ request.args.get('price_min', '') }}">
    </div>
    <div class="col-md-2">
      <input name="price_max" class="form-control" placeholder="Preço máx" value="{{ request.args.get('price_max', '') }}">
    </div>
    <div class="col-md-1">
      <input name="year_min" class="form-control" placeholder="Ano min" value="{{ request.args.get('year_min', '') }}">
    </div>
    <div class="col-md-1">
      <input name="year_max" class="form-control" placeholder="Ano máx" value="{{ request.args.get('year_max', '') }}">
    </div>
    <div class="col-md-1">
      <input name="km_max" class="form-control" placeholder="KM máx" value="{{ request.args.get('km_max', '') }}">
    </div>
    <div class="col-md-2">
      <select name="city" class="form-select">
        <option value="">Todas cidades</option>
        {% for c in cities %}
        <option value="{{ c }}" {% if request.args.get('city') == c %}selected{% endif %}>{{ c }}</option>
        {% endfor %}
      </select>
    </div>
    <div class="col-12">
      <button class="btn btn-primary">Filtrar</button>
      <a href="/" class="btn btn-outline-secondary">Limpar</a>
    </div>
  </form>

  <div class="table-responsive">
    <table class="table table-striped table-hover bg-white rounded shadow-sm">
      <thead class="table-dark">
        <tr>
          <th>Foto</th>
          <th>Título</th>
          <th>Preço</th>
          <th>Ano</th>
          <th>KM</th>
          <th>Cidade</th>
          <th>Link</th>
        </tr>
      </thead>
      <tbody>
        {% for ad in listings %}
        <tr>
          <td>
            {% if ad.image_urls %}
            {% set imgs = ad.image_urls|fromjson %}
            <img src="{{ imgs[0] }}" width="80" class="rounded" loading="lazy">
            {% endif %}
          </td>
          <td>{{ ad.title }}</td>
          <td>R$ {{ "%.2f"|format(ad.price / 100) }}</td>
          <td>{{ ad.year }}</td>
          <td>{{ "{:,}".format(ad.mileage).replace(',', '.') if ad.mileage else '-' }}</td>
          <td>{{ ad.city }}{{ '-' if ad.city and ad.state else '' }}{{ ad.state }}</td>
          <td><a href="{{ ad.listing_url }}" target="_blank">Abrir</a></td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
</body>
</html>
```

Precisa registrar o filtro `fromjson` no Jinja. Adicionar em `app.py` antes de rodar:

```python
import json
from flask import Flask
app = Flask(__name__)
app.jinja_env.filters['fromjson'] = lambda s: json.loads(s)
```

---

### Task 5: Script de execução + teste final

**Files:**
- Create: `run_scraper.py`

- [ ] Criar `run_scraper.py`:

```python
#!/usr/bin/env python3
from scrapy.crawler import CrawlerProcess
from scraper.spiders.olx_spider import OlxSpider

process = CrawlerProcess()
process.crawl(OlxSpider)
process.start()
```

- [ ] Instalar dependências: `pip install -r requirements.txt`
- [ ] Rodar scraper: `python run_scraper.py`
- [ ] Rodar web: `python web/app.py`
- [ ] Abrir `http://localhost:5000` e verificar filtros

---

## Self-Review

| Spec item                     | Task |
|-------------------------------|------|
| Schema com todas as colunas   | 2    |
| Scrapy spider com paginação   | 3    |
| Pipeline de upsert            | 3    |
| Start URL configurável        | 3 (definida inline) |
| Flask com filtros             | 4    |
| Template Bootstrap com tabela | 4    |
| Preço em centavos             | 2, 4 |
| Script de execução            | 5    |

Sem placeholders, sem contradições. Todos os tipos consistentes entre tasks.
