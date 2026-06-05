# Buscador de Carros — OLX Scraper

Scraper de anúncios de carros da OLX + interface web para visualização com filtros.

## Funcionalidades

- Scraper de anúncios de carros da OLX (contorna Cloudflare)
- 25+ campos extraídos: preço, ano, km, combustível, câmbio, cor, tipo, motor, bairro, CEP, anunciante, fotos etc.
- Interface web com filtros por: marca, modelo, cidade, bairro, tipo, motor, câmbio, ano, preço, km
- Ordenação por: preço, ano, km, data
- Filtros com checkbox, selecionados aparecem primeiro
- Botão "Marcar todos" em cada filtro
- Página de configuração para definir URL de pesquisa e executar o scraper
- Imagens exibidas via proxy (contorna bloqueio de hotlinking)
- **Ignorar anúncios** — oculta anúncios da listagem e filtros; página dedicada para gerenciar e restaurar
- **Favoritar anúncios** — marca com ♥ para acesso rápido; página dedicada com todos os favoritos
- **Modelos compostos** — reconhece nomes de modelo com duas palavras (Grand Siena, C4 Lounge, etc.) via JSON editável pela interface web
- **Exportar/Importar banco** — backup completo (.zip) do banco, configurações e modelos compostos
- Ignora anúncios com palavras-chave no título (*, retirada de peça, entrada, parcelas, sucata)

## Stack

Python 3, Scrapy, Flask, SQLAlchemy, SQLite, Bootstrap 5, cloudscraper

## Pré-requisitos

- **Python 3.9+** instalado
- **pip** e **venv** (geralmente inclusos no Python)

  No Windows, marque "Add Python to PATH" durante a instalação.
  No Linux, instale com: `sudo apt install python3 python3-pip python3-venv`

## Como usar

### Linux / macOS

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run_scraper.py    # baixa os anúncios
.venv/bin/python web/app.py        # inicia o servidor em http://localhost:5000
```

### Windows (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python run_scraper.py
.venv\Scripts\python web\app.py
```

Ou via interface web: `http://localhost:5000/config` — cole a URL de pesquisa da OLX e clique em "Salvar e Atualizar".

## Configuração

Em `config.py`:
- `START_URL` — URL da pesquisa na OLX
- `START_PAGE` — página inicial (1 = primeira página)
