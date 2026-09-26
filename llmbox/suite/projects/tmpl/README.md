# tmpl - a small template engine (Node.js, CommonJS, no dependencies)

Implement `render(template, data)` in `src/tmpl.js` (export `{ render, TemplateError }`).

## Output tags
- `{{ expr }}` outputs the value HTML-escaped (`& < > " '` -> `&amp; &lt; &gt; &quot; &#39;`).
- `{{{ expr }}}` outputs it raw (no escaping).
- `expr` is a variable path with dots: `user.name`, `items.0.title` (numeric segments index arrays). A missing path is
  `undefined`, which renders as the empty string; `null` also renders as "". Numbers render with `String(n)`, booleans
  as `true`/`false`.
- Filters are applied left to right with `|`: `{{ name | upper | truncate:5 }}`. Arguments follow a colon; several
  arguments are separated by commas; string arguments use double quotes. Filters:
  - `upper`, `lower`, `trim`
  - `default:X` - X when the value is `undefined`, `null` or `""`
  - `truncate:N` - if longer than N characters, the first N characters followed by `...` (so the result has N+3 chars)
  - `join:SEP` - joins an array with SEP (default `, ` when no argument)
  - `length` - length of a string or array
  - `date:FMT` - value is an ISO date string `YYYY-MM-DD`; FMT tokens `YYYY`, `MM`, `DD` are replaced
  - an unknown filter throws `TemplateError` (`message` contains the filter name)
- Escaping happens AFTER filters.

## Control tags
- `{% if expr %} ... {% elif expr %} ... {% else %} ... {% endif %}` - truthiness: `undefined`, `null`, `false`, `0`,
  `""` and EMPTY ARRAYS are false. `expr` may be `not expr`, or a comparison `a == b`, `a != b`, `a > b`, `a < b`
  where each side is a path, a number, or a double-quoted string.
- `{% for x in expr %} ... {% else %} ... {% endfor %}` - iterates an array; inside, `x` is the item and `loop.index`
  (1-based), `loop.first`, `loop.last` are available. The optional `{% else %}` renders when the array is empty or
  missing. Loops nest; the inner loop variable shadows outer names; `loop` always refers to the innermost loop.
- Whitespace control: a `-` right after `{%`/`{{` or right before `%}`/`}}` (e.g. `{%- if x -%}`) removes ALL whitespace
  (including newlines) on that side of the tag in the output.
- `{# comment #}` produces nothing.
- Unclosed or mismatched tags (`{% if %}` without `{% endif %}`, a stray `{% endfor %}`, an unterminated `{{`) throw
  `TemplateError`.

Run the tests: `node --test`
