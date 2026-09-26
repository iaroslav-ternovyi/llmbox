from dataclasses import dataclass, field


@dataclass
class Item:
    sku: str
    qty: int
    weight_g: int
    l: float
    w: float
    h: float

    @property
    def volume(self) -> float:
        return self.l * self.w * self.h


@dataclass
class Box:
    name: str
    l: float
    w: float
    h: float
    max_g: int
    tare_g: int

    @property
    def volume(self) -> float:
        return self.l * self.w * self.h


@dataclass
class Parcel:
    box: str
    skus: list
    weight_g: int
    l: float
    w: float
    h: float


@dataclass
class Order:
    id: str
    country: str
    service: str
    ordered_at: object
    promos: list = field(default_factory=list)
    items: list = field(default_factory=list)
    # FEATURE-BEGIN insurance
    insured_value_cents: int = 0
    # FEATURE-END
