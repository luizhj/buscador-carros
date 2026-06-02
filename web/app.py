import json
from flask import Flask, render_template, request
from models import CarListing, get_session, init_db

app = Flask(__name__)
app.jinja_env.filters["fromjson"] = lambda s: json.loads(s)


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
