"""Testes do casamento de escudo.

O risco aqui não é ficar sem escudo — é pôr o escudo errado. Escudo trocado num
jogo é um erro que a pessoa vê na hora e não sabe explicar, e que sobrevive a
qualquer recoleta porque o campo já está preenchido. Por isso a maior parte
destes testes verifica **recusa**, não acerto.
"""

from __future__ import annotations

from app.core.names import RUIDO, chave, normalizar
from app.providers.thesportsdb import APELIDOS, LIGAS, TheSportsDbCrests

# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------


def test_acento_e_pontuacao_somem() -> None:
    assert normalizar("Atlético-MG") == "atletico mg"
    assert normalizar("Nott'm Forest") == "nott m forest"
    assert normalizar("FC Bayern München") == "fc bayern munchen"


def test_forma_juridica_nao_distingue_clube() -> None:
    """``FC Porto`` e ``Porto`` são o mesmo time."""
    assert chave("FC Porto") == chave("Porto") == "porto"
    assert chave("Sporting CP") == chave("Sporting")


def test_nome_que_e_so_ruido_nao_vira_chave_vazia() -> None:
    """Chave vazia faria clubes diferentes colidirem no mesmo escudo."""
    assert chave("AC") == "ac"
    assert chave("FC") == "fc"
    for termo in RUIDO:
        assert chave(termo) != ""


def test_clubes_diferentes_nao_colidem() -> None:
    nomes = ["Internacional", "Inter", "Milan", "AC Milan", "Atlético-MG", "Atlético-GO"]
    chaves = [chave(nome) for nome in nomes]
    assert chave("Internacional") != chave("Inter")
    assert chave("Atlético-MG") != chave("Atlético-GO")
    assert len(set(chaves)) == len(nomes) - 1  # só "Milan" e "AC Milan" coincidem


# ---------------------------------------------------------------------------
# Tabelas de tradução — erram calado se as chaves não forem normalizadas
# ---------------------------------------------------------------------------


def test_chaves_das_tabelas_estao_na_forma_de_comparacao() -> None:
    """Chave fora da forma de :func:`chave` nunca casaria, e ninguém veria."""
    for tabela in (LIGAS, APELIDOS):
        for entrada in tabela:
            assert chave(entrada) == entrada, entrada


def test_apelidos_cobrem_as_abreviacoes_do_csv() -> None:
    for abreviado, esperado in [
        ("Man Utd", "Manchester United"),
        ("Spurs", "Tottenham Hotspur"),
        ("Nott'm Forest", "Nottingham Forest"),
        ("Inter", "Internazionale"),
    ]:
        assert APELIDOS[chave(abreviado)] == esperado


# ---------------------------------------------------------------------------
# Escolha do clube — o que impede escudo trocado
# ---------------------------------------------------------------------------


def resultado(nome: str, liga: str, badge: str = "http://escudo", **extra: object) -> dict:
    return {
        "strTeam": nome,
        "strLeague": liga,
        "strSport": "Soccer",
        "strBadge": badge,
        **extra,
    }


def test_nome_identico_na_liga_certa_e_aceito() -> None:
    achado = TheSportsDbCrests._escolher(
        [resultado("Arsenal", "English Premier League", "http://arsenal")],
        "English Premier League",
        "Arsenal",
    )
    assert achado == "http://arsenal"


def test_homonimo_de_outra_liga_e_recusado() -> None:
    """Buscar "Man Utd" devolve um time da copa da Finlândia antes do inglês."""
    achado = TheSportsDbCrests._escolher(
        [resultado("Puimur Mando Utd", "Finnish Cup", "http://errado")],
        "English Premier League",
        "Man Utd",
    )
    assert achado is None


def test_varios_candidatos_na_liga_sem_nome_igual_e_recusado() -> None:
    """Dois plausíveis e nenhum idêntico: não dá para escolher, então não escolhe."""
    achado = TheSportsDbCrests._escolher(
        [
            resultado("Manchester United U21", "English Premier League", "http://a"),
            resultado("Manchester United Women", "English Premier League", "http://b"),
        ],
        "English Premier League",
        "Man Utd",
    )
    assert achado is None


def test_unico_candidato_na_liga_e_aceito() -> None:
    """O filtro por liga já descartou os homônimos; sobrando um, é ele."""
    achado = TheSportsDbCrests._escolher(
        [
            resultado("Wolverhampton Wanderers", "English Premier League", "http://certo"),
            resultado("Wolverhampton FC", "Brazilian Serie A", "http://errado"),
        ],
        "English Premier League",
        "Wolves",
    )
    assert achado == "http://certo"


def test_nome_alternativo_conta() -> None:
    achado = TheSportsDbCrests._escolher(
        [
            resultado(
                "Bayern Munich",
                "German Bundesliga",
                "http://bayern",
                strAlternate="FC Bayern München, Bayern",
            )
        ],
        "German Bundesliga",
        "FC Bayern München",
    )
    assert achado == "http://bayern"


def test_resultado_sem_escudo_e_ignorado() -> None:
    achado = TheSportsDbCrests._escolher(
        [resultado("Arsenal", "English Premier League", badge="")],
        "English Premier League",
        "Arsenal",
    )
    assert achado is None


def test_outro_esporte_e_ignorado() -> None:
    """Existe Arsenal de futsal, de basquete e por aí vai."""
    achado = TheSportsDbCrests._escolher(
        [
            {
                "strTeam": "Arsenal",
                "strLeague": "English Premier League",
                "strSport": "Basketball",
                "strBadge": "http://errado",
            }
        ],
        "English Premier League",
        "Arsenal",
    )
    assert achado is None


def test_sem_liga_conhecida_so_aceita_nome_identico() -> None:
    """Competição fora da tabela: sem liga para filtrar, o critério aperta."""
    assert (
        TheSportsDbCrests._escolher(
            [resultado("Outro Clube", "Liga Qualquer", "http://errado")], None, "Meu Time"
        )
        is None
    )
    assert (
        TheSportsDbCrests._escolher(
            [resultado("Meu Time FC", "Liga Qualquer", "http://certo")], None, "Meu Time"
        )
        == "http://certo"
    )
