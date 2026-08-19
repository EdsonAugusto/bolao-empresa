"""CLI administrativa.

python -m app.cli seed
python -m app.cli check
python -m app.cli import-season --competition brasileirao-serie-a --year 2026
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import typer
from sqlalchemy import text

from app.core.config import settings
from app.core.db import SessionLocal, dispose_engine
from app.core.logging import configure_logging
from app.core.redis import close_redis, get_redis
from app.seed import run_seeds

cli = typer.Typer(help="Administração da plataforma de bolões", no_args_is_help=True)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@cli.command()
def seed() -> None:
    """Popula os dados base. Idempotente."""
    configure_logging()

    async def _run() -> None:
        try:
            results = await run_seeds()
        finally:
            await dispose_engine()
        for name, count in results.items():
            typer.echo(f"  {name}: {count}")
        typer.secho("seed concluído", fg=typer.colors.GREEN)

    asyncio.run(_run())


@cli.command()
def check() -> None:
    """Verifica conectividade com Postgres e Redis e imprime o ambiente."""
    configure_logging()

    async def _run() -> int:
        failures = 0

        try:
            async with SessionLocal() as session:
                version = (await session.execute(text("SELECT version()"))).scalar_one()
            typer.secho(f"  postgres  ok  {str(version).split(',')[0]}", fg=typer.colors.GREEN)
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"  postgres  FALHOU  {exc}", fg=typer.colors.RED)
            failures += 1

        try:
            await get_redis().ping()
            typer.secho("  redis     ok", fg=typer.colors.GREEN)
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"  redis     FALHOU  {exc}", fg=typer.colors.RED)
            failures += 1

        typer.echo(f"  ambiente  {settings.environment}")
        typer.echo(f"  provedor  {settings.football_provider}")

        await dispose_engine()
        await close_redis()
        return failures

    if asyncio.run(_run()):
        raise typer.Exit(code=1)


@cli.command("reset-password")
def reset_password(
    email: str = typer.Option(..., help="E-mail da conta"),
    password: str = typer.Option("", help="Senha nova. Em branco, uma forte é gerada e mostrada."),
) -> None:
    """Redefine a senha de uma conta.

    Esta plataforma não envia e-mail, então não existe link de recuperação — a
    recuperação é quem tem acesso à máquina rodar este comando. Numa instalação
    caseira isso é adequado: quem controla o servidor já controla o banco.

    Todas as sessões abertas daquela conta são encerradas. Se a senha foi
    perdida porque alguém mais a tinha, deixar as sessões vivas não resolveria
    nada.
    """
    configure_logging()

    async def _run() -> None:
        import secrets

        from sqlalchemy import select

        from app.core.security import hash_password
        from app.models import User
        from app.services.auth import normalize_email, revoke_all_sessions

        nova = password or secrets.token_urlsafe(12)

        try:
            async with SessionLocal() as session:
                user = await session.scalar(
                    select(User).where(User.email == normalize_email(email))
                )
                if user is None:
                    typer.secho(f"conta não encontrada: {email}", fg=typer.colors.RED)
                    raise typer.Exit(code=1)

                user.password_hash = hash_password(nova)
                user.is_active = True
                encerradas = await revoke_all_sessions(session, user.id)
                await session.commit()
        finally:
            await dispose_engine()

        typer.secho(
            f"senha redefinida para {user.display_name} <{user.email}>", fg=typer.colors.GREEN
        )
        if not password:
            typer.echo("")
            typer.secho(f"    {nova}", fg=typer.colors.CYAN, bold=True)
            typer.echo("")
            typer.secho(
                "  anote agora — ela não fica guardada em lugar nenhum em texto claro",
                fg=typer.colors.YELLOW,
            )
        typer.echo(f"  {encerradas} sessão(ões) encerrada(s); entre de novo nos aparelhos")

    asyncio.run(_run())


@cli.command("criar-admin")
def criar_admin(
    email: str = typer.Option(..., help="E-mail da conta de administração"),
    nome: str = typer.Option("", help="Nome de exibição. Vazio, deriva do e-mail."),
    senha: str = typer.Option("", help="Senha. Prefira --da-variavel-de-ambiente."),
    da_variavel_de_ambiente: bool = typer.Option(
        False,
        "--da-variavel-de-ambiente",
        help="Lê a senha de BOLAO_ADMIN_SENHA em vez de receber por argumento.",
    ),
) -> None:
    """Cria (ou promove) a conta de administração da instalação.

    Idempotente: se a conta já existe, ela é promovida a administradora e a
    senha é atualizada. É o que permite rodar o instalador de novo sem medo.

    A senha vem por variável de ambiente porque argumento de linha de comando
    aparece em ``ps`` para qualquer usuário da máquina e fica no histórico do
    shell. ``--senha`` continua existindo para uso interativo, com aviso.
    """
    configure_logging()

    import os

    if da_variavel_de_ambiente:
        senha = os.environ.get("BOLAO_ADMIN_SENHA", "")
        if not senha:
            typer.secho("BOLAO_ADMIN_SENHA não está definida", fg=typer.colors.RED)
            raise typer.Exit(code=1)
    elif senha:
        typer.secho(
            "  aviso: senha passada por argumento fica visível em `ps` e no histórico",
            fg=typer.colors.YELLOW,
        )
    else:
        senha = typer.prompt("Senha", hide_input=True, confirmation_prompt=True)

    if len(senha) < 10:
        typer.secho("a senha precisa de pelo menos 10 caracteres", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    async def _run() -> None:
        from sqlalchemy import select

        from app.core.permissoes import Nivel
        from app.core.security import hash_password
        from app.models import User
        from app.services.auth import normalize_email

        endereco = normalize_email(email)
        exibicao = nome.strip() or endereco.split("@", 1)[0].replace(".", " ").title()

        try:
            async with SessionLocal() as session:
                user = await session.scalar(select(User).where(User.email == endereco))
                if user is None:
                    # Nível e `is_superuser` sempre juntos: a coluna booleana é
                    # derivada do nível, e gravar só uma delas deixa a conta com
                    # poder que o painel de pessoas não mostra.
                    user = User(
                        email=endereco,
                        display_name=exibicao,
                        password_hash=hash_password(senha),
                        is_active=True,
                        nivel=str(Nivel.DONO),
                        is_superuser=True,
                    )
                    session.add(user)
                    acao = "criada"
                else:
                    user.password_hash = hash_password(senha)
                    user.nivel = str(Nivel.DONO)
                    user.is_superuser = True
                    user.is_active = True
                    acao = "atualizada"
                await session.commit()
        finally:
            await dispose_engine()

        typer.secho(f"  conta de administração {acao}: {endereco}", fg=typer.colors.GREEN)

    asyncio.run(_run())


@cli.command("definir-nivel")
def definir_nivel(
    email: str = typer.Option(..., help="E-mail da conta"),
    nivel: str = typer.Option(..., help="dev | dono | admin | moderador | organizador | jogador"),
) -> None:
    """Põe uma conta num nível da hierarquia, por fora do painel.

    Existe para dois casos que o painel não cobre por desenho: promover a
    primeira conta a ``dev`` (ninguém dentro da plataforma alcança esse nível
    para concedê-lo) e destravar uma instalação onde ninguém consegue entrar.

    Quem roda isto já tem acesso ao servidor, e portanto ao banco — a checagem
    de hierarquia aqui seria teatro.
    """
    configure_logging()

    from app.core.permissoes import ROTULOS, Nivel

    try:
        alvo_nivel = Nivel(nivel.strip().lower())
    except ValueError:
        validos = ", ".join(str(item) for item in Nivel)
        typer.secho(f"nível desconhecido: {nivel}. Use um de: {validos}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from None

    async def _run() -> None:
        from sqlalchemy import select

        from app.core.permissoes import NIVEIS_DE_ADMINISTRACAO
        from app.models import User
        from app.services.auth import normalize_email

        try:
            async with SessionLocal() as session:
                user = await session.scalar(
                    select(User).where(User.email == normalize_email(email))
                )
                if user is None:
                    typer.secho(f"conta não encontrada: {email}", fg=typer.colors.RED)
                    raise typer.Exit(code=1)

                anterior = user.nivel
                # As duas colunas sempre juntas: `is_superuser` é derivada do
                # nível e usada em consulta, e gravar só uma delas deixa a conta
                # com poder que o painel não mostra.
                user.nivel = str(alvo_nivel)
                user.is_superuser = alvo_nivel in NIVEIS_DE_ADMINISTRACAO
                await session.commit()
                nome = user.display_name
        finally:
            await dispose_engine()

        typer.secho(
            f"  {nome} <{normalize_email(email)}>: {anterior} → {alvo_nivel}",
            fg=typer.colors.GREEN,
        )
        typer.echo(f"  {ROTULOS[alvo_nivel][1]}")

    asyncio.run(_run())


@cli.command("list-users")
def list_users() -> None:
    """Lista as contas da instalação. Útil quando nem o e-mail se lembra."""
    configure_logging()

    async def _run() -> None:
        from sqlalchemy import select

        from app.core.permissoes import ROTULOS
        from app.models import User
        from app.services.permissoes import nivel_de

        try:
            async with SessionLocal() as session:
                users = (await session.scalars(select(User).order_by(User.id))).all()
        finally:
            await dispose_engine()

        if not users:
            typer.echo("nenhuma conta criada ainda")
            return

        for user in users:
            # O nível, não `is_superuser`: a coluna booleana é verdadeira para
            # dev, dono e admin igualmente, e imprimir "admin" para os três
            # esconde justamente a informação que se veio procurar aqui.
            nivel = nivel_de(user)
            marcas = [ROTULOS[nivel][0].lower()]
            if not user.is_active:
                marcas.append("sem acesso")
            typer.echo(
                f"  {user.id:>3}  {user.email:<34} {user.display_name:<22} [{', '.join(marcas)}]"
            )

    asyncio.run(_run())


@cli.command("seed-brasileirao")
def seed_brasileirao(
    year: int = typer.Option(2026, help="Ano da temporada"),
) -> None:
    """Cria a temporada completa do Brasileirão Série A.

    20 clubes, 38 rodadas, 380 jogos, turno e returno. Idempotente: rodar de
    novo atualiza em vez de duplicar.
    """
    configure_logging()

    async def _run() -> None:
        from app.services.seeding import seed_brasileirao as criar

        try:
            async with SessionLocal() as session:
                resultado = await criar(session, year)
                await session.commit()
        finally:
            await dispose_engine()

        typer.secho(f"Brasileirão {year} criado", fg=typer.colors.GREEN)
        typer.echo(f"  temporada  {resultado.season_id}")
        typer.echo(f"  clubes     {resultado.teams}")
        typer.echo(f"  rodadas    {resultado.rounds}")
        typer.echo(f"  jogos      {resultado.fixtures}")
        typer.echo(f"  rodada 1   {resultado.first_round_at:%d/%m/%Y}")
        typer.secho(
            "  atenção    calendário gerado (turno e returno completos), não é o oficial da CBF",
            fg=typer.colors.YELLOW,
        )

    asyncio.run(_run())


@cli.command("import-brasileirao")
def import_brasileirao(
    year: int = typer.Option(..., help="Ano da temporada, ex.: 2026"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Só coleta e mostra o que achou, sem gravar."
    ),
) -> None:
    """Importa o Brasileirão real coletando do site do GE Globo.

    São 38 requisições, uma por segundo — leva cerca de 40 segundos. Traz o
    calendário com datas, os placares dos jogos já realizados e os escudos
    oficiais.

    Atenção: não é uma API pública. Os termos de uso do Globo não autorizam
    coleta automatizada, e o endpoint interno pode mudar sem aviso. Se mudar,
    este comando falha com mensagem clara em vez de importar dado pela metade.
    """
    configure_logging()

    async def _run() -> None:
        from app.providers.globo import GloboProvider, GloboScrapeError
        from app.services.catalog import ingest_snapshot, record_sync_run

        provider = GloboProvider()
        iniciou = _utcnow()

        typer.echo(f"coletando as 38 rodadas de {year} (cerca de 40s)…")
        try:
            snapshot = await provider.import_season("brasileirao-serie-a", year)
        except GloboScrapeError as exc:
            typer.secho(f"coleta falhou: {exc}", fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc
        finally:
            await provider.aclose()

        realizados = sum(1 for jogo in snapshot.fixtures if jogo.home_ft is not None)
        typer.echo(f"  times      {len(snapshot.teams)}")
        typer.echo(f"  jogos      {len(snapshot.fixtures)}")
        typer.echo(f"  com placar {realizados}")

        if dry_run:
            typer.secho("dry-run: nada foi gravado", fg=typer.colors.YELLOW)
            for jogo in list(snapshot.fixtures)[:5]:
                typer.echo(f"    {jogo.kickoff_at:%d/%m %H:%M} UTC  {jogo.external_id}")
            await dispose_engine()
            return

        try:
            async with SessionLocal() as session:
                season, stats = await ingest_snapshot(
                    session, provider.slug, provider.name, snapshot
                )
                await record_sync_run(
                    session,
                    kind="import_season",
                    status="success",
                    started_at=iniciou,
                    stats=stats.as_dict(),
                )
                await session.commit()
        finally:
            await dispose_engine()

        typer.secho(f"Brasileirão {year} importado do GE", fg=typer.colors.GREEN)
        typer.echo(f"  temporada  {season.id}")
        typer.echo(f"  criados    {stats.fixtures_created}")
        typer.echo(f"  atualizados {stats.fixtures_updated}")
        typer.secho(
            "  rode `settle` ou espere o worker para apurar os jogos já realizados",
            fg=typer.colors.YELLOW,
        )

    asyncio.run(_run())


@cli.command("import-season")
def import_season(
    competition: str = typer.Option(..., help="Slug da competição, ex.: brasileirao-serie-a"),
    year: int = typer.Option(..., help="Ano da temporada, ex.: 2026"),
) -> None:
    """Importa uma temporada completa do provedor. (Fase 2)"""
    typer.secho(
        f"import-season ainda não implementado (Fase 2): {competition} {year}",
        fg=typer.colors.YELLOW,
    )
    raise typer.Exit(code=1)


if __name__ == "__main__":
    cli()


@cli.command("gerar-vapid")
def gerar_vapid() -> None:
    """Gera o par de chaves VAPID da notificação do navegador.

    Roda uma vez, na instalação. As duas linhas saem prontas para colar no
    `.env` — o instalador faz isso sozinho.

    Trocar as chaves depois INVALIDA todas as inscrições existentes: cada
    aparelho assinou contra a chave pública antiga e o serviço de push vai
    recusar. Não é perda de dado, mas todo mundo tem que autorizar de novo.
    """
    import base64

    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid01

    par = Vapid01()
    par.generate_keys()

    # Pública: ponto EC não comprimido em base64url sem preenchimento. É o
    # formato que `applicationServerKey` do navegador aceita, e só ele.
    publica = (
        base64.urlsafe_b64encode(
            par.public_key.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
        )
        .decode()
        .rstrip("=")
    )

    # Privada: os 32 bytes crus, também em base64url. É o que o pywebpush lê
    # com `Vapid02.from_raw`.
    privada = (
        base64.urlsafe_b64encode(
            par.private_key.private_numbers().private_value.to_bytes(32, "big")
        )
        .decode()
        .rstrip("=")
    )

    typer.echo(f"VAPID_PUBLIC_KEY={publica}")
    typer.echo(f"VAPID_PRIVATE_KEY={privada}")
