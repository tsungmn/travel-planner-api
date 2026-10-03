import re
from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AfterValidator, BaseModel, Field, NaiveDatetime, model_validator

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
        raise ValueError("invalid timezone")
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


# ===== Stop =====
class StopCreate(BaseModel):
    day_index: int = Field(ge=0, le=60)
    name: str = Field(min_length=1, max_length=100)
    address_text: str | None = Field(default=None, max_length=300)
    lat: Lat
    lng: Lng
    provider: Provider = "manual"
    provider_place_id: ProviderPlaceId | None = None
    memo: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _provider(self) -> Self:
        if self.provider == "manual":
            self.provider_place_id = None
        elif not self.provider_place_id:
            raise ValueError("provider_place_id is required unless provider is manual")
        return self


class StopUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    address_text: str | None = Field(default=None, max_length=300)
    memo: str | None = Field(default=None, max_length=1000)


STOP_NULLABLE = {"address_text", "memo"}


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
    place_id: str | None
    source: str
    check_in: date | None
    check_out: date | None
    memo: str | None


# ===== 조합 =====
class TripDetail(TripOut):
    flights: list[FlightOut]
    lodgings: list[LodgingOut]
    stops: list[StopOut]


class SharedTripOut(BaseModel):
    trip: TripPublic
    flights: list[FlightOut]
    lodgings: list[LodgingOut]
    stops: list[StopOut]