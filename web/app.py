import os
import sys
import json
from flask import Flask, render_template, request, Response, redirect
import urllib.request

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from sqlalchemy import func
from models import CarListing, get_session, init_db

CENTAVOS_PER_REAL = 100
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

app = Flask(__name__)
app.jinja_env.filters["fromjson"] = lambda s: json.loads(s)


def _brl(cents):
    if cents is None:
        return "-"
    return f"R$ {cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

app.jinja_env.filters["brl"] = _brl


@app.route("/img-proxy")
def img_proxy():
    url = request.args.get("url")
    if not url or not url.startswith("https://img.olx.com.br"):
        return ("", 400)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    resp = urllib.request.urlopen(req)
    return Response(resp.read(), content_type=resp.headers.get("Content-Type", "image/jpeg"))


def _sort_selected_first(items, selected):
    selected_set = set(selected)
    if items and isinstance(items[0], tuple) and len(items[0]) == 3:
        return sorted(items, key=lambda x: (0 if x[0] in selected_set else 1, x[0] or ""))
    return sorted(items, key=lambda x: (0 if x[0] in selected_set else 1, x[0] or ""))


def _int_or_none(val):
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


@app.route("/")
def index():
    session = get_session()
    try:
        q = session.query(CarListing)

        if s := request.args.get("q"):
            q = q.filter(CarListing.title.ilike(f"%{s}%"))
        if p_min := _int_or_none(request.args.get("price_min")):
            q = q.filter(CarListing.price >= p_min * CENTAVOS_PER_REAL)
        if p_max := _int_or_none(request.args.get("price_max")):
            q = q.filter(CarListing.price <= p_max * CENTAVOS_PER_REAL)
        if y_min := _int_or_none(request.args.get("year_min")):
            q = q.filter(CarListing.year >= y_min)
        if y_max := _int_or_none(request.args.get("year_max")):
            q = q.filter(CarListing.year <= y_max)
        if km_max := _int_or_none(request.args.get("km_max")):
            q = q.filter(CarListing.mileage <= km_max)
        if city := request.args.get("city"):
            q = q.filter(CarListing.city.ilike(f"%{city}%"))
        brands = request.args.getlist("brand")
        if brands:
            q = q.filter(CarListing.brand.in_(brands))
        if models := request.args.getlist("model"):
            q = q.filter(CarListing.model.in_(models))
        if city_check := request.args.getlist("city_check"):
            q = q.filter(CarListing.city.in_(city_check))
        if neighborhood_check := request.args.getlist("neighborhood_check"):
            if "Sem bairro informado" in neighborhood_check:
                q = q.filter(
                    CarListing.neighborhood.is_(None) | CarListing.neighborhood.in_([n for n in neighborhood_check if n != "Sem bairro informado"])
                )
            else:
                q = q.filter(CarListing.neighborhood.in_(neighborhood_check))
        # base query sem filtros de si-próprio para calcular available_*
        base_q = q
        cartype_filter = request.args.getlist("cartype_filter")
        motorpower_filter = request.args.getlist("motorpower_filter")
        gearbox_filter = request.args.getlist("gearbox_filter")
        year_filter = request.args.getlist("year_filter")
        if cartype_filter:
            q = q.filter(CarListing.cartype.in_(cartype_filter))
        if motorpower_filter:
            q = q.filter(CarListing.motorpower.in_(motorpower_filter))
        if gearbox_filter:
            q = q.filter(CarListing.transmission.in_(gearbox_filter))
        year_filter = [int(y) for y in year_filter if y.isdigit()]
        if year_filter:
            q = q.filter(CarListing.year.in_(year_filter))

        sort = request.args.get("sort", "")
        order = CarListing.created_at.desc()
        if sort == "price_asc":
            order = CarListing.price.asc().nullslast()
        elif sort == "price_desc":
            order = CarListing.price.desc().nullslast()
        elif sort == "year_asc":
            order = CarListing.year.asc().nullslast()
        elif sort == "year_desc":
            order = CarListing.year.desc().nullslast()
        elif sort == "km_asc":
            order = CarListing.mileage.asc().nullslast()

        listings = q.filter(CarListing.olx_id.isnot(None)).order_by(order).all()
        available_cities = (
            session.query(CarListing.city, func.count(CarListing.id))
            .filter(CarListing.city.isnot(None))
            .group_by(CarListing.city)
            .order_by(CarListing.city)
            .all()
        )
        available_brands = (
            session.query(CarListing.brand, func.count(CarListing.id))
            .filter(CarListing.brand.isnot(None))
            .group_by(CarListing.brand)
            .order_by(CarListing.brand)
            .all()
        )
        mq = (
            session.query(CarListing.model, func.count(CarListing.id))
            .filter(CarListing.model.isnot(None))
        )
        if brands:
            mq = mq.filter(CarListing.brand.in_(brands))
        available_models = mq.group_by(CarListing.model).order_by(CarListing.model).all()
        nq = (
            session.query(CarListing.neighborhood, CarListing.city, func.count(CarListing.id))
            .filter(CarListing.city.isnot(None))
        )
        if city_check:
            nq = nq.filter(CarListing.city.in_(city_check))
        raw_neighborhoods = nq.group_by(CarListing.neighborhood, CarListing.city).order_by(CarListing.neighborhood).all()
        no_nb_count = (
            session.query(func.count(CarListing.id))
            .filter(CarListing.neighborhood.is_(None), CarListing.city.isnot(None))
        )
        if city_check:
            no_nb_count = no_nb_count.filter(CarListing.city.in_(city_check))
        no_nb_count = no_nb_count.scalar()
        available_neighborhoods = [(n, c, cnt) for n, c, cnt in raw_neighborhoods if n is not None]
        if no_nb_count:
            available_neighborhoods.append(("Sem bairro informado", "", no_nb_count))

        available_cartypes = (
            base_q.with_entities(CarListing.cartype, func.count(CarListing.id))
            .filter(CarListing.cartype.isnot(None), CarListing.olx_id.isnot(None))
            .group_by(CarListing.cartype)
            .order_by(CarListing.cartype)
            .all()
        )
        available_motorpowers = (
            base_q.with_entities(CarListing.motorpower, func.count(CarListing.id))
            .filter(CarListing.motorpower.isnot(None), CarListing.olx_id.isnot(None))
            .group_by(CarListing.motorpower)
            .order_by(CarListing.motorpower)
            .all()
        )
        available_gearboxes = (
            base_q.with_entities(CarListing.transmission, func.count(CarListing.id))
            .filter(CarListing.transmission.isnot(None), CarListing.olx_id.isnot(None))
            .group_by(CarListing.transmission)
            .order_by(CarListing.transmission)
            .all()
        )
        available_years = (
            base_q.with_entities(CarListing.year, func.count(CarListing.id))
            .filter(CarListing.year.isnot(None), CarListing.olx_id.isnot(None))
            .group_by(CarListing.year)
            .order_by(CarListing.year.desc())
            .all()
        )
        return render_template(
            "index.html",
            listings=listings,
            cities=[c for c, _ in available_cities],
            available_cities=_sort_selected_first(available_cities, request.args.getlist("city_check")),
            available_brands=_sort_selected_first(available_brands, brands),
            selected_brands=brands,
            available_models=_sort_selected_first(available_models, request.args.getlist("model")),
            selected_models=request.args.getlist("model"),
            selected_cities=request.args.getlist("city_check"),
            available_neighborhoods=_sort_selected_first(available_neighborhoods, request.args.getlist("neighborhood_check")),
            selected_neighborhoods=request.args.getlist("neighborhood_check"),
            available_cartypes=_sort_selected_first(available_cartypes, cartype_filter),
            available_motorpowers=_sort_selected_first(available_motorpowers, motorpower_filter),
            available_gearboxes=_sort_selected_first(available_gearboxes, gearbox_filter),
            available_years=_sort_selected_first(available_years, year_filter),
            selected_cartypes=cartype_filter,
            selected_motorpowers=motorpower_filter,
            selected_gearboxes=gearbox_filter,
            selected_years=year_filter,
            sort=sort,
        )
    finally:
        session.close()


@app.route("/marcas")
def marcas():
    session = get_session()
    try:
        rows = (
            session.query(CarListing.brand, func.count(CarListing.id))
            .filter(CarListing.brand.isnot(None))
            .group_by(CarListing.brand)
            .order_by(CarListing.brand)
            .all()
        )
        return render_template("marcas.html", marcas=rows)
    finally:
        session.close()


@app.route("/modelos")
def todos_modelos():
    session = get_session()
    try:
        rows = (
            session.query(CarListing.brand, CarListing.model, func.count(CarListing.id))
            .filter(CarListing.brand.isnot(None), CarListing.model.isnot(None))
            .group_by(CarListing.brand, CarListing.model)
            .order_by(CarListing.brand, CarListing.model)
            .all()
        )
        return render_template("todos_modelos.html", modelos=rows)
    finally:
        session.close()


@app.route("/marcas/<brand>")
def modelos(brand):
    session = get_session()
    try:
        rows = (
            session.query(CarListing.model, func.count(CarListing.id))
            .filter(CarListing.brand == brand, CarListing.model.isnot(None))
            .group_by(CarListing.model)
            .order_by(CarListing.model)
            .all()
        )
        return render_template("modelos.html", brand=brand, modelos=rows)
    finally:
        session.close()


@app.route("/delete/<int:listing_id>")
def delete_listing(listing_id):
    session = get_session()
    try:
        listing = session.get(CarListing, listing_id)
        if listing:
            session.delete(listing)
            session.commit()
    finally:
        session.close()
    return redirect(request.referrer or "/")


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
