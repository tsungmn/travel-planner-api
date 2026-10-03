import asyncio

from fastapi import APIRouter, Depends, HTTPException

from ..db import get_pool
from ..deps import current_user, require_trip
from ..schemas import (
    TRIP_NULLABLE, SharedTripOut, TripCreate, TripDetail, TripListItem, TripOut, TripUpdate,
)
from ..sql import clean_patch, update_sql

router = APIRouter(prefix="/api", tags=["trips"])

MAX_TRIPS_PER_USER = 30


async def _load_children(trip_id):
    pool = get_pool()
    flights, lodgings, stops = await asyncio.gather(
        pool.fetch("select * from flights where trip_id = $1 order by dep_local nulls last", trip_id),
        pool.fetch("select * from lodgings where trip_id = $1 order by check_in nulls last", trip_id),
        pool.fetch("select * from stops where trip_id = $1 order by day_index, sort_order", trip_id),
    )
    return (
        [dict(r) for r in flights],
        [dict(r) for r in lodgings],
        [dict(r) for r in stops],
    )


@router.get("/trips", response_model=list[TripListItem])
async def list_trips(user: dict = Depends(current_user)):
    rows = await get_pool().fetch(
        """select t.*, (select count(*) from stops s where s.trip_id = t.id)::int as stop_count
             from trips t
            where t.owner_id = $1
            order by t.start_date desc, t.created_at desc""",
        user["id"],
    )
    return [dict(r) for r in rows]


@router.post("/trips", response_model=TripOut, status_code=201)
async def create_trip(body: TripCreate, user: dict = Depends(current_user)):
    pool = get_pool()
    n = await pool.fetchval("select count(*) from trips where owner_id = $1", user["id"])
    if n >= MAX_TRIPS_PER_USER:
        raise HTTPException(409, "too_many_trips")
    row = await pool.fetchrow(
        """insert into trips (owner_id, title, destination, dest_lat, dest_lng, start_date, end_date)
           values ($1, $2, $3, $4, $5, $6, $7) returning *""",
        user["id"], body.title, body.destination, body.dest_lat, body.dest_lng,
        body.start_date, body.end_date,
    )
    return dict(row)


@router.get("/trips/{trip_id}", response_model=TripDetail)
async def get_trip(trip=Depends(require_trip)):
    flights, lodgings, stops = await _load_children(trip["id"])
    return {**dict(trip), "flights": flights, "lodgings": lodgings, "stops": stops}


@router.patch("/trips/{trip_id}", response_model=TripOut)
async def update_trip(body: TripUpdate, trip=Depends(require_trip)):
    fields = clean_patch(body.model_dump(exclude_unset=True), TRIP_NULLABLE)

    start = fields.get("start_date", trip["start_date"])
    end = fields.get("end_date", trip["end_date"])
    if end < start or (end - start).days > 60:
        raise HTTPException(422, "invalid_dates")

    # 기간을 줄였을 때 범위를 벗어나는 일차에 장소가 남아 있으면 거부
    new_days = (end - start).days + 1
    pool = get_pool()
    orphaned = await pool.fetchval(
        "select exists (select 1 from stops where trip_id = $1 and day_index >= $2)",
        trip["id"], new_days,
    )
    if orphaned:
        raise HTTPException(409, "stops_out_of_range")

    sql, args = update_sql("trips", fields, {"id": trip["id"]})
    row = await pool.fetchrow(sql, *args)
    return dict(row)


@router.delete("/trips/{trip_id}", status_code=204)
async def delete_trip(trip=Depends(require_trip)):
    await get_pool().execute("delete from trips where id = $1", trip["id"])


# ===== 공유 =====
# 공유 켜기: 이미 켜져 있으면 기존 토큰 유지
@router.post("/trips/{trip_id}/share")
async def enable_share(trip=Depends(require_trip)):
    token = await get_pool().fetchval(
        """update trips
              set share_token = coalesce(share_token,
                    replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''))
            where id = $1
            returning share_token""",
        trip["id"],
    )
    return {"token": token}


@router.delete("/trips/{trip_id}/share", status_code=204)
async def disable_share(trip=Depends(require_trip)):
    await get_pool().execute("update trips set share_token = null where id = $1", trip["id"])


# 비로그인 읽기 전용 조회 (owner_id / share_token 은 응답에 포함하지 않음)
@router.get("/shared/{token}", response_model=SharedTripOut)
async def get_shared(token: str):
    if len(token) > 100:
        raise HTTPException(404, "not_found")
    trip = await get_pool().fetchrow(
        """select id, title, destination, dest_lat, dest_lng, start_date, end_date
             from trips where share_token = $1""",
        token,
    )
    if not trip:
        raise HTTPException(404, "not_found")
    flights, lodgings, stops = await _load_children(trip["id"])
    return {"trip": dict(trip), "flights": flights, "lodgings": lodgings, "stops": stops}