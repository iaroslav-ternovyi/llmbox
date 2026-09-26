# ledger

Small bookkeeping library. Amounts are integer **cents** everywhere inside the library.

- `ledger.money`: `parse_amount(text)` turns "1,234.56", "-12.5", "€7", "(3.10)" (parentheses = negative) into cents,
  rounding half up to the cent; `format_cents(cents)` gives "-1,234.56" style strings.
- `ledger.accounts`: `Account(name, kind)` with `deposit`, `withdraw`, `transfer`. Checking accounts may go down to
  -500.00 (overdraft); savings accounts may not go below zero. A failed withdraw/transfer raises `InsufficientFunds`
  and changes NOTHING (transfers are atomic).
- `ledger.interest`: `monthly_interest(balance_cents, year, month)` - savings interest for one month, tiered annual
  rates (up to and including 10,000.00: 1.0%; the part above: 2.0%), prorated by the actual number of days in that month
  over a 365-day year, rounded half up to the cent. Negative balances earn nothing.
- `ledger.importer`: `parse_csv(text)` -> list of `(date, account, cents, memo)`. Lines starting with `#` (after optional
  whitespace) and blank lines are ignored; fields may be quoted; amounts follow `parse_amount`.
- `ledger.report`: `totals_by_month(rows)` -> `{"YYYY-MM": {account: cents}}`; `top_accounts(rows, n)` -> the n account
  names with the largest total absolute movement, ties broken alphabetically; `monthly_net(rows, account)` ->
  `{"YYYY-MM": net_cents}` for one account, months sorted, months without movements omitted.

Run the tests: `python3 -m unittest discover -s tests -v`
