from .bringatrailer import BringATrailer
from .generic import BrowserJson, StaticPage, StaticThenBrowser
from .marketcheck import MarketCheck
from .apify import ApifyActor
from .demo import Demo
from .ferrari_approved import FerrariApproved
from .rmsothebys import RMSothebys
from .online_auctions import SothebysMotorsport, DupontLive
from .mecum import Mecum
from .broadarrow import BroadArrow
from .gooding import Gooding
from .bonhams import Bonhams
from .pcarmarket import PCarMarket
from .dealer_marketplaces import AutoTrader, AutoTraderCA, DupontRegistry

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
    "broadarrow": BroadArrow,
    "gooding": Gooding,
    "bonhams": Bonhams,
    "pcarmarket": PCarMarket,
    "autotrader": AutoTrader,
    "autotrader_ca": AutoTraderCA,
    "dupont_registry": DupontRegistry,
}
