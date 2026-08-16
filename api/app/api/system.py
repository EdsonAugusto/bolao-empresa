"""Informações da instalação.

Existe por causa do modo rede local: a pessoa precisa saber em que endereço
abrir a plataforma no celular, e ninguém quer descobrir o IP da máquina no
`ipconfig` para depois ditá-lo em voz alta.
"""

from __future__ import annotations

import socket

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.models import Pool, User

router = APIRouter(prefix="/system", tags=["sistema"])


class LanInfo(BaseModel):
    hostname: str
    addresses: list[str]
    port: int
    urls: list[str]
    hint: str


class InstallInfo(BaseModel):
    project_name: str
    environment: str
    provider: str
    notification_channels: list[str]
    users: int
    pools: int
    real_money: bool = False


def _local_addresses() -> list[str]:
    """IPs da máquina nas redes locais.

    O truque do socket UDP não envia pacote nenhum: só faz o SO escolher a
    interface que sairia para a rede, que é justamente a que os celulares da
    casa conseguem alcançar.
    """
    found: set[str] = set()

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.168.255.255", 1))
        found.add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if _is_private(address):
                found.add(address)
    except socket.gaierror:
        pass

    return sorted(address for address in found if _is_private(address))


def _is_private(address: str) -> bool:
    if address.startswith(("10.", "192.168.")):
        return True
    if address.startswith("172."):
        second = int(address.split(".")[1]) if address.count(".") >= 1 else 0
        return 16 <= second <= 31
    return False


@router.get("/lan", response_model=LanInfo)
async def lan_info(user: CurrentUser) -> LanInfo:
    addresses = _local_addresses()
    port = 8080
    return LanInfo(
        hostname=socket.gethostname(),
        addresses=addresses,
        port=port,
        urls=[f"http://{address}:{port}" for address in addresses],
        hint=(
            "Abra esse endereço no celular, com o aparelho na mesma rede Wi-Fi. "
            "Se não abrir, libere a porta no firewall do computador."
        ),
    )


class ComoEntrar(BaseModel):
    """O pouco que a tela de entrada precisa saber antes de alguém ter conta."""

    registration_mode: str
    needs_invite: bool
    is_first_account: bool
    """Instalação recém-criada: a primeira conta entra sem convite e administra."""


@router.get("/entrada", response_model=ComoEntrar)
async def como_entrar(session: SessionDep) -> ComoEntrar:
    """Público de propósito: é lido antes de existir sessão.

    Não expõe nada que já não se descubra tentando criar conta — e evita que a
    tela peça um código de convite onde ele não é exigido, ou deixe de pedir
    onde é.
    """
    primeira = (await session.scalar(select(User.id).limit(1))) is None
    return ComoEntrar(
        registration_mode=settings.registration_mode,
        needs_invite=settings.registration_mode == "convite" and not primeira,
        is_first_account=primeira,
    )


@router.get("/info", response_model=InstallInfo)
async def install_info(session: SessionDep, user: CurrentUser) -> InstallInfo:
    users = await session.scalar(select(func.count()).select_from(User))
    pools = await session.scalar(select(func.count()).select_from(Pool))
    return InstallInfo(
        project_name=settings.project_name,
        environment=settings.environment,
        provider=settings.football_provider,
        notification_channels=settings.notification_channels,
        users=users or 0,
        pools=pools or 0,
    )
