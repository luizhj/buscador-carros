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


class SavedFilter(Base):
    __tablename__ = "saved_filters"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    params = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def init_db():
    Base.metadata.create_all(engine)


def get_session():
    return SessionLocal()
