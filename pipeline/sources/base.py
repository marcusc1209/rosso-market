from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterator, Optional

from ..fetch import Fetcher, Browser
from ..records import RawRecord


@dataclass
class Context:
    fetcher: Fetcher
    run_date: date
    llm_cfg: dict = field(default_factory=dict)
    con: Any = None                 # the database, for sources that keep a small lookup cache
    _browser: Optional[Browser] = None

    @property
    def browser(self) -> Browser:
        if self._browser is None:
            self._browser = Browser({}, self.fetcher)
        return self._browser

    def close(self):
        if self._browser:
            self._browser.close()
        self.fetcher.close()


class Source:
    """One place prices come from. Subclasses implement fetch()."""
    name: str = "base"
    kind: str = "listing"          # listing, auction, or both

    def __init__(self, name: str, cfg: dict, ctx: Context):
        self.name = name
        self.cfg = cfg
        self.ctx = ctx
        self.log = logging.getLogger(f"source.{name}")
        self.kind = cfg.get("kind", self.kind)
        # A source that only reads what changed (finished results are never read again) sets
        # {"listed": items the site lists, "read": pages read, "failed": pages it could not make sense of},
        # so its health is judged on what it can see rather than on how much was new (pipeline/health.py).
        self.coverage: Optional[dict] = None

    def fetch(self) -> Iterator[RawRecord]:
        raise NotImplementedError
