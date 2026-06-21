from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey
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
    cartype = Column(String)
    motorpower = Column(String)
    neighborhood = Column(String)
    zip_code = Column(String)
    seller_name = Column(String)
    seller_type = Column(String)
    brand = Column(String)
    model = Column(String)
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
    status = Column(String, default="active")
    edited = Column(Text, default=None)
    notes = Column(Text, default=None)
    olx_avg_price = Column(Integer, default=None)
    fipe_price = Column(Integer, default=None)
    car_steering = Column(String, default=None)
    car_features = Column(Text, default=None)
    source = Column(String, default="olx")


class IgnoredListing(Base):
    __tablename__ = "ignored_listings"
    olx_id = Column(String, primary_key=True)
    title = Column(String)
    ignored_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class FavoriteListing(Base):
    __tablename__ = "favorite_listings"
    olx_id = Column(String, primary_key=True)
    title = Column(String)
    favorited_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Brand(Base):
    __tablename__ = "brands"
    olx_id = Column(Integer, primary_key=True)
    name = Column(String)
    slug = Column(String, default=None)


class BlacklistRule(Base):
    __tablename__ = "blacklist_rules"

    id = Column(Integer, primary_key=True)
    brand = Column(String, default=None)
    model = Column(String, default=None)
    motorpower = Column(String, default=None)
    transmission = Column(String, default=None)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SavedFilter(Base):
    __tablename__ = "saved_filters"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    params = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def init_db():
    Base.metadata.create_all(engine)
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    cols = [c["name"] for c in inspector.get_columns("car_listings")]
    if "olx_avg_price" not in cols:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE car_listings ADD COLUMN olx_avg_price INTEGER"))
            conn.commit()
    if "fipe_price" not in cols:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE car_listings ADD COLUMN fipe_price INTEGER"))
            conn.commit()
    if "car_steering" not in cols:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE car_listings ADD COLUMN car_steering VARCHAR"))
            conn.commit()
    if "car_features" not in cols:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE car_listings ADD COLUMN car_features TEXT"))
            conn.commit()
    if "source" not in cols:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE car_listings ADD COLUMN source VARCHAR DEFAULT 'olx'"))
            conn.commit()


def get_session():
    return SessionLocal()
