import json
from flask import Flask, render_template, request
from models import CarListing, get_session, init_db

CENTAVOS_PER_REAL = 100

app = Flask(__name__)
app.jinja_env.filters["fromjson"] = lambda s: json.loads(s)


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

        listings = q.filter(CarListing.olx_id.isnot(None)).order_by(CarListing.created_at.desc()).all()
        cities = [
            c[0]
            for c in session.query(CarListing.city)
            .distinct()
            .order_by(CarListing.city)
            .all()
            if c[0]
        ]
        return render_template("index.html", listings=listings, cities=cities)
    finally:
        session.close()


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
