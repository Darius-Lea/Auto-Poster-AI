"""Base types and adapter registry."""
from __future__ import annotations

import abc
import importlib
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("adapters.base")


@dataclass
class Record:
    name: str
    charge: str
    booking_date: str
    source_url: str
    image_url: str
    age: Optional[int] = None
    kind: str = "booking"


class ScraperAdapter(abc.ABC):
    """Subclass and implement fetch(url). Register it in _REGISTRY or via register_adapter."""

    name: str = "base"

    def __init__(self, dump_html: bool = False):
        self.dump_html = dump_html

    @abc.abstractmethod
    def fetch(self, url: str) -> list[Record]:
        raise NotImplementedError


_REGISTRY = {
    "allen_sheriff": ("adapters.allen_sheriff", "AllenSheriffAdapter"),
    "indiana_most_wanted": ("adapters.indiana_most_wanted", "IndianaMostWantedAdapter"),
    "solve_the_case": ("adapters.solve_the_case", "SolveTheCaseAdapter"),
}


def register_adapter(name: str, module: str, cls: str) -> None:
    _REGISTRY[name] = (module, cls)


def get_adapter(name: str, **kwargs) -> ScraperAdapter:
    if name not in _REGISTRY:
        raise KeyError(f"Unknown adapter '{name}'. Known: {sorted(_REGISTRY)}")
    module, cls = _REGISTRY[name]
    return getattr(importlib.import_module(module), cls)(**kwargs)
