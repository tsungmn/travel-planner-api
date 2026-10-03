import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException

from ..db import get_pool
from ..deps import require_trip
from ..schemas import STOP_NULLABLE, StopCreate, StopOrder, StopOut, StopUpdate
from ..sql import clean_patch, update_sql

router = APIRouter(prefix="/api/trips/{trip_id}/stops", tags=["stops"])

MAX_STOPS_PER_TRIP = 200


# 일차의 맨 뒤에 추가
@router.post("", response_model=StopOut, status_code=201)
async def create_stop(body: StopCreate, trip=Depends(require_trip)):
    if body.day_index >= trip["num_days"]:
        raise HTTPException(422, "day_out_of_range")
    pool = get_pool()
    n = await pool.fetchval("select count(*) from stops where trip_id = $1", trip["id"])
    if n >= MAX_STOPS_PER_TRIP:
        raise HTTPException(409, "too_many_stops")
    row = await pool.fetchrow(
        """insert into stops (trip_id, day_index, sort_order, name, address_text, lat, lng, place_id, memo)
           values ($1, $2,
                   (select coalesce(max(sort_order), -1) + 1 from stops where trip_id = $1 and day_index = $2),
                   $3, $4, $5, $6, $7, $8)
           returning *""",
        trip["id"], body.day_index, body.name, body.address_text,
        body.lat, body.lng, body.place_id, body.memo,
    )
    return dict(row)


# 드롭 시 한 번 호출: 일차 이동과 순서를 원자적으로 저장 (하나라도 내 여행의 장소가 아니면 전체 취소)
@router.put("/order", status_code=204)
async def reorder_stops(
    items: Annotated[list[StopOrder], Body(max_length=500)],
    trip=Depends(require_trip),
):
    ids = [i.id for i in items]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "duplicate_stop")
    if any(i.day_index >= trip["num_days"] for i in items):
        raise HTTPException(422, "day_out_of_range")

    payload = json.dumps([i.model_dump(mode="json") for i in items])
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            status = await conn.execute(
                """update stops s
                      set day_index = i.day_index, sort_order = i.sort_order
                     from jsonb_to_recordset($2::jsonb) as i(id uuid, day_index int, sort_order int)
                    where s.id = i.id and s.trip_id = $1""",
                trip["id"], payload,
            )
            if int(status.split()[-1]) != len(items):
                raise HTTPException(422, "unknown_stop")   # 트랜잭션 롤백


@router.patch("/{stop_id}", response_model=StopOut)
async def update_stop(stop_id: UUID, body: StopUpdate, trip=Depends(require_trip)):
    fields = clean_patch(body.model_dump(exclude_unset=True), STOP_NULLABLE)
    sql, args = update_sql("stops", fields, {"id": stop_id, "trip_id": trip["id"]})
    row = await get_pool().fetchrow(sql, *args)
    if not row:
        raise HTTPException(404, "not_found")
    return dict(row)


@router.delete("/{stop_id}", status_code=204)
async def delete_stop(stop_id: UUID, trip=Depends(require_trip)):
    status = await get_pool().execute(
        "delete from stops where id = $1 and trip_id = $2", stop_id, trip["id"]
    )
    if status == "DELETE 0":
        raise HTTPException(404, "not_found")