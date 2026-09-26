import datetime as dt

from .models import Box

DEFAULT_BOXES = [
    Box("S", 20, 15, 10, 2000, 100),
    Box("M", 35, 25, 20, 8000, 250),
    Box("L", 50, 40, 35, 20000, 500),
]
HOLIDAYS = {dt.date(2026, 10, 12), dt.date(2026, 11, 2), dt.date(2026, 12, 8), dt.date(2026, 12, 25)}
