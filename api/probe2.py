import asyncio
from app.core.db import SessionLocal
from app.services.form import retrospectos
from app.core.names import chave

async def main():
    print("chave('Vitoria SC') =", repr(chave("Vit\u00f3ria SC")))
    print("chave('Vitoria')    =", repr(chave("Vit\u00f3ria")))
    async with SessionLocal() as s:
        r = await retrospectos(s, [7, 185])
        for tid, ret in r.items():
            print(tid, "->", ret.resumo)
            for j in ret.jogos:
                print("   ", j.fixture_id, j.adversario, j.marcou, "-", j.sofreu, "casa" if j.em_casa else "fora", j.kickoff_at)

asyncio.run(main())
