from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class StateArea:
    name: str
    code: str
    south: float
    west: float
    north: float
    east: float


@dataclass
class Business:
    name: str
    phone: str = ""
    source: str = ""
    source_id: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    latitude: float | None = None
    longitude: float | None = None
    rating: float | None = None
    review_count: int | None = None
    url: str = ""
    categories: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    source_ids: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items()
                if value not in (None, "", [], {}) and key not in ("source", "source_id")}
