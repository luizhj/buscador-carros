#!/usr/bin/env python3
"""Remove todos os anúncios baixados do banco de dados."""
from models import get_session, CarListing


def clear_db():
    session = get_session()
    count = session.query(CarListing).delete()
    session.commit()
    session.close()
    return count


if __name__ == "__main__":
    count = clear_db()
    print(f"Removidos {count} anúncios do banco.")
