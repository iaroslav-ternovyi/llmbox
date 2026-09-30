"""Where the public llmbox lives, for every client command.

The site (the model list, the recipes, the task bank) and the intake (seeds, uploads, accounts) have public defaults;
$LLMBOX_SITE / $LLMBOX_SERVER, or ~/.llmbox/config.json {"site": ..., "server": ...}, point a machine elsewhere (a local
site while developing, a test intake). The site's index may name the intake, so a new address needs no new llmbox."""
from __future__ import annotations

import json
import os

from .hosts import HOME

SITE = "https://llmbox.pages.dev"


def _config() -> dict:
    try:
        return json.load(open(os.path.join(HOME, "config.json")))
    except (OSError, ValueError):
        return {}


def site() -> str:
    return (os.environ.get("LLMBOX_SITE") or _config().get("site") or SITE).rstrip("/")


def server() -> str:
    """The intake: $LLMBOX_SERVER, the config, else the address the site's model list names."""
    v = os.environ.get("LLMBOX_SERVER") or _config().get("server")
    if v:
        return v.rstrip("/")
    try:
        idx = json.load(open(os.path.join(HOME, "recipes", "registry", "index.json")))
        return (idx.get("intake") or "").rstrip("/") or "https://intake.invalid"
    except (OSError, ValueError):
        return "https://intake.invalid"
