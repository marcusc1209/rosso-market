from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Iterator, Optional

from ..fetch import Fetcher, Browser
from ..records import RawRecord


@dataclass
class Context:
    fetcher: Fetcher
    run_date: date
    llm_cfg: dict = field(default_factory=dict)
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

    def fetch(self) -> Iterator[RawRecord]:
        raise NotImplementedError
