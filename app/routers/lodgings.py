from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from ..db import get_pool
from ..deps import require_trip
from ..schemas import LODGING_NULLABLE, LodgingCreate, LodgingOut, LodgingUpdate
from ..sql import clean_patch, update_sql

router = APIRouter(prefix="/api/trips/{trip_id}/lodgings", tags=["lodgings"])

MAX_LODGINGS_PER_TRIP = 20


@router.post("", response_model=LodgingOut, status_code=201)
async def create_lodging(body: LodgingCreate, trip=Depends(require_trip)):
    pool = get_pool()
    n = await pool.fetchval("select count(*) from lodgings where trip_id = $1", trip["id"])
    if n >= MAX_LODGINGS_PER_TRIP:
        raise HTTPException(409, "too_many_lodgings")
    row = await pool.fetchrow(
        """insert into lodgings (trip_id, name, address_text, lat, lng, provider, provider_place_id,
                                 check_in, check_out, memo)
           values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) returning *""",
        trip["id"], body.name, body.address_text, body.lat, body.lng,
        body.provider, body.provider_place_id, body.check_in, body.check_out, body.memo,
    )
    return dict(row)


@router.patch("/{lodging_id}", response_model=LodgingOut)
async def update_lodging(lodging_id: UUID, body: LodgingUpdate, trip=Depends(require_trip)):
    fields = clean_patch(body.model_dump(exclude_unset=True), LODGING_NULLABLE)
    sql, args = update_sql("lodgings", fields, {"id": lodging_id, "trip_id": trip["id"]})
    row = await get_pool().fetchrow(sql, *args)
    if not row:
        raise HTTPException(404, "not_found")
    return dict(row)


@router.delete("/{lodging_id}", status_code=204)
async def delete_lodging(lodging_id: UUID, trip=Depends(require_trip)):
    status = await get_pool().execute(
        "delete from lodgings where id = $1 and trip_id = $2", lodging_id, trip["id"]
    )
    if status == "DELETE 0":
        raise HTTPException(404, "not_found")