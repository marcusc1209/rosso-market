"""Apify actors as a managed backup for auction houses.

Community maintained scrapers exist for Bring a Trailer, Cars & Bids,
RM Sotheby's, Mecum and others. They cost a few dollars per thousand lots and
the maintainers fix them when sites change. Configure any number of actors in
config/sources.yaml; the output of each is mapped with the same generic mapper
the browser adapters use. Needs APIFY_TOKEN.
"""
from __future__ import annotations

import os
from typing import Iterator

from .base import Source
from .generic import _finish
from ..extract import map_generic
from ..records import RawRecord
from .. import taxonomy


class ApifyActor(Source):
    def fetch(self) -> Iterator[RawRecord]:
        token = os.environ.get(self.cfg.get("token_env", "APIFY_TOKEN"))
        if not token:
            raise RuntimeError("APIFY_TOKEN is not set")
        actor = self.cfg["actor"].replace("/", "~")
        url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
        r = self.ctx.fetcher.request("POST", url, json=self.cfg.get("input", {}),
                                     headers={"Authorization": f"Bearer {token}"},
                                     params={"timeout": self.cfg.get("timeout", 600)}, timeout=660)
        for item in r.json():
            rec = map_generic(item, self.name, self.kind, self.cfg.get("base_url", ""))
            if rec and taxonomy.is_ferrari(rec.title, rec.make):
                yield _finish(rec, self.cfg, url)
