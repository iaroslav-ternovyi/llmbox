"""`llmbox site`: the public static site, generated from saved results (design "Oscilloscope", llmbox/site_assets/osc.css).

Pages are built only from records: suite results, the job queue (what is being measured now) and speed probes. Nothing
on a page is typed by hand, so the site cannot drift from the data. Structure follows what users of benchmark sites
value: UserBenchmark (ranked tiles, your box among the same hardware), Artificial Analysis (quality x speed with a
Pareto line), LocalScore (time to first token), LMArena (ties when a difference is inside its margin of error).

The package: words (names and texts), stats (ties, places, the score axis), components (pieces every page draws the
same way), data (records, pools, model shapes), layout (the page frame), one module per page (home, model, run,
hardware, compare, new, method), build (the whole site). CSS and page scripts live in assets/.
"""
from .build import build

__all__ = ["build"]
