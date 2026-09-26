def totals_by_month(rows) -> dict:
    out: dict = {}
    for date, account, cents, _memo in rows:
        month = date[:7]  #@MUT rep-month: month = date[:6]
        out.setdefault(month, {})
        out[month][account] = out[month].get(account, 0) + cents
    return out


def top_accounts(rows, n: int) -> list[str]:
    moved: dict = {}
    for _date, account, cents, _memo in rows:
        moved[account] = moved.get(account, 0) + abs(cents)  #@MUT rep-abs: moved[account] = moved.get(account, 0) + cents
    ranked = sorted(moved.items(), key=lambda kv: (-kv[1], kv[0]))  #@MUT rep-ties: ranked = sorted(moved.items(), key=lambda kv: -kv[1])
    return [name for name, _ in ranked[:n]]


def monthly_net(rows, account: str) -> dict:
    out: dict = {}
    for date, acc, cents, _memo in rows:
        if acc == account:
            out[date[:7]] = out.get(date[:7], 0) + cents
    return dict(sorted(out.items()))
