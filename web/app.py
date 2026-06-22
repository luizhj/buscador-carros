import os
import sys
import json
import re
import html
from datetime import datetime, timezone
import zipfile
import io
import subprocess
import threading
import shutil
from flask import Flask, render_template, request, Response, redirect, url_for, send_file
import urllib.request
import cloudscraper
from parsel import Selector

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from sqlalchemy import func
from models import CarListing, IgnoredListing, FavoriteListing, SavedFilter, BlacklistRule, Brand, get_session, init_db

CENTAVOS_PER_REAL = 100
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

app = Flask(__name__)
app.jinja_env.filters["fromjson"] = lambda s: json.loads(s)


def _url_for_page(p):
    from urllib.parse import urlencode, parse_qs
    from flask import request as req
    qs = parse_qs(req.query_string.decode(), keep_blank_values=True)
    qs.pop("page", None)
    if p > 1:
        qs["page"] = [str(p)]
    return "/?" + urlencode(qs, doseq=True)


app.jinja_env.globals["url_for_page"] = _url_for_page


def _brl(cents):
    if cents is None:
        return "-"
    return f"R$ {cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

app.jinja_env.filters["brl"] = _brl


@app.route("/img-proxy")
def img_proxy():
    url = request.args.get("url")
    allowed = (
        url.startswith("https://img.olx.com.br") or
        url.startswith("https://www.socarrao.com.br/sc-vehicle-images-prod/")
    )
    if not url or not allowed:
        return ("", 400)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    resp = urllib.request.urlopen(req)
    return Response(resp.read(), content_type=resp.headers.get("Content-Type", "image/jpeg"))


def _last_scrape_result():
    try:
        with open(SCRAPE_RESULT_FILE) as f:
            return json.load(f)
    except Exception:
        return None


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
        # -- filtros universais (não auto-excludentes) --
        q_base = session.query(CarListing).filter(CarListing.status == "active")
        if s := request.args.get("q"):
            q_base = q_base.filter(CarListing.title.ilike(f"%{s}%"))
        if p_min := _int_or_none(request.args.get("price_min")):
            q_base = q_base.filter(CarListing.price >= p_min * CENTAVOS_PER_REAL)
        if p_max := _int_or_none(request.args.get("price_max")):
            q_base = q_base.filter(CarListing.price <= p_max * CENTAVOS_PER_REAL)
        notes_filter = request.args.get("notes_filter", "")
        if notes_filter == "with":
            q_base = q_base.filter(CarListing.notes.isnot(None))
        elif notes_filter == "without":
            q_base = q_base.filter(CarListing.notes.is_(None))

        _ignored_sub = session.query(IgnoredListing.olx_id).filter(IgnoredListing.olx_id.isnot(None))
        _ignored_filter = ~CarListing.olx_id.in_(_ignored_sub)
        q_base = q_base.filter(_ignored_filter)

        # -- filtros auto-excludentes (lidos antes, aplicados em q_final) --
        brands = request.args.getlist("brand")
        models_list = request.args.getlist("model")
        city_txt = request.args.get("city")
        city_check = request.args.getlist("city_check")
        neighborhood_check = request.args.getlist("neighborhood_check")
        seller_type_filter = request.args.getlist("seller_type")
        cartype_filter = request.args.getlist("cartype_filter")
        motorpower_filter = request.args.getlist("motorpower_filter")
        gearbox_filter = request.args.getlist("gearbox_filter")
        steering_filter = request.args.getlist("steering_filter")
        features_filter = request.args.getlist("features_filter")
        year_filter = [int(y) for y in request.args.getlist("year_filter") if y.isdigit()]
        y_min = _int_or_none(request.args.get("year_min"))
        y_max = _int_or_none(request.args.get("year_max"))
        km_max = _int_or_none(request.args.get("km_max"))

        # -- q_final = q_base + todos os auto-excludentes --
        q = q_base
        if brands:
            q = q.filter(CarListing.brand.in_(brands))
        if models_list:
            q = q.filter(CarListing.model.in_(models_list))
        if city_txt:
            q = q.filter(CarListing.city.ilike(f"%{city_txt}%"))
        if city_check:
            q = q.filter(CarListing.city.in_(city_check))
        if neighborhood_check:
            if "Sem bairro informado" in neighborhood_check:
                q = q.filter(CarListing.neighborhood.is_(None) | CarListing.neighborhood.in_([n for n in neighborhood_check if n != "Sem bairro informado"]))
            else:
                q = q.filter(CarListing.neighborhood.in_(neighborhood_check))
        if cartype_filter:
            q = q.filter(CarListing.cartype.in_(cartype_filter))
        if motorpower_filter:
            q = q.filter(CarListing.motorpower.in_(motorpower_filter))
        if gearbox_filter:
            q = q.filter(CarListing.transmission.in_(gearbox_filter))
        if year_filter:
            q = q.filter(CarListing.year.in_(year_filter))
        if seller_type_filter:
            q = q.filter(CarListing.seller_type.in_(seller_type_filter))
        if steering_filter:
            q = q.filter(CarListing.car_steering.in_(steering_filter))
        if features_filter:
            q = q.filter(CarListing.car_features.contains(features_filter[0]))
            for ff in features_filter[1:]:
                q = q.filter(CarListing.car_features.contains(ff))
        if y_min:
            q = q.filter(CarListing.year >= y_min)
        if y_max:
            q = q.filter(CarListing.year <= y_max)
        if km_max:
            q = q.filter(CarListing.mileage <= km_max)
        if request.args.get("novos"):
            q = q.filter(func.abs(func.julianday(CarListing.created_at) - func.julianday(CarListing.updated_at)) < 0.00001)

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

        base_listings_q = q.filter(CarListing.olx_id.isnot(None), CarListing.status == "active").order_by(order)
        page = _int_or_none(request.args.get("page")) or 1
        page = max(page, 1)
        per_page = 50
        total = base_listings_q.count()
        listings = base_listings_q.offset((page - 1) * per_page).limit(per_page).all()
        total_pages = max((total + per_page - 1) // per_page, 1)

        from datetime import timedelta
        _new_ids = set()
        for ad in listings:
            if ad.created_at and ad.updated_at:
                delta = abs((ad.created_at - ad.updated_at).total_seconds())
                if delta < 1:
                    _new_ids.add(ad.olx_id)

        # -- available_* usam q_base + filtros relevantes (EXCETO o próprio) --
        def _excl(bq, *excluded):
            """retorna query com todos os filtros auto-excludentes exceto os listados."""
            r = bq
            if brands and "brand" not in excluded:
                r = r.filter(CarListing.brand.in_(brands))
            if models_list and "model" not in excluded:
                r = r.filter(CarListing.model.in_(models_list))
            if city_txt and "city" not in excluded:
                r = r.filter(CarListing.city.ilike(f"%{city_txt}%"))
            if city_check and "city" not in excluded:
                r = r.filter(CarListing.city.in_(city_check))
            if neighborhood_check and "neighborhood" not in excluded:
                if "Sem bairro informado" in neighborhood_check:
                    r = r.filter(CarListing.neighborhood.is_(None) | CarListing.neighborhood.in_([n for n in neighborhood_check if n != "Sem bairro informado"]))
                else:
                    r = r.filter(CarListing.neighborhood.in_(neighborhood_check))
            if cartype_filter and "cartype" not in excluded:
                r = r.filter(CarListing.cartype.in_(cartype_filter))
            if motorpower_filter and "motorpower" not in excluded:
                r = r.filter(CarListing.motorpower.in_(motorpower_filter))
            if gearbox_filter and "gearbox" not in excluded:
                r = r.filter(CarListing.transmission.in_(gearbox_filter))
            if seller_type_filter and "seller_type" not in excluded:
                r = r.filter(CarListing.seller_type.in_(seller_type_filter))
            if steering_filter and "steering" not in excluded:
                r = r.filter(CarListing.car_steering.in_(steering_filter))
            if features_filter and "features" not in excluded:
                r = r.filter(CarListing.car_features.contains(features_filter[0]))
                for ff in features_filter[1:]:
                    r = r.filter(CarListing.car_features.contains(ff))
            if year_filter and "year" not in excluded:
                r = r.filter(CarListing.year.in_(year_filter))
            if y_min and "year" not in excluded:
                r = r.filter(CarListing.year >= y_min)
            if y_max and "year" not in excluded:
                r = r.filter(CarListing.year <= y_max)
            if km_max and "km" not in excluded:
                r = r.filter(CarListing.mileage <= km_max)
            return r

        available_cities = (
            _excl(q_base).with_entities(CarListing.city, func.count(CarListing.id))
            .filter(CarListing.city.isnot(None))
            .group_by(CarListing.city)
            .order_by(CarListing.city)
            .all()
        )
        available_brands = (
            _excl(q_base, "brand").with_entities(CarListing.brand, func.count(CarListing.id))
            .filter(CarListing.brand.isnot(None))
            .group_by(CarListing.brand)
            .order_by(CarListing.brand)
            .all()
        )
        mq = _excl(q_base, "model").with_entities(CarListing.model, func.count(CarListing.id)).filter(CarListing.model.isnot(None))
        if brands:
            mq = mq.filter(CarListing.brand.in_(brands))
        available_models = mq.group_by(CarListing.model).order_by(CarListing.model).all()
        nq = (
            _excl(q_base, "neighborhood").with_entities(CarListing.neighborhood, CarListing.city, func.count(CarListing.id))
            .filter(CarListing.city.isnot(None))
        )
        if city_check:
            nq = nq.filter(CarListing.city.in_(city_check))
        raw_neighborhoods = nq.group_by(CarListing.neighborhood, CarListing.city).order_by(CarListing.neighborhood).all()
        no_nb_count = (
            q_base.with_entities(func.count(CarListing.id))
            .filter(CarListing.neighborhood.is_(None), CarListing.city.isnot(None))
        )
        if city_check:
            no_nb_count = no_nb_count.filter(CarListing.city.in_(city_check))
        no_nb_count = no_nb_count.scalar()
        available_neighborhoods = [(n, c, cnt) for n, c, cnt in raw_neighborhoods if n is not None]
        if no_nb_count:
            available_neighborhoods.append(("Sem bairro informado", "", no_nb_count))

        available_cartypes = (
            _excl(q_base, "cartype").with_entities(CarListing.cartype, func.count(CarListing.id))
            .filter(CarListing.cartype.isnot(None), CarListing.olx_id.isnot(None))
            .group_by(CarListing.cartype)
            .order_by(CarListing.cartype)
            .all()
        )
        available_motorpowers = (
            _excl(q_base, "motorpower").with_entities(CarListing.motorpower, func.count(CarListing.id))
            .filter(CarListing.motorpower.isnot(None), CarListing.olx_id.isnot(None))
            .group_by(CarListing.motorpower)
            .order_by(CarListing.motorpower)
            .all()
        )
        available_gearboxes = (
            _excl(q_base, "gearbox").with_entities(CarListing.transmission, func.count(CarListing.id))
            .filter(CarListing.transmission.isnot(None), CarListing.olx_id.isnot(None))
            .group_by(CarListing.transmission)
            .order_by(CarListing.transmission)
            .all()
        )
        available_years = (
            _excl(q_base, "year").with_entities(CarListing.year, func.count(CarListing.id))
            .filter(CarListing.year.isnot(None), CarListing.olx_id.isnot(None))
            .group_by(CarListing.year)
            .order_by(CarListing.year.desc())
            .all()
        )
        available_seller_types = (
            _excl(q_base, "seller_type").with_entities(CarListing.seller_type, func.count(CarListing.id))
            .filter(CarListing.seller_type.isnot(None))
            .group_by(CarListing.seller_type)
            .order_by(CarListing.seller_type)
            .all()
        )
        available_steerings = (
            _excl(q_base, "steering").with_entities(CarListing.car_steering, func.count(CarListing.id))
            .filter(CarListing.car_steering.isnot(None))
            .group_by(CarListing.car_steering)
            .order_by(CarListing.car_steering)
            .all()
        )
        _all_features_raw = (
            _excl(q_base, "features").with_entities(CarListing.car_features)
            .filter(CarListing.car_features.isnot(None))
            .all()
        )
        _all_features_set = set()
        for (row,) in _all_features_raw:
            try:
                for f in json.loads(row):
                    _all_features_set.add(f)
            except (json.JSONDecodeError, TypeError):
                pass
        available_features = sorted((f, 0) for f in _all_features_set)

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
            available_seller_types=_sort_selected_first(available_seller_types, seller_type_filter),
            available_steerings=_sort_selected_first(available_steerings, steering_filter),
            available_features=available_features,
            selected_cartypes=cartype_filter,
            selected_motorpowers=motorpower_filter,
            selected_gearboxes=gearbox_filter,
            selected_years=year_filter,
            selected_seller_types=seller_type_filter,
            selected_steerings=steering_filter,
            selected_features=features_filter,
            new_ids=_new_ids,
            scrape_result=_last_scrape_result(),
            sort=sort,
            page=page,
            total_pages=total_pages,
            total=total,
            favorited={r[0] for r in session.query(FavoriteListing.olx_id).all()},
        )
    finally:
        session.close()


@app.route("/marcas")
def marcas():
    session = get_session()
    try:
        _ignored = session.query(IgnoredListing.olx_id).filter(IgnoredListing.olx_id.isnot(None))
        rows = (
            session.query(CarListing.brand, func.count(CarListing.id))
            .filter(CarListing.brand.isnot(None), CarListing.status == "active", ~CarListing.olx_id.in_(_ignored))
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
        _ignored = session.query(IgnoredListing.olx_id).filter(IgnoredListing.olx_id.isnot(None))
        cartype = request.args.get("cartype", "")
        motorpower = request.args.get("motorpower", "")
        transmission = request.args.get("transmission", "")
        seller_type = request.args.get("seller_type", "")
        price_min = _int_or_none(request.args.get("price_min"))
        price_max = _int_or_none(request.args.get("price_max"))
        year_min = _int_or_none(request.args.get("year_min"))
        year_max = _int_or_none(request.args.get("year_max"))
        q = session.query(CarListing.brand, CarListing.model, func.count(CarListing.id)).filter(
            CarListing.brand.isnot(None), CarListing.model.isnot(None),
            CarListing.status == "active",
            ~CarListing.olx_id.in_(_ignored),
        )
        if cartype:
            q = q.filter(CarListing.cartype == cartype)
        if motorpower:
            q = q.filter(CarListing.motorpower == motorpower)
        if transmission:
            q = q.filter(CarListing.transmission == transmission)
        if seller_type:
            q = q.filter(CarListing.seller_type == seller_type)
        if price_min:
            q = q.filter(CarListing.price >= price_min * CENTAVOS_PER_REAL)
        if price_max:
            q = q.filter(CarListing.price <= price_max * CENTAVOS_PER_REAL)
        if year_min:
            q = q.filter(CarListing.year >= year_min)
        if year_max:
            q = q.filter(CarListing.year <= year_max)
        rows = q.group_by(CarListing.brand, CarListing.model).order_by(CarListing.brand, CarListing.model).all()
        _base = session.query(CarListing).filter(CarListing.status == "active", ~CarListing.olx_id.in_(_ignored))
        if price_min:
            _base = _base.filter(CarListing.price >= price_min * CENTAVOS_PER_REAL)
        if price_max:
            _base = _base.filter(CarListing.price <= price_max * CENTAVOS_PER_REAL)
        if year_min:
            _base = _base.filter(CarListing.year >= year_min)
        if year_max:
            _base = _base.filter(CarListing.year <= year_max)
        cartypes = [r[0] for r in _base.with_entities(CarListing.cartype).filter(CarListing.cartype.isnot(None)).distinct().order_by(CarListing.cartype).all() if r[0]]
        motorpowers = [r[0] for r in _base.with_entities(CarListing.motorpower).filter(CarListing.motorpower.isnot(None)).distinct().order_by(CarListing.motorpower).all() if r[0]]
        transmissions = [r[0] for r in _base.with_entities(CarListing.transmission).filter(CarListing.transmission.isnot(None)).distinct().order_by(CarListing.transmission).all() if r[0]]
        seller_types = [r[0] for r in _base.with_entities(CarListing.seller_type).filter(CarListing.seller_type.isnot(None)).distinct().order_by(CarListing.seller_type).all() if r[0]]
        extra = {}
        if cartype: extra["cartype_filter"] = cartype
        if motorpower: extra["motorpower_filter"] = motorpower
        if transmission: extra["gearbox_filter"] = transmission
        if seller_type: extra["seller_type"] = seller_type
        if price_min: extra["price_min"] = price_min
        if price_max: extra["price_max"] = price_max
        if year_min: extra["year_min"] = year_min
        if year_max: extra["year_max"] = year_max
        return render_template("todos_modelos.html", modelos=rows, cartypes=cartypes, cartype=cartype,
                               motorpowers=motorpowers, motorpower=motorpower,
                               transmissions=transmissions, transmission=transmission,
                               seller_types=seller_types, seller_type=seller_type,
                               price_min=price_min, price_max=price_max,
                               year_min=year_min, year_max=year_max, model_extra=extra)
    finally:
        session.close()


@app.route("/marcas/<brand>")
def modelos(brand):
    session = get_session()
    try:
        _ignored = session.query(IgnoredListing.olx_id).filter(IgnoredListing.olx_id.isnot(None))
        rows = (
            session.query(CarListing.model, func.count(CarListing.id))
            .filter(CarListing.brand == brand, CarListing.model.isnot(None), ~CarListing.olx_id.in_(_ignored))
            .group_by(CarListing.model)
            .order_by(CarListing.model)
            .all()
        )
        return render_template("modelos.html", brand=brand, modelos=rows)
    finally:
        session.close()


@app.route("/ignore-batch", methods=["POST"])
def ignore_batch():
    ids = [int(v) for v in request.form.getlist("listing_id") if v.isdigit()]
    session = get_session()
    try:
        for listing in session.query(CarListing).filter(CarListing.id.in_(ids)):
            if listing.olx_id and not session.get(IgnoredListing, listing.olx_id):
                session.add(IgnoredListing(olx_id=listing.olx_id, title=listing.title))
        session.commit()
    finally:
        session.close()
    return redirect(request.referrer or "/")


@app.route("/ignore/<int:listing_id>")
def ignore_listing(listing_id):
    session = get_session()
    try:
        listing = session.get(CarListing, listing_id)
        if listing and listing.olx_id:
            existing = session.get(IgnoredListing, listing.olx_id)
            if not existing:
                session.add(IgnoredListing(olx_id=listing.olx_id, title=listing.title))
                session.commit()
    finally:
        session.close()
    return redirect(request.referrer or "/")


EDITABLE_FIELDS = ["brand", "model", "cartype", "motorpower", "transmission"]


@app.route("/edit/<int:listing_id>", methods=["POST"])
def edit_listing(listing_id):
    session = get_session()
    try:
        listing = session.get(CarListing, listing_id)
        if listing:
            edited = []
            for f in EDITABLE_FIELDS:
                if f in request.form:
                    val = request.form.get(f, "").strip() or None
                    if val != getattr(listing, f):
                        setattr(listing, f, val)
                        edited.append(f)
            if "notes" in request.form:
                listing.notes = request.form.get("notes", "").strip() or None
            listing.edited = json.dumps(edited) if edited else listing.edited
            session.commit()
    finally:
        session.close()
    return redirect(request.referrer or "/")


@app.route("/favorite/<int:listing_id>", methods=["POST"])
def favorite_listing(listing_id):
    session = get_session()
    try:
        listing = session.get(CarListing, listing_id)
        if listing and listing.olx_id:
            if not session.get(FavoriteListing, listing.olx_id):
                session.add(FavoriteListing(olx_id=listing.olx_id, title=listing.title))
                session.commit()
    finally:
        session.close()
    return ("", 200)


@app.route("/unfavorite/<int:listing_id>", methods=["POST"])
def unfavorite_listing(listing_id):
    session = get_session()
    try:
        listing = session.get(CarListing, listing_id)
        if listing and listing.olx_id:
            fav = session.get(FavoriteListing, listing.olx_id)
            if fav:
                session.delete(fav)
                session.commit()
    finally:
        session.close()
    return ("", 200)


@app.route("/favoritos")
def favoritos():
    session = get_session()
    try:
        q = session.query(CarListing).join(FavoriteListing, CarListing.olx_id == FavoriteListing.olx_id)
        listings = q.order_by(FavoriteListing.favorited_at.desc()).all()
        return render_template("favoritos.html", listings=listings)
    finally:
        session.close()


@app.route("/excluidos")
def excluded_listings():
    session = get_session()
    try:
        _ignored = session.query(IgnoredListing.olx_id).filter(IgnoredListing.olx_id.isnot(None))
        q = session.query(CarListing).filter(
            CarListing.status == "deleted",
            ~CarListing.olx_id.in_(_ignored),
        ).order_by(CarListing.updated_at.desc())
        listings = q.all()
        return render_template("excluidos.html", listings=listings,
                               favorited={r[0] for r in session.query(FavoriteListing.olx_id).all()})
    finally:
        session.close()


@app.route("/restore-active/<int:listing_id>", methods=["GET", "POST"])
def restore_active(listing_id):
    session = get_session()
    try:
        listing = session.get(CarListing, listing_id)
        if listing:
            listing.status = "active"
            session.commit()
    finally:
        session.close()
    return redirect(request.referrer or "/excluidos")


@app.route("/restore-active-batch", methods=["POST"])
def restore_active_batch():
    ids = [int(v) for v in request.form.getlist("listing_id") if v.isdigit()]
    session = get_session()
    try:
        session.query(CarListing).filter(CarListing.id.in_(ids)).update({"status": "active"}, synchronize_session=False)
        session.commit()
    finally:
        session.close()
    return ("", 200)


@app.route("/delete-listing/<int:listing_id>")
def delete_listing(listing_id):
    session = get_session()
    try:
        listing = session.get(CarListing, listing_id)
        if listing:
            if listing.olx_id:
                session.query(FavoriteListing).filter(FavoriteListing.olx_id == listing.olx_id).delete(synchronize_session=False)
            session.delete(listing)
            session.commit()
    finally:
        session.close()
    return redirect(request.referrer or "/excluidos")


@app.route("/delete-listing-batch", methods=["POST"])
def delete_listing_batch():
    ids = [int(v) for v in request.form.getlist("listing_id") if v.isdigit()]
    session = get_session()
    try:
        olx_ids = [r[0] for r in session.query(CarListing.olx_id).filter(CarListing.id.in_(ids), CarListing.olx_id.isnot(None)).all()]
        if olx_ids:
            session.query(FavoriteListing).filter(FavoriteListing.olx_id.in_(olx_ids)).delete(synchronize_session=False)
        session.query(CarListing).filter(CarListing.id.in_(ids)).delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()
    return ("", 200)


@app.route("/clear-deleted", methods=["POST"])
def clear_deleted():
    session = get_session()
    try:
        olx_ids = [r[0] for r in session.query(CarListing.olx_id).filter(CarListing.status == "deleted", CarListing.olx_id.isnot(None)).all()]
        if olx_ids:
            session.query(FavoriteListing).filter(FavoriteListing.olx_id.in_(olx_ids)).delete(synchronize_session=False)
        session.query(CarListing).filter(CarListing.status == "deleted").delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()
    return redirect(url_for("excluded_listings"))


@app.route("/ignorados")
def ignored_listings():
    session = get_session()
    try:
        q = session.query(CarListing).join(IgnoredListing, CarListing.olx_id == IgnoredListing.olx_id)
        sort = request.args.get("sort", "")
        order = IgnoredListing.ignored_at.desc()
        if sort == "price_asc":
            order = CarListing.price.asc().nullslast()
        elif sort == "price_desc":
            order = CarListing.price.desc().nullslast()
        listings = q.order_by(order).all()
        return render_template("ignorados.html", listings=listings)
    finally:
        session.close()


@app.route("/restore/<olx_id>")
def restore_listing(olx_id):
    session = get_session()
    try:
        ignored = session.get(IgnoredListing, olx_id)
        if ignored:
            session.delete(ignored)
            session.commit()
    finally:
        session.close()
    return redirect(request.referrer or "/")


COMPOUND_PATH = os.path.join(_project_root, "models_compostos.json")


@app.route("/modelos-compostos")
def modelos_compostos():
    try:
        with open(COMPOUND_PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = []
    return render_template("modelos_compostos.html", modelos=data)


@app.route("/modelos-compostos/save", methods=["POST"])
def save_compounds():
    data = request.get_json()
    if data is None:
        return ("JSON inválido", 400)
    with open(COMPOUND_PATH, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return ("", 200)


@app.route("/blacklist")
def blacklist_page():
    session = get_session()
    try:
        rules = session.query(BlacklistRule).order_by(BlacklistRule.created_at.desc()).all()
        motorpowers = [r[0] for r in session.query(CarListing.motorpower).filter(CarListing.motorpower.isnot(None)).distinct().order_by(CarListing.motorpower).all() if r[0]]
        transmissions = [r[0] for r in session.query(CarListing.transmission).filter(CarListing.transmission.isnot(None)).distinct().order_by(CarListing.transmission).all() if r[0]]
        brands = [r.name for r in session.query(Brand).order_by(Brand.name).all()]
        return render_template("blacklist.html", rules=rules, motorpowers=motorpowers, transmissions=transmissions, brands=brands)
    finally:
        session.close()


@app.route("/blacklist/add", methods=["POST"])
def blacklist_add():
    brand = request.form.get("brand", "").strip() or None
    model = request.form.get("model", "").strip() or None
    motorpower = request.form.get("motorpower", "").strip() or None
    transmission = request.form.get("transmission", "").strip() or None
    if not any([brand, model, motorpower, transmission]):
        return redirect(url_for("blacklist_page"))
    session = get_session()
    try:
        session.add(BlacklistRule(brand=brand, model=model, motorpower=motorpower, transmission=transmission))
        session.commit()
    finally:
        session.close()
    return redirect(request.referrer or url_for("blacklist_page"))


@app.route("/blacklist/delete/<int:rule_id>", methods=["POST"])
def blacklist_delete(rule_id):
    session = get_session()
    try:
        rule = session.get(BlacklistRule, rule_id)
        if rule:
            session.delete(rule)
            session.commit()
    finally:
        session.close()
    return redirect(url_for("blacklist_page"))


@app.route("/blacklist/apply", methods=["POST"])
def blacklist_apply():
    session = get_session()
    try:
        rules = session.query(BlacklistRule).all()
        added = 0
        for rule in rules:
            q = session.query(CarListing).filter(CarListing.status == "active", CarListing.olx_id.isnot(None))
            if rule.brand:
                q = q.filter(CarListing.brand == rule.brand)
            if rule.model:
                q = q.filter(CarListing.model == rule.model)
            if rule.motorpower:
                q = q.filter(CarListing.motorpower == rule.motorpower)
            if rule.transmission:
                q = q.filter(CarListing.transmission == rule.transmission)
            for listing in q.all():
                if listing.olx_id and not session.get(IgnoredListing, listing.olx_id):
                    session.add(IgnoredListing(olx_id=listing.olx_id, title=listing.title))
                    added += 1
        if added:
            session.commit()
    finally:
        session.close()
    return redirect(url_for("blacklist_page"))


@app.route("/blacklist/delete-batch", methods=["POST"])
def blacklist_delete_batch():
    ids = [int(v) for v in request.form.getlist("rule_id") if v.isdigit()]
    session = get_session()
    try:
        session.query(BlacklistRule).filter(BlacklistRule.id.in_(ids)).delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()
    return redirect(url_for("blacklist_page"))


DB_PATH = os.path.join(_project_root, "car_listings.db")


@app.route("/export")
def export_db():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(DB_PATH, "car_listings.db")
        cfg = os.path.join(_project_root, "config.py")
        if os.path.exists(cfg):
            zf.write(cfg, "config.py")
        if os.path.exists(COMPOUND_PATH):
            zf.write(COMPOUND_PATH, "models_compostos.json")
        if os.path.exists(CIDADES_PATH):
            zf.write(CIDADES_PATH, "cidades_permitidas.json")
        if os.path.exists(CURRENT_URL_FILE):
            zf.write(CURRENT_URL_FILE, ".current_url")
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name="carros-olx.zip")


@app.route("/import")
def import_page():
    return render_template("import.html")


@app.route("/import", methods=["POST"])
def import_db():
    f = request.files.get("file")
    if not f or not f.filename.endswith(".zip"):
        return redirect(url_for("import_page"))

    zf = zipfile.ZipFile(f.stream)
    tmp = _project_root + "/.car_listings_import.db"
    with open(tmp, "wb") as out:
        out.write(zf.read("car_listings.db"))
    if "config.py" in zf.namelist():
        zf.extract("config.py", _project_root)
    if "models_compostos.json" in zf.namelist():
        zf.extract("models_compostos.json", _project_root)
    if "cidades_permitidas.json" in zf.namelist():
        zf.extract("cidades_permitidas.json", _project_root)
    if ".current_url" in zf.namelist():
        zf.extract(".current_url", _project_root)
    zf.close()

    from models import engine, init_db
    engine.dispose()
    os.replace(tmp, DB_PATH)
    init_db()
    return redirect("/")


@app.route("/config")
def config_page():
    try:
        with open(CURRENT_URL_FILE) as f:
            url = f.read().strip()
    except FileNotFoundError:
        from config import START_URL
        url = START_URL
    try:
        with open(CURRENT_URL_SOCARRAO_FILE) as f:
            socarrao_url = f.read().strip()
    except FileNotFoundError:
        from config import SOCARRAO_URL
        socarrao_url = SOCARRAO_URL
    try:
        with open(CIDADES_PATH) as f:
            cidades = "\n".join(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        cidades = ""
    session = get_session()
    try:
        stats = {
            "total": session.query(CarListing).count(),
            "active": session.query(CarListing).filter(CarListing.status == "active").count(),
            "deleted": session.query(CarListing).filter(CarListing.status == "deleted").count(),
            "ignored": session.query(IgnoredListing).count(),
            "favorited": session.query(FavoriteListing).count(),
        }
    finally:
        session.close()
    return render_template("config.html", current_url=url, socarrao_url=socarrao_url, cidades=cidades, stats=stats)


@app.route("/save-socarrao-url", methods=["POST"])
def save_socarrao_url():
    url = request.form.get("socarrao_url", "").strip()
    if url:
        with open(CURRENT_URL_SOCARRAO_FILE, "w") as f:
            f.write(url)
    return redirect(url_for("config_page"))


LOG_FILE = os.path.join(_project_root, "scrape.log")


CIDADES_PATH = os.path.join(_project_root, "cidades_permitidas.json")
CURRENT_URL_FILE = os.path.join(_project_root, ".current_url")
CURRENT_URL_SOCARRAO_FILE = os.path.join(_project_root, ".current_url_socarrao")
SCRAPE_RESULT_FILE = os.path.join(_project_root, ".last_scrape.json")


def _save_scrape_result(start_time):
    """Lê o log e salva resultado da última execução."""
    import re
    try:
        with open(LOG_FILE) as f:
            text = f.read()
        m = re.search(r"Done! Scraped (\d+) listings total\.", text)
        if not m:
            m = re.search(r"Total: (\d+) anúncio\(s\)\.", text)
        count = int(m.group(1)) if m else 0
        elapsed = round((datetime.now(tz=timezone.utc) - start_time).total_seconds())
        with open(SCRAPE_RESULT_FILE, "w") as f:
            json.dump({"count": count, "finished_at": datetime.now(tz=timezone.utc).isoformat(), "elapsed": elapsed}, f)
    except Exception:
        pass


def _run_socarrao_scraper_background(url, clear):
    """Roda o scraper SóCarrão em background."""
    _start = datetime.now(tz=timezone.utc)
    with open(CURRENT_URL_SOCARRAO_FILE, "w") as f:
        f.write(url)
    with open(LOG_FILE, "w") as f:
        f.write(f"URL: {url}\n")
        if clear:
            session = get_session()
            removed = session.query(CarListing).filter(CarListing.source == "socarrao").delete(synchronize_session=False)
            session.commit()
            session.close()
            f.write(f"Anúncios SóCarrão removidos ({removed})\n\n")
        else:
            f.write("Atualizando anúncios existentes...\n\n")
        f.flush()
    with open(LOG_FILE, "a") as f:
        proc = subprocess.Popen(
            [sys.executable, "-u", os.path.join(_project_root, "run_scraper_socarrao.py")],
            stdout=f, stderr=subprocess.STDOUT,
            text=True, cwd=_project_root,
        )
        proc.wait()
    _save_scrape_result(_start)


def _run_scraper_background(url, cidades):
    """Roda o scraper em background escrevendo log em arquivo."""
    _start = datetime.now(tz=timezone.utc)
    with open(CIDADES_PATH, "w") as f:
        json.dump(cidades, f, indent=2, ensure_ascii=False)
    from clear_db import clear_db
    removed = clear_db()
    with open(CURRENT_URL_FILE, "w") as f:
        f.write(url)
    with open(LOG_FILE, "w") as f:
        f.write(f"URL: {url}\n")
        f.write(f"Banco limpo ({removed} registros removidos)\n\n")
        f.flush()
    env = {**os.environ, "SCRAPE_URL": url, "SCRAPE_START": _start.isoformat()}
    with open(LOG_FILE, "a") as f:
        proc = subprocess.Popen(
            [sys.executable, "-u", os.path.join(_project_root, "run_scraper.py")],
            stdout=f, stderr=subprocess.STDOUT,
            text=True, cwd=_project_root, env=env,
        )
        proc.wait()
    _save_scrape_result(_start)


def _run_scraper_background_noclear(url, cidades):
    """Roda o scraper em background sem limpar o banco."""
    _start = datetime.now(tz=timezone.utc)
    with open(CIDADES_PATH, "w") as f:
        json.dump(cidades, f, indent=2, ensure_ascii=False)
    with open(CURRENT_URL_FILE, "w") as f:
        f.write(url)
    with open(LOG_FILE, "w") as f:
        f.write(f"URL: {url}\nAtualizando anúncios existentes...\n\n")
        f.flush()
    env = {**os.environ, "SCRAPE_URL": url, "SCRAPE_START": _start.isoformat()}
    with open(LOG_FILE, "a") as f:
        proc = subprocess.Popen(
            [sys.executable, "-u", os.path.join(_project_root, "run_scraper.py")],
            stdout=f, stderr=subprocess.STDOUT,
            text=True, cwd=_project_root, env=env,
        )
        proc.wait()
    _save_scrape_result(_start)


@app.route("/clear-all", methods=["POST"])
def clear_all():
    session = get_session()
    try:
        ca = session.query(CarListing).delete()
        ig = session.query(IgnoredListing).delete()
        fv = session.query(FavoriteListing).delete()
        session.commit()
    finally:
        session.close()
    return redirect(url_for("config_page"))


@app.route("/clear-keep-favs", methods=["POST"])
def clear_keep_favs():
    session = get_session()
    try:
        favorited = session.query(FavoriteListing.olx_id).filter(FavoriteListing.olx_id.isnot(None))
        ca = session.query(CarListing).filter(~CarListing.olx_id.in_(favorited)).delete(synchronize_session=False)
        ig = session.query(IgnoredListing).delete()
        session.commit()
    finally:
        session.close()
    return redirect(url_for("config_page"))


@app.route("/run-scrape", methods=["POST"])
def run_scrape():
    url = request.form.get("url", "").strip()
    if not url:
        return redirect(url_for("config_page"))
    cidades_raw = request.form.get("cidades", "").strip()
    cidades = [c.strip() for c in cidades_raw.split("\n") if c.strip()]
    threading.Thread(target=_run_scraper_background, args=(url, cidades), daemon=True).start()
    return redirect(url_for("scraping_page"))


@app.route("/run-scrape-only", methods=["POST"])
def run_scrape_only():
    url = request.form.get("url", "").strip()
    if not url:
        return redirect(url_for("config_page"))
    cidades_raw = request.form.get("cidades", "").strip()
    cidades = [c.strip() for c in cidades_raw.split("\n") if c.strip()]
    threading.Thread(target=_run_scraper_background_noclear, args=(url, cidades), daemon=True).start()
    return redirect(url_for("scraping_page"))


@app.route("/run-scrape-socarrao", methods=["POST"])
def run_scrape_socarrao():
    url = request.form.get("socarrao_url", "").strip()
    if not url:
        return redirect(url_for("config_page"))
    threading.Thread(target=_run_socarrao_scraper_background, args=(url, True), daemon=True).start()
    return redirect(url_for("scraping_page"))


@app.route("/run-scrape-socarrao-only", methods=["POST"])
def run_scrape_socarrao_only():
    url = request.form.get("socarrao_url", "").strip()
    if not url:
        return redirect(url_for("config_page"))
    threading.Thread(target=_run_socarrao_scraper_background, args=(url, False), daemon=True).start()
    return redirect(url_for("scraping_page"))


@app.route("/scraping")
def scraping_page():
    return render_template("scraping.html")


@app.route("/scrape-log")
def scrape_log():
    if not os.path.exists(LOG_FILE):
        return Response("Aguardando scraper iniciar...\n", mimetype="text/plain")
    with open(LOG_FILE) as f:
        content = f.read()
    if not content.strip():
        return Response("Aguardando scraper iniciar...\n", mimetype="text/plain")
    return Response(content, mimetype="text/plain", headers={"Cache-Control": "no-cache"})


@app.route("/save-filter", methods=["POST"])
def save_filter():
    data = request.get_json()
    name = (data or {}).get("name", "").strip()
    params = (data or {}).get("params", "")
    if not name or not params:
        return ("", 400)
    session = get_session()
    try:
        session.add(SavedFilter(name=name, params=params))
        session.commit()
    finally:
        session.close()
    return ("", 200)


@app.route("/delete-filter/<int:fid>", methods=["POST"])
def delete_filter(fid):
    session = get_session()
    try:
        f = session.get(SavedFilter, fid)
        if f:
            session.delete(f)
            session.commit()
    finally:
        session.close()
    return ("", 200)


@app.route("/filters")
def list_filters():
    session = get_session()
    try:
        filters = session.query(SavedFilter).order_by(SavedFilter.created_at.desc()).all()
        return {"filters": [{"id": f.id, "name": f.name, "params": f.params} for f in filters]}
    finally:
        session.close()


@app.route("/scrape-details/<olx_id>")
def scrape_details(olx_id):
    force = request.args.get("force") == "1"
    session = get_session()
    try:
        listing = session.query(CarListing).filter(CarListing.olx_id == olx_id).first()
        if not listing or not listing.listing_url:
            return {"error": "Anúncio não encontrado"}, 404

        def _first_img():
            if listing.image_urls:
                try:
                    urls = json.loads(listing.image_urls)
                    return urls[0] if urls else None
                except (json.JSONDecodeError, IndexError):
                    return None
            return None

        def _listing_fields(desc, cached):
            return {
                "cached": cached,
                "title": listing.title,
                "description": desc,
                "olx_avg_price": listing.olx_avg_price,
                "fipe_price": listing.fipe_price,
                "listing_price": listing.price,
                "city": listing.city,
                "neighborhood": listing.neighborhood,
                "seller_type": listing.seller_type,
                "brand": listing.brand,
                "model": listing.model,
                "year": listing.year,
                "mileage": listing.mileage,
                "fuel": listing.fuel,
                "transmission": listing.transmission,
                "cartype": listing.cartype,
                "motorpower": listing.motorpower,
                "color": listing.color,
                "seller_name": listing.seller_name,
                "listing_date": listing.listing_date,
                "zip_code": listing.zip_code,
                "listing_url": listing.listing_url,
                "notes": listing.notes,
                "car_steering": listing.car_steering,
                "car_features": json.loads(listing.car_features) if listing.car_features else [],
                "created_at": listing.created_at.isoformat() if listing.created_at else None,
                "updated_at": listing.updated_at.isoformat() if listing.updated_at else None,
                "image_url": _first_img(),
                "image_urls": json.loads(listing.image_urls) if listing.image_urls else [],
            }

        if not force and listing.description:
            return _listing_fields(listing.description, True)

        description = None
        olx_avg_price = None
        fipe_price = None

        if listing.source == "socarrao" or (olx_id and olx_id.startswith("SC")):
            from playwright.sync_api import sync_playwright
            import time as _time
            try:
                with sync_playwright() as _pw:
                    _b = _pw.chromium.launch(headless=True, timeout=20000)
                    try:
                        _p = _b.new_page()
                        _p.goto(listing.listing_url, wait_until="domcontentloaded", timeout=30000)
                        _time.sleep(3)
                        _de = _p.locator("[class*=description]").first
                        if _de:
                            _raw = _de.text_content().strip()
                            description = _raw if _raw and "N\u00e3o h\u00e1 descri\u00e7\u00e3o" not in _raw else None
                    finally:
                        _b.close()
            except Exception as _e:
                return {"error": f"Erro ao acessar SóCarrão: {_e}"}, 502
        else:
            _scraper = cloudscraper.create_scraper()
            resp = _scraper.get(listing.listing_url, timeout=30)
            if resp.status_code != 200:
                return {"error": f"Erro ao acessar OLX: HTTP {resp.status_code}"}, 502

            sel = Selector(text=resp.text)

            raw_json = sel.css("script#initial-data::attr(data-json)").get()
            if raw_json:
                try:
                    data = json.loads(html.unescape(raw_json))
                    ad = data.get("ad") or {}
                    raw = ad.get("body") or ad.get("description") or ""
                    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
                    raw = re.sub(r"<[^>]+>", "", raw)
                    description = raw.strip() or None
                    fipe_raw = (ad.get("abuyFipePrice") or {}).get("fipePrice")
                    if fipe_raw is not None:
                        fipe_price = int(fipe_raw) * 100
                    priceref = ad.get("abuyPriceRef") or {}
                    avg_raw = priceref.get("price_p50")
                    if avg_raw is not None:
                        olx_avg_price = int(avg_raw) * 100
                    imgs = ad.get("images") or []
                    if imgs:
                        urls = [img["original"] for img in imgs if img.get("original")]
                        if urls:
                            listing.image_urls = json.dumps(urls)
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            if not description:
                paragraphs = sel.css("meta[property='og:description']::attr(content)").get()
                if paragraphs:
                    description = paragraphs.strip() or None

        listing.description = description
        listing.olx_avg_price = olx_avg_price
        listing.fipe_price = fipe_price
        session.commit()

        return _listing_fields(description, False)
    finally:
        session.close()


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
