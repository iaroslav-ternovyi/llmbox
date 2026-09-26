import datetime as dt

TRANSIT = {1: 1, 2: 2, 3: 3, 4: 5}


def is_business(d: dt.date, holidays) -> bool:
    return d.weekday() < 5 and d not in holidays  #@MUT c-hol: return d.weekday() < 5


def next_business(d: dt.date, holidays) -> dt.date:
    d += dt.timedelta(days=1)
    while not is_business(d, holidays):
        d += dt.timedelta(days=1)
    return d


def estimate(ordered_at: dt.datetime, zone: int, service: str, holidays) -> dt.date:
    start = ordered_at.date()
    if ordered_at.hour >= 14 or not is_business(start, holidays):  #@MUT c-cutoff: if ordered_at.hour > 14 or not is_business(start, holidays):
        start = next_business(start, holidays)
    transit = TRANSIT[zone]
    if service == "express":
        transit = max(1, transit - 1)  #@MUT c-express: transit = transit - 1
    d = start
    for _ in range(transit):
        d = next_business(d, holidays)
    return d
