import asyncio
from collections import defaultdict
from sqlalchemy import select
from app.core.db import SessionLocal
from app.core.names import chave
from app.models import Team

async def main():
    async with SessionLocal() as s:
        times = (await s.scalars(select(Team))).all()
        g = defaultdict(list)
        for t in times:
            g[chave(t.name)].append((t.id, t.name, t.country))
        for k, v in sorted(g.items()):
            paises = {c for _, _, c in v}
            if len(v) > 1 and len(paises) > 1:
                print(k, "->", v)

asyncio.run(main())
