import asyncio
from app.core.db import async_session_maker
from app.services.form import retrospectos
from app.core.names import chave

async def main():
    print("chave('Vitoria SC') =", repr(chave("Vitória SC")))
    print("chave('Vitoria')    =", repr(chave("Vitória")))
    async with async_session_maker() as s:
        r = await retrospectos(s, [7, 185])
        for tid, ret in r.items():
            print(tid, ret.resumo, [(j.fixture_id, j.adversario, j.marcou, j.sofreu, str(j.kickoff_at)) for j in ret.jogos])

asyncio.run(main())
