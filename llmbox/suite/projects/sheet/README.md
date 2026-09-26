# sheet - a tiny spreadsheet engine (Python 3, stdlib only)

Implement `sheet.engine.Sheet`:

```python
s = Sheet()
s.set("A1", "10")          # raw input: number, text, or a formula starting with "="
s.set("B1", "=A1*2+1")
s.get("B1")                # -> 21
```

## Cells and values
- Cell names: one letter A-Z + a row number 1-99 (`A1` … `Z99`); case-insensitive (`b2` == `B2`). Any other name inside a
  formula (e.g. `AA1`, `A0`, `A100`) evaluates to the error `#REF!`.
- Raw input that parses as a number (`12`, `-3.5`, `1e3`) is a number; anything else not starting with `=` is text
  (keep it as given). `set(cell, "")` clears the cell.
- `get(cell)` returns the current value: a number (return an `int` when the value is integral, else `float`), a string,
  a bool (from comparisons), or an error string. Empty cells return `None`.
- Values are always up to date: after `set`, every `get` reflects the new inputs (no manual recalc).

## Formulas
- Numbers, cell references, ranges `A1:B3` (only as function arguments; rectangular, any corner order), string literals
  in double quotes (`"abc"`, no escapes), `TRUE`/`FALSE`.
- Operators, highest precedence first: `^` (power, RIGHT-associative), unary `-`, `* /`, `+ -`, `&` (text concatenation),
  comparisons `= <> < > <= >=` (return bool). Parentheses group.
  So `-2^2` = -4, `2^3^2` = 512, `1+2&"x"` = "3x".
- An empty cell counts as 0 in arithmetic and comparisons and as "" in `&`. Numbers in `&` use their `get` form
  (`2` not `2.0`). Bools in arithmetic count as 1/0.
- Functions (case-insensitive names; arguments are expressions or ranges):
  - `SUM`, `MIN`, `MAX`, `AVERAGE` - over all numbers in their arguments; text and empty cells inside ranges are
    ignored; `MIN`/`MAX` of no numbers = 0; `AVERAGE` of no numbers = `#DIV/0!`.
  - `COUNT` - how many numeric values (text, empty and bools inside ranges are not counted).
  - `IF(cond, a, b)` - evaluates only the chosen branch (errors in the other branch do not matter).
  - `ROUND(x, n)` - round half AWAY from zero to n decimals (n may be negative).
  - `LEN(x)` - length of the text form; `CONCAT(...)` - joins the text forms of all arguments (ranges row by row).

## Errors
Error values are the strings `#DIV/0!`, `#REF!`, `#VALUE!`, `#NAME?`, `#CYCLE!`.
- Division by zero -> `#DIV/0!`; text used where a number is needed (arithmetic, comparisons with a number, `^`) ->
  `#VALUE!`; unknown function -> `#NAME?`; bad reference -> `#REF!`.
- Errors propagate: if an operand or argument is an error, the result is that error; the FIRST error in left-to-right
  order wins. (Functions over ranges also propagate the first error found, row by row.)
- A formula that depends on itself, directly or through other cells, is a cycle: every cell on the cycle evaluates
  to `#CYCLE!`, and cells that depend on a cycle get `#CYCLE!` by propagation.
- A malformed formula (e.g. `=1+`, `=(2`) evaluates to `#VALUE!`.

Run the tests: `python3 -m unittest discover -s tests -v`
