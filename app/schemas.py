import re
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import (
    AfterValidator, BaseModel, Field, NaiveDatetime, field_validator, model_validator,
)
from typing_extensions import Self

Lat = Annotated[float, Field(ge=-90, le=90)]
Lng = Annotated[float, Field(ge=-180, le=180)]
ProviderPlaceId = Annotated[str, Field(min_length=1, max_length=200)]
Provider = Literal["geoapify", "nominatim", "manual"]


def _flight_no(v: str) -> str:
    v = re.sub(r"\s+", "", v).upper()
    if not re.fullmatch(r"[A-Z0-9]{2}\d{1,4}[A-Z]?", v):
        raise ValueError("invalid flight number")
    return v


def _iata(v: str) -> str:
    v = v.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", v):
        raise ValueError("invalid IATA code")
    return v


def _tz(v: str) -> str:
    try:
        ZoneInfo(v)
    except Exception:
        raise ValueError("invalid timezone") from None
    return v


FlightNo = Annotated[str, AfterValidator(_flight_no)]
Iata = Annotated[str, AfterValidator(_iata)]
TzName = Annotated[str, AfterValidator(_tz)]


# ===== Trip =====
class TripCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    destination: str = Field(min_length=1, max_length=100)
    dest_lat: Lat | None = None
    dest_lng: Lng | None = None
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _dates(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if (self.end_date - self.start_date).days > 60:
            raise ValueError("trip is too long (max 61 days)")
        return self


class TripUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    destination: str | None = Field(default=None, min_length=1, max_length=100)
    dest_lat: Lat | None = None
    dest_lng: Lng | None = None
    start_date: date | None = None
    end_date: date | None = None


TRIP_NULLABLE = {"dest_lat", "dest_lng"}


class TripOut(BaseModel):
    id: UUID
    title: str
    destination: str
    dest_lat: float | None
    dest_lng: float | None
    start_date: date
    end_date: date
    share_token: str | None
    share_flights: bool
    share_lodgings: bool
    share_memos: bool
    created_at: datetime


class TripListItem(TripOut):
    stop_count: int


class TripPublic(BaseModel):
    title: str
    destination: str
    dest_lat: float | None
    dest_lng: float | None
    start_date: date
    end_date: date


# ===== 공유 =====
class ShareOptions(BaseModel):
    """보낸 항목만 바꾸고, 생략한 항목은 현재 값을 유지한다. (새 링크의 기본값은 모두 false)"""
    flights: bool | None = None
    lodgings: bool | None = None
    memos: bool | None = None


class ShareOut(BaseModel):
    flights: bool
    lodgings: bool
    memos: bool


class ShareInfo(ShareOut):
    token: str


# ===== Stop =====
def _blank_to_none(v: str | None) -> str | None:
    """공백뿐인 별칭은 '없음'으로 본다."""
    if v is None:
        return None
    return v.strip() or None


class StopCreate(BaseModel):
    day_index: int = Field(ge=0, le=60)
    name: str = Field(min_length=1, max_length=100)
    alias: str | None = Field(default=None, max_length=50)
    address_text: str | None = Field(default=None, max_length=300)
    lat: Lat
    lng: Lng
    provider: Provider = "manual"
    provider_place_id: ProviderPlaceId | None = None
    memo: str | None = Field(default=None, max_length=1000)

    _alias = field_validator("alias")(_blank_to_none)

    @model_validator(mode="after")
    def _provider(self) -> Self:
        if self.provider == "manual":
            self.provider_place_id = None
        elif not self.provider_place_id:
            raise ValueError("provider_place_id is required unless provider is manual")
        return self


class StopUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    alias: str | None = Field(default=None, max_length=50)
    address_text: str | None = Field(default=None, max_length=300)
    memo: str | None = Field(default=None, max_length=1000)


    _alias = field_validator("alias")(_blank_to_none)


STOP_NULLABLE = {"alias", "address_text", "memo"}


class StopOrder(BaseModel):
    id: UUID
    day_index: int = Field(ge=0, le=60)
    sort_order: int = Field(ge=0, le=100000)


class StopOut(BaseModel):
    id: UUID
    trip_id: UUID
    day_index: int
    sort_order: int
    name: str
    alias: str | None
    address_text: str | None
    lat: float
    lng: float
    provider: str
    provider_place_id: str | None
    memo: str | None


# ===== Flight =====
class FlightCreate(BaseModel):
    direction: Literal["outbound", "inbound"]
    flight_no: FlightNo
    dep_airport: Iata | None = None
    arr_airport: Iata | None = None
    dep_local: NaiveDatetime | None = None   # 출발지 현지 시각 (타임존 없이)
    arr_local: NaiveDatetime | None = None   # 도착지 현지 시각
    dep_tz: TzName | None = None             # 예: Asia/Seoul
    arr_tz: TzName | None = None             # 예: Asia/Shanghai


class FlightUpdate(BaseModel):
    direction: Literal["outbound", "inbound"] | None = None
    flight_no: FlightNo | None = None
    dep_airport: Iata | None = None
    arr_airport: Iata | None = None
    dep_local: NaiveDatetime | None = None
    arr_local: NaiveDatetime | None = None
    dep_tz: TzName | None = None
    arr_tz: TzName | None = None


FLIGHT_NULLABLE = {"dep_airport", "arr_airport", "dep_local", "arr_local", "dep_tz", "arr_tz"}


class FlightOut(BaseModel):
    id: UUID
    trip_id: UUID
    direction: str
    flight_no: str
    dep_airport: str | None
    arr_airport: str | None
    dep_local: datetime | None
    arr_local: datetime | None
    dep_tz: str | None
    arr_tz: str | None


# ===== Lodging =====
class LodgingCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    address_text: str | None = Field(default=None, max_length=300)
    lat: Lat
    lng: Lng
    provider: Provider = "manual"
    provider_place_id: ProviderPlaceId | None = None
    check_in: date | None = None
    check_out: date | None = None
    memo: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.provider == "manual":
            self.provider_place_id = None
        elif not self.provider_place_id:
            raise ValueError("provider_place_id is required unless provider is manual")
        if self.check_in and self.check_out and self.check_out < self.check_in:
            raise ValueError("check_out must be on or after check_in")
        return self


class LodgingUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    address_text: str | None = Field(default=None, max_length=300)
    lat: Lat | None = None
    lng: Lng | None = None
    provider: Provider | None = None
    provider_place_id: ProviderPlaceId | None = None
    check_in: date | None = None
    check_out: date | None = None
    memo: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.provider == "manual":
            self.provider_place_id = None      # 대입하면 '전달된 필드'로 취급되어 DB 값도 null 로 갱신됨
        elif self.provider is not None and not self.provider_place_id:
            raise ValueError("provider_place_id is required unless provider is manual")
        if self.check_in and self.check_out and self.check_out < self.check_in:
            raise ValueError("check_out must be on or after check_in")
        return self


LODGING_NULLABLE = {"address_text", "provider_place_id", "check_in", "check_out", "memo"}


class LodgingOut(BaseModel):
    id: UUID
    trip_id: UUID
    name: str
    address_text: str | None
    lat: float
    lng: float
    provider: str
    provider_place_id: str | None
    check_in: date | None
    check_out: date | None
    memo: str | None


# ===== Area (러프 계획 영역) =====
AREA_MIN_RADIUS_M = 100
AREA_MAX_RADIUS_M = 50000


def _strip(v: str) -> str:
    return v.strip()


def _strip_or_none(v: str | None) -> str | None:
    return v.strip() if v is not None else None


class AreaCreate(BaseModel):
    day_index: int | None = Field(default=None, ge=0, le=60)   # None = 일차 미지정
    label: str = Field(default="", max_length=30)
    lat: Lat
    lng: Lng
    radius_m: int = Field(default=800, ge=AREA_MIN_RADIUS_M, le=AREA_MAX_RADIUS_M)
    memo: str | None = Field(default=None, max_length=500)

    _label = field_validator("label")(_strip)
    _memo = field_validator("memo")(_blank_to_none)


class AreaUpdate(BaseModel):
    day_index: int | None = Field(default=None, ge=0, le=60)
    label: str | None = Field(default=None, max_length=30)
    lat: Lat | None = None
    lng: Lng | None = None
    radius_m: int | None = Field(default=None, ge=AREA_MIN_RADIUS_M, le=AREA_MAX_RADIUS_M)
    memo: str | None = Field(default=None, max_length=500)

    _label = field_validator("label")(_strip_or_none)
    _memo = field_validator("memo")(_blank_to_none)


AREA_NULLABLE = {"day_index", "memo"}


class AreaOut(BaseModel):
    id: UUID
    trip_id: UUID
    day_index: int | None
    label: str
    lat: float
    lng: float
    radius_m: int
    memo: str | None


# ===== 조합 =====
class TripDetail(TripOut):
    flights: list[FlightOut]
    lodgings: list[LodgingOut]
    stops: list[StopOut]
    areas: list[AreaOut]


class SharedTripOut(BaseModel):
    trip: TripPublic
    share: ShareOut          # 이 링크에서 공개된 항목 (공개하지 않은 항목은 빈 목록/null 로 내려간다)
    flights: list[FlightOut]
    lodgings: list[LodgingOut]
    stops: list[StopOut]
    areas: list[AreaOut]