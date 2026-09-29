"""knowledge.regex: what a call to Python's `re` returns - findall with groups, split with captures, empty matches,
anchors and newlines, lookarounds, Unicode, flags - mixed with questions about things `re` does not have.

The answer key cannot be wrong: every snippet is generated from a seed and evaluated with CPython's own `re` when the
item is built (in process, no subprocess). Only behaviour that CPython 3.12 (the Mac) and 3.14 (the box) share is used:
no `\\B` on an empty string (3.14 changed it), no `\\z` (3.14 only), count= passed positionally only where 3.13+ merely
warns; re.error and its 3.13 name PatternError are both accepted. tests/test_real_engines.py re-runs every snippet of
50 seeds per level on python3.14 here and on the box and requires the same value.

Conventions of the knowledge block (knowledge.py): the questions of one kind, credit per question - right 1, UNKNOWN
1/3, wrong or invented 0 - and exactly 2 questions per item about things that do not exist (an invented function, flag,
keyword argument or Match method, or PCRE syntax like \\p{L} that Python's `re` rejects); NONEXISTENT or the exception
it raises is right for those. Answers are compared structurally (quotes and spacing of a repr do not matter, types do:
a list is not a tuple, '' is not None).

  1-2   findall / sub / split / search basics, greedy vs lazy, count=, IGNORECASE
  3-4   findall with one group or several (tuples), split keeping captured separators, maxsplit, backreferences in the
        replacement, alternation order (the first alternative wins), ^ with and without MULTILINE, `.` and newline,
        lookahead / lookbehind, named groups, subn
  5-6   an optional group that did not take part ('' in findall), backtracking between groups, a leading separator, a
        replacement function, `$` before a trailing newline, a repeated group keeps its last repetition, \\w and \\b on
        Unicode, alternation of groups, splitting on a lookahead, fullmatch backtracking into the second alternative
  7-8   empty matches (sub, split, finditer spans; Python 3.7+ rules), None from a group split did not use, an unmatched
        group in the replacement, re.I passed where count goes, overlapping matches via lookahead, span (-1, -1), spaces
        in a VERBOSE character class, $ in sub, flags not at the start, variable-width lookbehind, bad escapes in the
        replacement, possessive quantifiers, IGNORECASE matching the Kelvin sign and the long s
  9-10  aimed at the frontier: every question is a tuple of 2-4 such calls, right only when every part is; one made-up
        call hides at the end of a tuple (evaluating it raises, so NONEXISTENT is right and working out the rest is
        wasted); at level 10 two parts of every tuple are rarely met documented behaviour (the y_ generators)
"""
from __future__ import annotations

import ast
import re
import warnings

from . import knowledge as K
from .common import Item, rng

BLOCK = "knowledge"
GRADING = 1
HEAD = ("Quick questions about Python's `re` module. Each snippet runs on CPython 3.12 or newer with `re` imported. What does "
        "it evaluate to? Answer with the value's repr as the Python REPL shows it, or `raises <ExceptionName>` if it raises.")
WORDS = ["alpha", "beta", "gamma", "delta", "omega", "kappa", "sigma", "theta", "zeta", "iota", "rho", "tau"]
LOW = "abcdefghijklmnoprstuvwyz"


def _q(s: str) -> str:
    """A Python string literal for s: repr, single-quoted like the REPL."""
    return repr(s)


def _w(r, n=None):
    return r.choice(WORDS) if n is None else r.sample(WORDS, n)


def _num(r, lo=1, hi=999):
    return str(r.randint(lo, hi))


# ---- question generators: seed -> one Python expression ---------------------------------------------------------------

def g_digits(r):
    s = f"{_w(r)} {_num(r)} of {_num(r)}, {_w(r)} {_num(r, 1, 9)}"
    return f"re.findall(r'\\d+', {_q(s)})"


def g_sub_class(r):
    w = " ".join(_w(r, 2))
    return r.choice([f"re.sub(r'[aeiou]', '', {_q(w)})", f"re.sub(r'[^a-z]', '', {_q(w.title() + ' ' + _num(r))})"])


def g_split_comma(r):
    ws = _w(r, 4)
    s = ws[0] + ", " + ws[1] + "," + ws[2] + ",  " + ws[3]
    return f"re.split(r',\\s*', {_q(s)})"


def g_search_group(r):
    a, b = _w(r, 2)
    return f"re.search(r'(\\w+)@(\\w+)\\.org', {_q(f'mail {a}@{b}.org now')}).group({r.choice([1, 2])})"


def g_match_none(r):
    return r.choice([f"re.match(r'\\d+', {_q(_w(r) + ' ' + _num(r))})", f"re.match(r'[a-z]+', {_q(_num(r) + _w(r))})"])


def g_fullmatch_list(r):
    ws = [_w(r)[:3] + _num(r, 1, 9), _w(r)[:2] + _num(r, 10, 99), _w(r)[:3].capitalize() + _num(r, 1, 9), r.choice(LOW) + _num(r, 1, 9)]
    r.shuffle(ws)
    return f"[bool(re.fullmatch(r'[a-z]+\\d', w)) for w in {ws!r}]"


def g_findall_group1(r):
    a, b, c = _w(r, 3)
    return f"re.findall(r'(\\w+)=\\d+', {_q(f'{a}={_num(r, 1, 99)} {b}=x {c}={_num(r, 1, 99)}')})"


def g_greedy_tag(r):
    a, b = _w(r, 2)
    return f"re.findall(r'<.+>', {_q(f'<b>{a}</b> <i>{b}</i>')})"


def g_lazy_tag(r):
    a, b = _w(r, 2)
    return f"re.findall(r'<.+?>', {_q(f'<b>{a}</b> x <i>{b}</i>')})"


def g_sub_count(r):
    s = r.choice(LOW) + _num(r, 1, 9) + r.choice(LOW) + _num(r, 10, 99) + r.choice(LOW) + _num(r, 100, 999)
    return f"re.sub(r'\\d', '#', {_q(s)}, count={r.randint(2, 4)})"


def g_ignorecase(r):
    w = _w(r)[:3]
    variants = [w, w.upper(), w.capitalize(), w[0] + w[1:].upper()]
    r.shuffle(variants)
    return f"re.findall({_q(w)}, {_q(' '.join(variants + [_w(r)]))}, re.I)"


def g_match_prefix(r):
    return f"re.match(r'[a-z]+', {_q(_w(r) + _num(r) + _w(r))}).group()"


def g_findall_tuples(r):
    a, b, c = _w(r, 3)
    return f"re.findall(r'(\\w+)=(\\d+)', {_q(f'{a}={_num(r, 1, 99)}, {b}={_num(r, 1, 99)}, {c}=x')})"


def g_split_capture(r):
    ws = _w(r, 4)
    s = ws[0] + "," + ws[1] + ";" + ws[2] + "," + ws[3]
    return f"re.split(r'([,;])', {_q(s)})"


def g_split_maxsplit(r):
    ws = _w(r, 4)
    s = ws[0] + " , " + ws[1] + ";" + ws[2] + " ; " + ws[3]
    return f"re.split(r'\\s*[,;]\\s*', {_q(s)}, maxsplit={r.choice([1, 2])})"


def g_sub_backref(r):
    a, b, c, d = _w(r, 4)
    return f"re.sub(r'(\\w+)@(\\w+)', r'\\2 at \\1', {_q(f'{a}@{b} {c}@{d}')})"


def g_alternation(r):
    x = r.choice(["ab|abc", "a|ab", "cat|category"])
    s = {"ab|abc": "abcabc ab", "a|ab": "abab a", "cat|category": "category cat"}[x]
    return f"re.findall(r'{x}', {_q(s)})"


def g_caret(r):
    ws = _w(r, 5)
    s = f"{ws[0]} {ws[1]}\n{ws[2]} {ws[3]}\n{ws[4]}"
    return r.choice([f"re.findall(r'^\\w+', {_q(s)})", f"re.findall(r'^\\w+', {_q(s)}, re.M)", f"re.findall(r'\\w+$', {_q(s)})"])


def g_dot_newline(r):
    return f"re.findall(r'a.c', {_q('abc a' + chr(10) + 'c a c aXc')})"


def g_lookahead(r):
    vals = [_num(r, 1, 99) + u for u in r.sample(["px", "em", "px", "pt", "px"], 4)]
    return f"re.findall(r'\\d+(?=px)', {_q(' '.join(vals))})"


def g_lookbehind(r):
    return f"re.findall(r'(?<=\\$)\\d+', {_q(f'cost ${_num(r, 1, 99)}, tax {_num(r, 1, 9)}, tip ${_num(r, 1, 9)}')})"


def g_named_sub(r):
    d = f"2026-{r.randint(1, 12):02d}-{r.randint(1, 28):02d}"
    return f"re.sub(r'(?P<y>\\d{{4}})-(?P<m>\\d\\d)-(?P<d>\\d\\d)', r'\\g<d>/\\g<m>/\\g<y>', {_q('due ' + d)})"


def g_subn(r):
    ws = _w(r, 3)
    return f"re.subn(r'\\s+', ' ', {_q(' ' + ws[0] + '  ' + ws[1] + '   ' + ws[2] + ' ')})"


def g_lazy_groups(r):
    return f"re.search(r'(\\d+?)(\\d+)', {_q('x' + _num(r, 1000, 99999))}).groups()"


def g_findall_optional(r):
    vals = [_num(r, 1, 99) + u for u in ["px", "", "em", ""]]
    r.shuffle(vals)
    return f"re.findall(r'(\\d+)(px|em)?', {_q(' '.join(vals))})"


def g_backtrack(r):
    return f"re.search(r'(\\d+)(\\d{{{r.choice([2, 3])}}})', {_q('id ' + _num(r, 10000, 999999))}).groups()"


def g_split_leading(r):
    a, b = _w(r, 2)
    return f"re.split(r'[,;]', {_q(f',{a};;{b},')})"


def g_replfunc(r):
    s = " ".join(r.choice(LOW) + _num(r, 0, 20) for _ in range(3))
    return f"re.sub(r'\\d+', lambda m: str(int(m.group()) * 2), {_q(s)})"


def g_backref_double(r):
    s = r.choice(["bookkeeper", "committee", "balloon", "mississippi"]) + " " + r.choice(["aa ab", "xx yz zz", "oo o"])
    return f"re.findall(r'(\\w)\\1', {_q(s)})"


def g_dollar_newline(r):
    s = f"total {_num(r)}\n"
    return r.choice([f"re.search(r'\\d+$', {_q(s)}).group()", f"re.search(r'\\d+\\Z', {_q(s)})", f"re.fullmatch(r'\\w+ \\d+', {_q(s)})"])


def g_findall_repeat(r):
    return r.choice([f"re.findall(r'(\\d)+', {_q(_num(r, 100, 999) + ' ' + _num(r, 10, 99) + ' ' + _num(r, 1, 9))})",
                     f"re.findall(r'(ab)+', {_q('ababx abab ab')})"])


def g_unicode_word(r):
    s = r.choice(["naïve café au lait", "über straße 12", "façade déjà vu"])
    return r.choice([f"re.findall(r'\\w+', {_q(s)})", f"re.findall(r'\\w+', {_q(s)}, re.A)",
                     f"re.findall(r'\\b\\w{{3}}\\b', {_q(s)})"])


def g_neg_lookahead(r):
    ws = ["undo", "unit", "able", "under", "uncle", "tuna"]
    r.shuffle(ws)
    return f"re.findall(r'\\b(?!un)\\w+', {_q(' '.join(ws[:4]))})"


def g_alt_groups(r):
    s = _num(r, 1, 99) + r.choice(LOW) + r.choice(LOW) + " " + _num(r, 1, 9)
    return f"re.findall(r'(\\d+)|([a-z]+)', {_q(s)})"


def g_split_camel(r):
    s = r.choice(["getHTTPResponseCode", "parseXMLDoc", "loadURLList", "toJSONString"])
    return f"re.split(r'(?=[A-Z])', {_q(s)})"


def g_fullmatch_alt(r):
    return r.choice(["re.fullmatch(r'ab|abc', 'abc').group()", "re.match(r'ab|abc', 'abc').group()",
                     "re.fullmatch(r'(a|ab)(c|bcd)', 'abcd').groups()", "re.match(r'(a|ab)(c|bcd)(d*)', 'abcd').groups()"])


# -- levels 7-8 ------------------------------------------------------------------------------------------------------------

def g_empty_sub(r):
    s = r.choice(LOW) + r.choice(LOW) + "x" * r.randint(1, 2) + r.choice(LOW)
    return r.choice([f"re.sub(r'x*', '-', {_q(s)})", f"re.sub(r'x?', '-', {_q(s)})"])


def g_empty_split(r):
    a, b = _w(r)[:2], _w(r)[:2]
    return r.choice([f"re.split(r'\\b', {_q(a + ' ' + b)})", f"re.split(r'x*', {_q(a + 'x' + b)})",
                     f"re.split(r'\\s*', {_q(' ' + a[0] + ' ' + b[0] + ' ')})"])


def g_split_none(r):
    a, b, c = _w(r, 3)
    return f"re.split(r'(,)|;', {_q(f'{a},{b};{c}')})"


def g_sub_unmatched(r):
    return r.choice(["re.sub(r'(a)|b', r'[\\1]', 'abc')", "re.sub(r'(x)?y', r'<\\1>', 'xyy')"])


def g_positional_count(r):
    s = "".join(r.choice(["a", "A"]) for _ in range(5))
    return f"re.sub('a', 'b', {_q(s + 'a')}, re.I)"


def g_overlap(r):
    s = r.choice([_num(r, 10000, 99999), _w(r)[:4]])
    return f"re.findall(r'(?=(\\w\\w))', {_q(s)})"


def g_span_none(r):
    return r.choice(["re.match(r'(a)(b)?', 'ac').span(2)", "re.match(r'(?P<x>a)(?P<y>b)?', 'ac').groupdict()",
                     "re.match(r'(a)(b)?(c)?', 'ac').lastindex", "re.match(r'(a)(b)?', 'ac').group(2)"])


def g_verbose_class(r):
    return r.choice(["re.findall(r'[a b]+', 'a b ab', re.X)", "re.findall(r'a b', 'a bab', re.X)",
                     "re.findall(r'a\\ b', 'a bab', re.X)", "re.findall(r'\\d+ # digits', '12 34', re.X)"])


def g_finditer_empty(r):
    s = r.choice(LOW) + _num(r, 10, 99) + r.choice(LOW)
    return f"[m.span() for m in re.finditer(r'\\d*', {_q(s)})]"


def g_dollar_sub(r):
    a, b = r.choice(LOW), r.choice(LOW)
    return r.choice([f"re.sub(r'$', '!', {_q(a + chr(10) + b + chr(10))})", f"re.sub(r'(?m)^', '> ', {_q(a + chr(10) + b + chr(10))})",
                     f"re.sub(r'(?m)$', ';', {_q(a + chr(10) + b + chr(10))})"])


def g_global_flag(r):
    return r.choice(["re.findall(r'x(?i)y', 'xY')", "re.findall(r'(?i)x', 'xX')", "re.findall(r'(?i:x)y', 'XY Xy')"])


def g_lookbehind_var(r):
    return r.choice(["re.findall(r'(?<=ab|c)d', 'abd cd')", "re.findall(r'(?<=ab|cd)e', 'abe cde')",
                     "re.findall(r'(?<=a+)b', 'aab')"])


def g_bad_repl(r):
    return r.choice(["re.sub(r'(\\w)(\\d)', r'\\20', 'a1')", "re.sub(r'(\\w)(\\d)', r'\\g<2>0', 'a1')",
                     "re.sub(r'a', r'\\d', 'a')", "re.sub(r'a', '\\\\n', 'a')"])


def g_group_last(r):
    s = "".join(r.choice("ab") for _ in range(r.randint(3, 5))) + "c"
    return f"re.search(r'(a|b)*c', {_q(s)}).groups()"


def g_possessive(r):
    return r.choice(["re.findall(r'\\d++5', '12345 555')", "re.findall(r'\\d+5', '12345 555')",
                     "re.fullmatch(r'(?>a*)a', 'aaa')", "re.findall(r'x{1,3}+x', 'xxxxx')"])


def g_unicode_icase(r):
    # the Kelvin sign (U+212A) prints exactly like K: ask for something that shows whether it matched, not the glyph
    return r.choice(["re.sub(r'[a-z]+', '_', 'Stra\\u00dfe \\u212aelvin \\u017fun', flags=re.I)",
                     "re.sub(r'k', '_', 'K k \\u212a', flags=re.I)", "re.findall(r'i', '\\u0130 I \\u0131', re.I)",
                     "len(re.findall(r'[a-z]', '\\u212a\\u017f', re.I | re.A))", "len(re.findall(r'[a-z]', '\\u212a\\u017f', re.I))"])


# -- extra parts for levels 9-10: several rules in one call ----------------------------------------------------------

def x_split_mix(r):
    return r.choice(["re.split(r'(-)|(?=[A-Z])', 'aB-cD')", "re.split(r'(x)?', 'axb')", "re.split(r'(?:x)?', 'axb')",
                     "re.split(r'(\\d)', 'a1b2c3d', maxsplit=2)"])


def x_sub_lookaround(r):
    return r.choice(["re.sub(r'(?<=a)', '-', 'aaa')", "re.sub(r'\\B(?=(\\d{3})+(?!\\d))', ',', '1234567')",
                     "re.sub(r'(?=a)', '^', 'bab')", "re.sub(r'(?<!\\d)(\\d{3})(?=(\\d{3})+(?!\\d))', r'\\1,', '1234567')"])


def x_nested(r):
    return r.choice(["re.findall(r'((\\w)\\w)', 'abcde')", "re.findall(r'((a)|b)+', 'abba ab')",
                     "re.search(r'(?:(a)|b)*', 'ab').groups()", "re.findall(r'(a)(?:(b)|c)', 'ab ac')"])


def x_template(r):
    return r.choice(["re.sub(r'(\\w+)', r'<\\g<0>>\\n', 'a b')", "re.sub(r'(?P<w>\\w+)', r'\\g<w>\\g<0>', 'ab c')",
                     "re.sub(r'(\\w)', r'\\g<1>0', 'ab')", "re.sub(r'a', r'\\\\', 'bab')"])


def x_backref_case(r):
    return r.choice(["re.findall(r'(?i)(\\w)\\1', 'aAbBcd')", "re.findall(r'(\\w)\\1', 'aAbBcc')",
                     "re.findall(r'(?P<a>x)(?P=a)', 'xx xX')"])


def x_verbose_hash(r):
    return r.choice(["re.findall(r'\\d+\\#?[a-z] # note', '12a 3#b', re.X)", "re.findall(r'[#]\\w+ #x', '#ab #c', re.X)",
                     "re.fullmatch(r'(?x) a  b # c', 'ab') is not None"])


def x_fullmatch_backtrack(r):
    n = r.randint(4, 6)
    return r.choice([f"re.fullmatch(r'(a+)(a+)(a+)', {_q('a' * n)}).groups()", f"re.fullmatch(r'(a+?)(a+)(a+?)', {_q('a' * n)}).groups()",
                     f"re.match(r'(a*)(a+)', {_q('a' * n)}).groups()"])


def x_dotall(r):
    return r.choice(["re.findall(r'<(.*)>', '<a>\\n<b>', re.S)", "re.findall(r'<(.*)>', '<a>\\n<b>')",
                     "re.findall(r'^a.', 'ab\\nAc\\na\\n', re.I | re.M)", "re.findall(r'.$', 'ab\\ncd\\n')"])


def x_escape(r):
    return r.choice(["re.escape('1+1=2? (yes)')", "re.escape('a.b-c d_e/f')", "re.escape('50% #1 ~x')"])


def x_unicode_digit(r):
    return r.choice(["re.findall(r'\\d+', '12\\u0663\\u00b2 x\\u0661')", "re.findall(r'\\d+', '12\\u0663\\u00b2 x\\u0661', re.A)",
                     "re.findall(r'[0-9]+', '4\\u0664 5')", "re.findall(r'\\s', 'a\\u00a0b\\u2003c\\x1cd')"])


def x_posix_class(r):
    return r.choice(["re.findall(r'[[:digit:]]+', 'a1:b]d]')", "re.findall(r'[^\\W\\d_]+', 'ab_c1d\\u00e9')",
                     "re.findall(r'[\\w-]+', 'a-b c_d')", "re.findall(r'[]a]+', 'a]b')"])


def x_multiline_end(r):
    return r.choice(["re.findall(r'$', 'a\\nb\\n')", "re.findall(r'(?m)$', 'a\\n\\nb')", "re.findall(r'\\w+$', 'ab\\ncd\\n', re.M)",
                     "re.findall(r'^', 'a\\n', re.M)"])


# -- level 10 (v0.11 hardening: Claude Opus answered the first level 10 all right): documented behaviour few people
# have met - pattern methods' pos / endpos (^ and \b still see the real string), .flags with UNICODE (32) set, negative
# count / maxsplit, the empty match right after a non-empty one, lastgroup, groups(default=), a replacement function
# that returns None, forward references and redefined names, split on ^ / lookarounds, $ before the last of several
# newlines, repeated groups that match empty, case-insensitive ranges and negated classes, bytes patterns

def y_flags(r):
    return r.choice(["re.compile('a').flags", "re.compile('a', re.I).flags", "re.compile('(?x)a', re.I).flags",
                     "re.compile(b'a', re.I).flags", "re.compile('a', re.A | re.M).flags", "re.compile('(?s)a', re.X).flags"])


def y_pos(r):
    return r.choice(["re.compile('^a').search('ba', 1)", "re.compile('^a').match('ba', 1)", "re.compile(r'a$').search('ab', 0, 1).span()",
                     "re.compile(r'\\d+').fullmatch('12a', 0, 2).group()", "re.compile(r'(?<=b)a').search('ba', 1).span()",
                     "re.compile(r'\\ba').search('ba', 1)", "re.compile('a').findall('aaaa', 1, 3)", "re.compile(r'\\Aa').search('ba', 1)"])


def y_negcount(r):
    return r.choice(["re.sub('x', 'y', 'xxx', count=-1)", "re.split('x', 'axbxc', maxsplit=-1)", "re.compile('a').sub('x', 'aaa', -1)",
                     "re.split('x', 'axbxc', maxsplit=0)", "re.subn('x', 'y', 'xax', count=0)"])


def y_empty_after(r):
    return r.choice(["re.sub('x*', '-', 'axx')", "re.split('x*', 'xxa')", "re.findall(r'a|', 'ab')", "re.sub(r'a|', '-', 'ab')",
                     "re.search('x*', 'axx').span()", "re.sub('x*', '-', 'xxa')", "re.subn(r'', '-', '')"])


def y_lastgroup(r):
    return r.choice(["re.match(r'(?P<n>a)|(?P<m>b)', 'b').lastgroup", "re.match(r'(a)|(b)', 'b').lastgroup",
                     "re.match(r'(a)(b)?', 'a').groups(default='-')", "re.match(r'(a)(b)', 'ab').expand(r'\\2-\\1')",
                     "re.match(r'(a)(b)', 'ab').group(0, 2)", "re.match(r'(?P<x>a)(?P<y>b)?', 'a').groupdict('?')"])


def y_replnone(r):
    return r.choice(["re.sub('x', lambda m: None, 'axb')", "re.sub('x', lambda m: '', 'axb', count=1)",
                     "re.subn('(x)', lambda m: m.group(1) * 2, 'xax')"])


def y_badgroups(r):
    return r.choice(["re.fullmatch(r'(\\2two|(one))+', 'oneonetwo')", "re.compile(r'(?P<x>a)(?P<x>b)')",
                     "re.sub(r'x', r'\\g<-1>', 'x')", "re.sub(r'(\\d)', r'\\g<01>', 'a1')", "re.compile(r'(a)(?:b)(c)').groups",
                     "dict(re.compile(r'(?P<a>x)(y)(?P<b>z)').groupindex)"])


def y_split_anchor(r):
    return r.choice(["re.split(r'^', 'a\\nb', flags=re.M)", "re.split(r'(?=a)', 'aab')", "re.split(r'(?<=a)', 'aab')",
                     "re.sub(r'(?<=x)|(?=x)', '-', 'axb')", "re.split(r'\\s+', ' a b ', maxsplit=1)", "re.split(r'(\\s)+', ' a  b')"])


def y_dollar(r):
    return r.choice(["re.findall(r'$', 'ab\\n\\n')", "re.sub(r'$', '!', 'a\\n\\n')", "re.search(r'\\n$', 'a\\n\\n').span()",
                     "re.findall(r'^$', 'a\\n\\nb\\n', re.M)", "re.search(r'^$', '\\n').span()", "re.findall(r'.$', 'ab\\n', re.S)"])


def y_repeat_empty(r):
    return r.choice(["re.findall(r'(?:(a)|(b))+', 'ab ba')", "re.match(r'(a*)+', 'aa').groups()", "re.fullmatch(r'(a+)+b', 'aaab').groups()",
                     "re.match(r'(a?)+?', 'aa').span()", "re.findall(r'(a|ab)(c|bcd)?', 'abcd')", "re.search(r'a??b', 'aab').group()"])


def y_case_class(r):
    return r.choice(["re.findall(r'[Z-a]', 'Z_a[^`A', re.I)", "re.findall(r'[^a]', 'aAb', re.I)",
                     "len(re.findall(r'(?i)[^k]', 'kK\\u212ax'))", "re.findall(r'(?i)\\u00df', 'SS ss \\u00df \\u1e9e')",
                     "re.findall(r'\\W', 'a_\\u00e9-', re.A)", "re.findall(rb'\\w+', 'n\\u00e9'.encode())"])


GEN = {f.__name__: f for f in [g_digits, g_sub_class, g_split_comma, g_search_group, g_match_none, g_fullmatch_list,
                               g_findall_group1, g_greedy_tag, g_lazy_tag, g_sub_count, g_ignorecase, g_match_prefix,
                               g_findall_tuples, g_split_capture, g_split_maxsplit, g_sub_backref, g_alternation, g_caret,
                               g_dot_newline, g_lookahead, g_lookbehind, g_named_sub, g_subn, g_lazy_groups,
                               g_findall_optional, g_backtrack, g_split_leading, g_replfunc, g_backref_double, g_dollar_newline,
                               g_findall_repeat, g_unicode_word, g_neg_lookahead, g_alt_groups, g_split_camel, g_fullmatch_alt,
                               g_empty_sub, g_empty_split, g_split_none, g_sub_unmatched, g_positional_count, g_overlap,
                               g_span_none, g_verbose_class, g_finditer_empty, g_dollar_sub, g_global_flag, g_lookbehind_var,
                               g_bad_repl, g_group_last, g_possessive, g_unicode_icase, x_split_mix, x_sub_lookaround, x_nested,
                               x_template, x_backref_case, x_verbose_hash, x_fullmatch_backtrack, x_dotall, x_escape,
                               x_unicode_digit, x_posix_class, x_multiline_end, y_flags, y_pos, y_negcount, y_empty_after,
                               y_lastgroup, y_replnone, y_badgroups, y_split_anchor, y_dollar, y_repeat_empty, y_case_class]}
RARE = [n for n in GEN if n.startswith("y_")]   # level 10: two of these in every tuple
MIX = {
    1: ["g_digits", "g_sub_class", "g_split_comma", "g_search_group", "g_match_none", "g_fullmatch_list"],
    2: ["g_findall_group1", "g_greedy_tag", "g_lazy_tag", "g_sub_count", "g_ignorecase", "g_match_prefix"],
    3: ["g_findall_tuples", "g_split_capture", "g_split_maxsplit", "g_sub_backref", "g_alternation", "g_caret"],
    4: ["g_dot_newline", "g_lookahead", "g_lookbehind", "g_named_sub", "g_subn", "g_lazy_groups"],
    5: ["g_findall_optional", "g_backtrack", "g_split_leading", "g_replfunc", "g_backref_double", "g_dollar_newline"],
    6: ["g_findall_repeat", "g_unicode_word", "g_neg_lookahead", "g_alt_groups", "g_split_camel", "g_fullmatch_alt"],
    7: ["g_empty_sub", "g_empty_split", "g_split_none", "g_sub_unmatched", "g_positional_count", "g_overlap", "g_span_none",
        "g_verbose_class"],
    8: ["g_finditer_empty", "g_dollar_sub", "g_global_flag", "g_lookbehind_var", "g_bad_repl", "g_group_last", "g_possessive",
        "g_unicode_icase"],
}
PARTS_910 = MIX[7] + MIX[8] + [n for n in GEN if n.startswith("x_")]
SIZES = {9: [2, 2, 3, 3, 3, 3, 3, 3], 10: [3, 3, 3, 3, 3, 4, 4, 4, 4, 4]}   # parts per real question; + 2 made up

FAKES = {   # (band, expression): each must raise AttributeError / TypeError / re.error on 3.12 and 3.14
    1: ["re.findfirst(r'\\d+', 'a1 b2')", "re.matchall(r'\\w', 'ab')", "re.replace(r'a', 'b', 'aa')", "re.count(r'a', 'banana')",
        "re.findall(r'a', 'A', re.NOCASE)", "re.sub(r'a', 'b', 'aa', flags=re.GLOBAL)", "re.search(r'x', 'xyz').text",
        "re.findall(r'\\d', 'a1', re.DIGITS)"],
    5: ["re.split(r',', 'a,b,c', limit=1)", "re.findall(r'aa', 'aaaa', overlapped=True)", "re.sub(r'a', 'b', 'aaa', max=1)",
        "re.match(r'(a)+', 'aa').captures(1)", "re.search(r'\\d', 'a1').spans()", "re.findall(r'a+?', 'aa', re.UNGREEDY)",
        "re.compile(r'\\w+').replace('-', 'a b')", "re.search(r'a', 'ba', re.ANCHORED)", "re.fullmatch(r'a', 'a').fullgroup()",
        "re.splititer(r',', 'a,b')", "re.search(r'b', 'ab', start=1)", "re.findall(r'\\w', 'ab', re.EXTENDED)"],
    9: ["re.findall(r'\\p{L}+', 'ab1')", "re.search(r'(?<name>a)', 'a')", "re.sub(r'\\h', '', 'a b')",
        "re.match(r'(a)(b)', 'ab').groups(named=True)", "re.compile(r'a').names", "re.findall(r'a', 'aA', re.CASELESS)",
        "re.sub(r'a', 'b', 'aaa', limit=2)", "re.search(r'\\d+', 'a12').lastmatch", "re.split(r'(,)', 'a,b', keep=False)"],
}


# ---- evaluation and grading -----------------------------------------------------------------------------------------

def evaluate(expr: str) -> dict:
    """{'value': v} or {'exc': [names]}; re.error is accepted as `error` or its 3.13 name `PatternError`."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            v = eval(expr, {"re": re})
        return {"value": v}
    except re.error:
        return {"exc": ["error", "PatternError"]}
    except Exception as e:
        return {"exc": [type(e).__name__]}


def _accept(res: dict) -> dict | None:
    if "exc" in res:
        return {"exc": res["exc"]}
    v = res["value"]
    try:
        if repr(K._canon(ast.literal_eval(repr(v)))) != repr(K._canon(v)):
            return None
    except (ValueError, SyntaxError):
        return None            # a Match object or similar: not a question we ask
    return {"repr": repr(v), "canon": repr(K._canon(v)), "type": type(v).__name__, "plain": _plain(v)}


def _plain(v) -> str | None:
    """A string value that may also be written without quotes: only when the bare text is no other Python literal
    ('a-b' yes; '42', 'None', "['a']" no)."""
    if not isinstance(v, str) or not v or v != v.strip() or re.search(r"['\"\\\n]", v):
        return None
    try:
        ast.literal_eval(v)
        return None
    except (ValueError, SyntaxError):
        return v


def _question(expr: str, level: int, fake: bool = False, parts: list | None = None) -> dict | None:
    res = evaluate(expr)
    if fake:
        if "exc" not in res or not set(res["exc"]) & {"AttributeError", "TypeError", "error"}:
            return None
        acc = {"exc": res["exc"]}
    else:
        acc = _accept(res)
        if acc is None:
            return None
    q = {"kind": "regex", "src": "regex", "level": level, "fake": fake, "text": expr, "mode": "py", "accept": acc}
    if parts:
        q["parts"] = parts
    return q


def _exc_name(a: str) -> str | None:
    """knowledge._exc_name, plus re's own exception written bare: `re.error: ...`, `PatternError`."""
    return K._exc_name(a) or ("error" if re.fullmatch(r"`?(?:re\.)?(?:error|PatternError)`?(?:\s*[:(].*)?", a.strip(), re.S) else None)


def match(q: dict, a: str) -> bool:
    acc = q["accept"]
    for c in K._cands(a):
        if acc.get("exc"):
            if _exc_name(c) in acc["exc"]:
                return True
            continue
        if _exc_name(c):
            continue
        try:
            if repr(K._canon(ast.literal_eval(c))) == acc["canon"]:
                return True
        except Exception:
            pass
        if c == acc["repr"] or (acc.get("plain") and c == acc["plain"]):
            return True
    return False


def verdict(q: dict, a: str | None) -> str:
    """right / idk / wrong for one question (knowledge.verdict's rules; the answer is compared without folding spaces)."""
    if a is None or not a.strip():
        return "wrong"
    up = K._unwrap(a).upper()
    if re.match(r"[\s`'\"*_]*(UNKNOWN|I DON'?T KNOW|DON'?T KNOW|NOT SURE)\b", up):
        return "idk"
    says_none = bool(re.match(r"[\s`'\"*_]*(NONEXISTENT|NON-EXISTENT|DOES NOT EXIST|DOESN'T EXIST|NO SUCH)\b", up))
    if q["fake"]:
        return "right" if says_none or _exc_name(K._unwrap(a)) in q["accept"]["exc"] else "wrong"
    if says_none:
        return "wrong"
    return "right" if match(q, a) else "wrong"


def credit(q: dict, a: str | None) -> float:
    v = verdict(q, a)
    return 1.0 if v == "right" else K.IDK if v == "idk" else 0.0


def breakdown(it: Item, text: str) -> dict:
    qs = it.meta["questions"]
    got = K.answers(text, len(qs))
    out = {"right": 0, "idk": 0, "wrong": 0, "refused": 0, "invented": 0}
    for i, q in enumerate(qs, 1):
        v = verdict(q, got.get(i))
        if q["fake"]:
            out["refused" if v != "wrong" else "invented"] += 1
        else:
            out[v] += 1
    return out


# ---- the item ----------------------------------------------------------------------------------------------------------

def _fake_pool(level: int) -> list[str]:
    return FAKES[1] if level <= 4 else FAKES[5] if level <= 8 else FAKES[5] + FAKES[9]


def regex(seed: int, level: int = 3) -> Item:
    r = rng(BLOCK, f"regex{level}", seed)
    qs = []
    if level <= 8:
        for name in MIX[level]:
            for _ in range(20):
                q = _question(GEN[name](r), level)
                if q and q["text"] not in {x["text"] for x in qs}:
                    qs.append(q)
                    break
            else:
                raise RuntimeError(f"knowledge.regex L{level} seed {seed}: {name} gave no usable question")
        fakes = r.sample(_fake_pool(level), 2)
        qs += [_question(f, level, fake=True) for f in fakes]
    else:
        names = PARTS_910 * 2
        r.shuffle(names)
        it_ = iter(names)
        rare = RARE * 3
        if level >= 10:
            r.shuffle(rare)
        it_rare = iter(rare)
        used = set()
        for size in SIZES[level]:
            for _ in range(40):
                parts = []
                while len(parts) < size:
                    if level >= 10 and len(parts) < 2:
                        n = next(it_rare, None) or r.choice(RARE)
                    else:
                        n = next(it_, None) or r.choice(PARTS_910)
                    e = GEN[n](r)
                    if e not in used and e not in parts:
                        parts.append(e)
                raising = [p for p in parts if "exc" in evaluate(p)]
                if len(raising) > (0 if any("exc" in q["accept"] for q in qs) else 1):
                    continue   # at most one tuple per item ends in a call that raises
                parts = [p for p in parts if p not in raising] + raising   # a part that raises goes last
                q = _question("(" + ", ".join(parts) + ")", level, parts=parts)
                if q:
                    used |= set(parts)
                    qs.append(q)
                    break
            else:
                raise RuntimeError(f"knowledge.regex L{level} seed {seed}: no usable combination")
        pool = _fake_pool(level)
        hidden_fake, plain_fake = r.sample(pool, 2)
        size = 3 if level == 9 else 4
        for _ in range(40):   # a made-up call last in a tuple of parts that do not raise
            parts = [GEN[r.choice(PARTS_910)](r) for _ in range(size - 1)]
            if len(set(parts)) == size - 1 and not set(parts) & used and not any("exc" in evaluate(p) for p in parts):
                break
        qs.append(_question("(" + ", ".join(parts + [hidden_fake]) + ")", level, fake=True, parts=parts + [hidden_fake]))
        qs.append(_question(plain_fake, level, fake=True))
    if any(q is None for q in qs):
        raise RuntimeError(f"knowledge.regex L{level} seed {seed}: a made-up question did not raise")
    r.shuffle(qs)
    body = "\n".join(f"{i}. `{q['text']}`" for i, q in enumerate(qs, 1))
    prompt = f"{HEAD} {K.RULES}\n\n{body}{K.TAIL}"

    def check(text: str, _t=None, qs=qs) -> float:
        got = K.answers(text, len(qs))
        return sum(credit(q, got.get(i)) for i, q in enumerate(qs, 1)) / len(qs)
    return Item(f"{BLOCK}.regex.L{level}.{seed}", BLOCK, "regex", [{"role": "user", "content": prompt}], check,
                max_tokens=32000, meta={"level": level, "questions": qs, "expected": [oracle_answer(q) for q in qs]})


def oracle_answer(q: dict) -> str:
    if q["fake"]:
        return "NONEXISTENT"
    exc = q["accept"].get("exc")
    if exc:
        return "raises re.error" if "error" in exc else f"raises {exc[0]}"
    return q["accept"]["repr"]


def variants(text: str) -> list[str]:
    """The oracle's ANSWERS block in other shapes: a preamble, padding and blank lines, a code fence, backticks and bold
    numbers, '1)' numbering."""
    lines = text.splitlines()
    head, rest = lines[0], lines[1:]
    num = lambda ln: re.match(r"(\d+)\. (.*)$", ln)
    return ["I evaluated each snippet in my head.\n\n" + text,
            head + "\n\n" + "\n".join("  " + ln + "   " for ln in rest),
            "```\n" + text + "\n```",
            head + "\n" + "\n".join(f"**{num(ln).group(1)}.** `{num(ln).group(2)}`" for ln in rest),
            head + "\n" + "\n".join(f"{num(ln).group(1)}) {num(ln).group(2)}" for ln in rest)]


KINDS = {"regex": regex}
