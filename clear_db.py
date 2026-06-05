#!/usr/bin/env python3
"""Remove anúncios baixados (preserva ignorados e favoritos)."""
from models import get_session, CarListing, IgnoredListing, FavoriteListing


def clear_db():
    session = get_session()
    ignored = session.query(IgnoredListing.olx_id).filter(IgnoredListing.olx_id.isnot(None))
    favorited = session.query(FavoriteListing.olx_id).filter(FavoriteListing.olx_id.isnot(None))
    preserved = ignored.union(favorited)
    count = session.query(CarListing).filter(~CarListing.olx_id.in_(preserved)).delete(synchronize_session=False)
    session.commit()
    session.close()
    return count


if __name__ == "__main__":
    count = clear_db()
    print(f"Removidos {count} anúncios do banco.")
