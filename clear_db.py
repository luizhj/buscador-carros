#!/usr/bin/env python3
"""Remove todos os anúncios baixados do banco de dados."""
from models import get_session, CarListing

session = get_session()
count = session.query(CarListing).delete()
session.commit()
session.close()
print(f"Removidos {count} anúncios do banco.")
