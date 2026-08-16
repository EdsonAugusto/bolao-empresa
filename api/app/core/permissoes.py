"""Quem pode o quê na plataforma.

Duas ideias que se completam, e é importante não confundi-las:

**Nível** é hierarquia. É uma escada curta e ordenada, e a ordem tem
consequência: só se mexe em quem está **abaixo**. Sem isso, dois administradores
podem se rebaixar um ao outro, e a instalação vira disputa.

**Permissão** é capacidade. Cada nível já vem com um conjunto, mas o conjunto é
ajustável por pessoa e por grupo — porque a realidade não cabe em cinco caixas.
O caso que motivou isto: o amigo que organiza o bolão da firma precisa importar
campeonato, mas não precisa (nem deve) mexer nas contas dos outros.

Este módulo é **puro**: sem banco, sem HTTP, sem relógio. Ele só descreve o
vocabulário e as regras de comparação. Quem lê e grava é ``services/permissoes``.

Não confundir com ``MembershipRole``
------------------------------------
``MembershipRole`` (dono/admin/jogador) é papel **dentro de um bolão**: quem
monta a rodada daquele bolão, quem convida. O que está aqui é papel na
**instalação**. São eixos independentes de propósito: alguém pode organizar o
próprio bolão sem administrar nada da plataforma, e o contrário também.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Nivel(enum.StrEnum):
    """Escada hierárquica. A ordem está em :data:`ORDEM`, não no nome."""

    DEV = "dev"
    DONO = "dono"
    ADMIN = "admin"
    MODERADOR = "moderador"
    ORGANIZADOR = "organizador"
    JOGADOR = "jogador"


#: Peso de cada nível. Números espaçados de propósito: encaixar um nível novo no
#: meio um dia não obriga a renumerar os outros nem a migrar dado.
ORDEM: dict[Nivel, int] = {
    Nivel.DEV: 120,
    Nivel.DONO: 100,
    Nivel.ADMIN: 80,
    Nivel.MODERADOR: 60,
    Nivel.ORGANIZADOR: 40,
    Nivel.JOGADOR: 20,
}

#: Como o nível aparece na tela, e o que ele significa em uma linha.
ROTULOS: dict[Nivel, tuple[str, str]] = {
    Nivel.DEV: (
        "Desenvolvedor",
        "Quem constrói e mantém a plataforma. Acima de tudo, inclusive do dono.",
    ),
    Nivel.DONO: ("Dono", "Quem instalou esta plataforma. Manda em tudo que roda nela."),
    Nivel.ADMIN: ("Administrador", "Administra a plataforma inteira."),
    Nivel.MODERADOR: ("Moderador", "Cuida dos relatos e dos participantes."),
    Nivel.ORGANIZADOR: ("Organizador", "Cria bolões e monta rodadas."),
    Nivel.JOGADOR: ("Jogador", "Participa e palpita."),
}


class Permissao(enum.StrEnum):
    """O que uma conta pode fazer. O valor é ``area.acao``."""

    USUARIOS_VER = "usuarios.ver"
    USUARIOS_GERENCIAR = "usuarios.gerenciar"
    GRUPOS_GERENCIAR = "grupos.gerenciar"

    CAMPEONATOS_IMPORTAR = "campeonatos.importar"
    CAMPEONATOS_PLACAR = "campeonatos.placar"

    BOLOES_CRIAR = "boloes.criar"
    RODADAS_MONTAR = "rodadas.montar"

    RELATOS_TRIAR = "relatos.triar"
    PLATAFORMA_CONFIGURAR = "plataforma.configurar"


@dataclass(frozen=True, slots=True)
class Descricao:
    rotulo: str
    ajuda: str
    area: str


#: Texto de cada permissão. Fica aqui e não na tela porque a mesma frase precisa
#: aparecer no painel, na mensagem de recusa e na documentação — três lugares
#: que divergem no dia em que cada um escreve a sua versão.
DESCRICOES: dict[Permissao, Descricao] = {
    Permissao.USUARIOS_VER: Descricao(
        "Ver as contas", "Abrir o painel e ver quem tem conta na plataforma.", "Pessoas"
    ),
    Permissao.USUARIOS_GERENCIAR: Descricao(
        "Gerenciar contas",
        "Mudar o nível e as permissões de quem está abaixo, e desativar contas.",
        "Pessoas",
    ),
    Permissao.GRUPOS_GERENCIAR: Descricao(
        "Gerenciar grupos", "Criar e editar grupos de permissão.", "Pessoas"
    ),
    Permissao.CAMPEONATOS_IMPORTAR: Descricao(
        "Importar campeonato",
        "Trazer tabela, jogos e escudos. Vale para a instalação inteira.",
        "Campeonatos",
    ),
    Permissao.CAMPEONATOS_PLACAR: Descricao(
        "Lançar placar",
        "Corrigir ou lançar o resultado de qualquer jogo, o que reapura os bolões.",
        "Campeonatos",
    ),
    Permissao.BOLOES_CRIAR: Descricao(
        "Criar bolão", "Abrir um bolão novo e convidar gente.", "Bolões"
    ),
    Permissao.RODADAS_MONTAR: Descricao(
        "Montar rodada",
        "Escolher os jogos de uma rodada em bolão de rodada personalizada.",
        "Bolões",
    ),
    Permissao.RELATOS_TRIAR: Descricao(
        "Triar relatos", "Ver todos os relatos de bug, responder e mudar o estado.", "Suporte"
    ),
    Permissao.PLATAFORMA_CONFIGURAR: Descricao(
        "Configurar a plataforma",
        "Ver o endereço da instalação e os dados de diagnóstico.",
        "Plataforma",
    ),
}

#: O que cada nível já traz, sem ninguém precisar marcar nada.
#:
#: `JOGADOR` cria bolão de propósito: numa plataforma entre amigos, quem chega
#: e quer organizar o bolão da firma não deveria ter que pedir permissão a
#: alguém. Se a instalação preferir o contrário, é uma caixa a desmarcar.
PADRAO_DO_NIVEL: dict[Nivel, frozenset[Permissao]] = {
    Nivel.DEV: frozenset(Permissao),
    Nivel.DONO: frozenset(Permissao),
    Nivel.ADMIN: frozenset(Permissao),
    # O moderador gerencia contas, e isso não é contradição com "não é
    # administrador": ele só alcança quem está **abaixo** dele na escada, ou
    # seja, organizador e jogador. É exatamente o que o nome promete — e sem
    # `usuarios.gerenciar` o nível seria só um rótulo com acesso de leitura.
    Nivel.MODERADOR: frozenset(
        {
            Permissao.USUARIOS_VER,
            Permissao.USUARIOS_GERENCIAR,
            Permissao.RELATOS_TRIAR,
            Permissao.BOLOES_CRIAR,
            Permissao.RODADAS_MONTAR,
            Permissao.CAMPEONATOS_PLACAR,
        }
    ),
    Nivel.ORGANIZADOR: frozenset(
        {
            Permissao.BOLOES_CRIAR,
            Permissao.RODADAS_MONTAR,
            Permissao.CAMPEONATOS_IMPORTAR,
            Permissao.CAMPEONATOS_PLACAR,
        }
    ),
    Nivel.JOGADOR: frozenset({Permissao.BOLOES_CRIAR, Permissao.RODADAS_MONTAR}),
}

#: Níveis que fazem de alguém administrador da plataforma. Existe para manter
#: ``users.is_superuser`` em acordo com o nível — a coluna é usada em consulta e
#: em código antigo, e duas fontes de verdade divergiriam no primeiro descuido.
NIVEIS_DE_ADMINISTRACAO = frozenset({Nivel.DEV, Nivel.DONO, Nivel.ADMIN})


#: Níveis que não podem ser esvaziados: se o último sumisse, ninguém alcançaria
#: aquela posição de novo pela tela.
TOPOS = frozenset({Nivel.DEV, Nivel.DONO})


def peso(nivel: Nivel) -> int:
    return ORDEM[nivel]


def manda_em(quem: Nivel, alvo: Nivel) -> bool:
    """``quem`` pode mexer em ``alvo``?

    Estritamente maior. Igual não vale: dois administradores que possam se
    rebaixar transformam a instalação numa disputa, e o dono existe justamente
    para ser o desempate que ninguém alcança.

    **Os dois níveis de topo são exceção, e precisam ser.** Sem isso não existe
    transferência de posição: quem já está no topo não poderia nem promover
    alguém até lá (é o próprio nível), nem substituir quem estava antes — e a
    instalação ficaria presa na primeira conta para sempre, com saída só por
    linha de comando no servidor. A disputa entre iguais é contida por outra
    regra, no serviço: **nunca some o último de cada topo**.

    O dono manda em tudo **que roda na plataforma dele** — mas não em quem a
    constrói. Deixá-lo alcançar o desenvolvedor daria a ele o caminho para
    subir ao nível acima do próprio, que é justamente o que a escada impede.
    """
    if quem is Nivel.DEV:
        return True
    if quem is Nivel.DONO:
        return alvo is not Nivel.DEV
    return peso(quem) > peso(alvo)


def niveis_que_pode_conceder(quem: Nivel) -> list[Nivel]:
    """Níveis que ``quem`` pode atribuir a outra pessoa.

    Só abaixo do próprio — promover alguém ao seu nível seria criar um par capaz
    de te rebaixar no minuto seguinte. Os dois topos são exceção: são eles que
    transferem a própria posição.
    """
    return [nivel for nivel in Nivel if manda_em(quem, nivel)]


def efetivas(
    nivel: Nivel,
    *,
    de_grupos: frozenset[Permissao] = frozenset(),
    concedidas: frozenset[Permissao] = frozenset(),
    revogadas: frozenset[Permissao] = frozenset(),
) -> frozenset[Permissao]:
    """Permissões que valem de fato para uma conta.

    A conta é: o padrão do nível, mais o que os grupos trazem, mais o que foi
    concedido à pessoa, menos o que foi revogado dela.

    **Os dois topos são exceção e precisam ser.** Revogar `usuarios.gerenciar`
    de quem administra a instalação tranca todo mundo para fora do painel, sem
    caminho de volta pela tela — só por linha de comando no servidor.
    """
    if nivel in TOPOS:
        return frozenset(Permissao)

    return (PADRAO_DO_NIVEL[nivel] | de_grupos | concedidas) - revogadas


def catalogo() -> list[dict[str, object]]:
    """O vocabulário inteiro, pronto para a tela desenhar o painel."""
    return [
        {
            "chave": str(permissao),
            "rotulo": DESCRICOES[permissao].rotulo,
            "ajuda": DESCRICOES[permissao].ajuda,
            "area": DESCRICOES[permissao].area,
        }
        for permissao in Permissao
    ]


def escada() -> list[dict[str, object]]:
    """Os níveis, do mais alto para o mais baixo, com o que cada um traz."""
    return [
        {
            "chave": str(nivel),
            "rotulo": ROTULOS[nivel][0],
            "ajuda": ROTULOS[nivel][1],
            "peso": ORDEM[nivel],
            "permissoes": sorted(str(item) for item in PADRAO_DO_NIVEL[nivel]),
        }
        for nivel in sorted(Nivel, key=lambda item: -ORDEM[item])
    ]
