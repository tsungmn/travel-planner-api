from typing import Any

from fastapi import HTTPException


def clean_patch(fields: dict[str, Any], nullable: set[str]) -> dict[str, Any]:
    """PATCH 본문 정리. null 을 허용하지 않는 컬럼에 null 이 오면 422, 빈 본문도 422."""
    for k, v in fields.items():
        if v is None and k not in nullable:
            raise HTTPException(422, f"{k}_cannot_be_null")
    if not fields:
        raise HTTPException(422, "no_fields")
    return fields


def update_sql(table: str, fields: dict[str, Any], keys: dict[str, Any]) -> tuple[str, list[Any]]:
    """테이블/컬럼명은 코드(Pydantic 모델)에서만 오므로 사용자 입력이 SQL에 들어가지 않는다."""
    sets = ", ".join(f"{c} = ${i}" for i, c in enumerate(fields, 1))
    off = len(fields)
    where = " and ".join(f"{c} = ${off + i}" for i, c in enumerate(keys, 1))
    sql = f"update {table} set {sets} where {where} returning *"
    return sql, [*fields.values(), *keys.values()]