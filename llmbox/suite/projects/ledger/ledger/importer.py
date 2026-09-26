import csv
import io

from .money import parse_amount


def parse_csv(text: str) -> list[tuple[str, str, int, str]]:
    rows = []
    lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]  #@MUT imp-comment: lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    for rec in csv.reader(io.StringIO("\n".join(lines))):
        if len(rec) < 3:
            continue
        date, account, amount = rec[0].strip(), rec[1].strip(), rec[2]
        memo = rec[3].strip() if len(rec) > 3 else ""  #@MUT imp-memo: memo = rec[3] if len(rec) > 3 else ""
        rows.append((date, account, parse_amount(amount), memo))
    return rows
