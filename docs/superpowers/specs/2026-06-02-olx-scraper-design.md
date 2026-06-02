# OLX Car Scraper — Design Document

## Objetivo
Web scraper que baixa anúncios de carros da OLX, salva em SQLite, e exibe em uma interface web com filtros.

## Stack
- **Scraper:** Scrapy
- **Web:** Flask + Bootstrap
- **Banco:** SQLite via SQLAlchemy
- **Linguagem:** Python 3

## Arquitetura

```
scraper/olx_spider.py  ──▶  models.py  ──▶  web/app.py
       │                      │                  │
       │                   SQLAlchemy             │
       │                      │                  │
       ▼                   car_listings.db        │
    Scrapy Pipeline ──────▶ SQLite ◀──────────────┘
```

- Scraper e web compartilham o mesmo `models.py` e o mesmo arquivo SQLite.
- O spider é executado manualmente via `scrapy crawl olx`.
- A web é servida por Flask e lê do mesmo banco.

## Schema: `car_listing`

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | INTEGER PK | Auto incremento |
| `olx_id` | TEXT UNIQUE | ID do anúncio na OLX (dedup) |
| `title` | TEXT | Título do anúncio |
| `price` | INTEGER | Preço em centavos (R$ * 100) |
| `year` | INTEGER | Ano de fabricação |
| `mileage` | INTEGER | Quilometragem |
| `fuel` | TEXT | Combustível |
| `transmission` | TEXT | Câmbio |
| `color` | TEXT | Cor |
| `neighborhood` | TEXT | Bairro |
| `zip_code` | TEXT | CEP |
| `seller_name` | TEXT | Nome do anunciante |
| `city` | TEXT | Cidade |
| `state` | TEXT | Estado (UF) |
| `description` | TEXT | Descrição completa |
| `image_urls` | TEXT | JSON com URLs das fotos |
| `listing_url` | TEXT | Link original |
| `listing_date` | TEXT | Data de publicação |
| `created_at` | DATETIME | Timestamp de inserção |
| `updated_at` | DATETIME | Timestamp de atualização |

## Scraper (Scrapy)

- **Spider:** `OlxSpider`
- **Start URL:** `https://www.olx.com.br/autos-e-pecas/carros-vans-e-utilitarios/estado-pr/regiao-de-curitiba-e-paranagua?pe=50000&sp=5&gb=1&gb=2&ics=1&ics=2&ics=5&cf=1&rs=2016` (Curitiba/PR, até R$50k, >=2016). Usuário altera conforme necessidade.
- **Extração:** Parse da listagem → links individuais → parse da página interna para dados completos
- **Paginação:** Segue link "Próxima" até o fim
- **Pipeline:** `UpsertPipeline` — usa `olx_id` para evitar duplicatas (INSERT OR REPLACE)
- **Config:** `DOWNLOAD_DELAY = 2.0`, respeita robots.txt, User-Agent padrão Scrapy

## Interface Web (Flask)

- **Rota única (`/`):** tabela de anúncios com filtros
  - Campo de busca textual (título/descrição)
  - Faixa de preço (min/max)
  - Ano (min/max)
  - Quilometragem (min/max)
  - Cidade (dropdown com valores do banco)
- **Layout:** Bootstrap 5, responsivo
- **Template único:** `templates/index.html`

## Estrutura de Arquivos

```
buscador-carros/
├── docs/superpowers/specs/
│   └── 2026-06-02-olx-scraper-design.md
├── scraper/
│   ├── scrapy.cfg
│   └── spiders/
│       └── olx_spider.py
├── web/
│   ├── app.py
│   └── templates/
│       └── index.html
├── models.py
├── run_scraper.py
└── requirements.txt
```

## Como Usar

1. `pip install -r requirements.txt`
2. Ajustar `start_urls` em `olx_spider.py` se necessário
3. `scrapy crawl olx` — coleta os anúncios
4. `python web/app.py` — inicia a interface web em `http://localhost:5000`
