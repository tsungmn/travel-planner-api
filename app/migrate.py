import asyncio
import pathlib

import asyncpg

from .config import settings

MIGRATIONS = pathlib.Path(__file__).resolve().parent.parent / "db" / "migrations"
LOCK_ID = 7340192   # 앱 전용 advisory lock 키 (동시 실행 방지)


async def main() -> None:
    conn = await asyncpg.connect(settings.database_url)
    try:
        await conn.execute("select pg_advisory_lock($1)", LOCK_ID)
        await conn.execute(
            """create table if not exists schema_migrations (
                 version text primary key,
                 applied_at timestamptz not null default now())"""
        )
        done = {r["version"] for r in await conn.fetch("select version from schema_migrations")}
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.name in done:
                print(f"skip     {f.name}")
                continue
            print(f"applying {f.name}")
            async with conn.transaction():
                await conn.execute(f.read_text(encoding="utf-8"))
                await conn.execute("insert into schema_migrations (version) values ($1)", f.name)
        print("done")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())