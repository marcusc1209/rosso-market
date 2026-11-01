from .bringatrailer import BringATrailer
from .generic import BrowserJson, StaticPage, StaticThenBrowser
from .marketcheck import MarketCheck
from .apify import ApifyActor
from .demo import Demo
from .ferrari_approved import FerrariApproved
from .rmsothebys import RMSothebys
from .online_auctions import SothebysMotorsport, DupontLive
from .mecum import Mecum

ADAPTERS = {
    "bringatrailer": BringATrailer,
    "browser_json": BrowserJson,
    "static_page": StaticPage,
    "static_then_browser": StaticThenBrowser,
    "marketcheck": MarketCheck,
    "apify": ApifyActor,
    "demo": Demo,
    "ferrari_approved": FerrariApproved,
    "rmsothebys": RMSothebys,
    "somo": SothebysMotorsport,
    "dupont_live": DupontLive,
    "mecum": Mecum,
}
