"""Resource modules grouping endpoints by domain."""

from .datafields import DatafieldsResource
from .rates import RatesResource
from .screener import ScreenerResource
from .signals import SignalsResource
from .simulations import SimulationsResource
from .ticker import TickerHandle

__all__ = [
    "DatafieldsResource",
    "RatesResource",
    "ScreenerResource",
    "SignalsResource",
    "SimulationsResource",
    "TickerHandle",
]
