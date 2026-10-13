from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from ..db import get_pool
from ..deps import require_trip
from ..schemas import AREA_NULLABLE, AreaCreate, AreaOut, AreaUpdate
from ..sql import clean_patch, update_sql

router = APIRouter(prefix="/api/trips/{trip_id}/areas", tags=["areas"])

MAX_AREAS_PER_TRIP = 100


@router.post("", response_model=AreaOut, status_code=201)
async def create_area(body: AreaCreate, trip=Depends(require_trip)):
    if body.day_index is not None and body.day_index >= trip["num_days"]:
        raise HTTPException(422, "day_out_of_range")
    pool = get_pool()
    n = await pool.fetchval("select count(*) from trip_areas where trip_id = $1", trip["id"])
    if n >= MAX_AREAS_PER_TRIP:
        raise HTTPException(409, "too_many_areas")
    row = await pool.fetchrow(
        """insert into trip_areas (trip_id, day_index, label, lat, lng, radius_m, memo)
           values ($1, $2, $3, $4, $5, $6, $7) returning *""",
        trip["id"], body.day_index, body.label, body.lat, body.lng, body.radius_m, body.memo,
    )
    return dict(row)


@router.patch("/{area_id}", response_model=AreaOut)
async def update_area(area_id: UUID, body: AreaUpdate, trip=Depends(require_trip)):
    fields = clean_patch(body.model_dump(exclude_unset=True), AREA_NULLABLE)
    day = fields.get("day_index")
    if day is not None and day >= trip["num_days"]:
        raise HTTPException(422, "day_out_of_range")
    sql, args = update_sql("trip_areas", fields, {"id": area_id, "trip_id": trip["id"]})
    row = await get_pool().fetchrow(sql, *args)
    if not row:
        raise HTTPException(404, "not_found")
    return dict(row)


@router.delete("/{area_id}", status_code=204)
async def delete_area(area_id: UUID, trip=Depends(require_trip)):
    status = await get_pool().execute(
        "delete from trip_areas where id = $1 and trip_id = $2", area_id, trip["id"]
    )
    if status == "DELETE 0":
        raise HTTPException(404, "not_found")
