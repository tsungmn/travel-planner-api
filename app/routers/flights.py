from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from ..db import get_pool
from ..deps import require_trip
from ..schemas import FLIGHT_NULLABLE, FlightCreate, FlightOut, FlightUpdate
from ..sql import clean_patch, update_sql

router = APIRouter(prefix="/api/trips/{trip_id}/flights", tags=["flights"])

MAX_FLIGHTS_PER_TRIP = 10


@router.post("", response_model=FlightOut, status_code=201)
async def create_flight(body: FlightCreate, trip=Depends(require_trip)):
    pool = get_pool()
    n = await pool.fetchval("select count(*) from flights where trip_id = $1", trip["id"])
    if n >= MAX_FLIGHTS_PER_TRIP:
        raise HTTPException(409, "too_many_flights")
    row = await pool.fetchrow(
        """insert into flights (trip_id, direction, flight_no, dep_airport, arr_airport,
                                dep_local, arr_local, dep_tz, arr_tz)
           values ($1, $2, $3, $4, $5, $6, $7, $8, $9) returning *""",
        trip["id"], body.direction, body.flight_no, body.dep_airport, body.arr_airport,
        body.dep_local, body.arr_local, body.dep_tz, body.arr_tz,
    )
    return dict(row)


@router.patch("/{flight_id}", response_model=FlightOut)
async def update_flight(flight_id: UUID, body: FlightUpdate, trip=Depends(require_trip)):
    fields = clean_patch(body.model_dump(exclude_unset=True), FLIGHT_NULLABLE)
    sql, args = update_sql("flights", fields, {"id": flight_id, "trip_id": trip["id"]})
    row = await get_pool().fetchrow(sql, *args)
    if not row:
        raise HTTPException(404, "not_found")
    return dict(row)


@router.delete("/{flight_id}", status_code=204)
async def delete_flight(flight_id: UUID, trip=Depends(require_trip)):
    status = await get_pool().execute(
        "delete from flights where id = $1 and trip_id = $2", flight_id, trip["id"]
    )
    if status == "DELETE 0":
        raise HTTPException(404, "not_found")