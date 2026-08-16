"""Comparação de nome de clube.

Mora em ``core`` porque tanto ``services`` quanto ``providers`` precisam da
mesma forma normalizada, e provedor não pode importar serviço — a seta de
dependência vai de ``services`` para ``providers``, nunca ao contrário.

O problema que isto resolve: cada fonte chama o mesmo clube de um jeito.
``Man Utd`` no CSV, ``Manchester United`` no GE, ``FC Bayern München`` numa,
``Bayern de Munique`` na outra.
"""

from __future__ import annotations

import re
import unicodedata

#: Termos de forma jurídica e afins. Saem do nome antes de comparar porque não
#: distinguem clube nenhum: "FC Porto" e "Porto" são o mesmo time.
RUIDO = frozenset(
    {
        "fc", "cf", "sc", "ac", "afc", "cd", "rc", "rcd", "ss", "us", "as", "sv",
        "bsc", "ec", "se", "sad", "ud", "sco", "losc", "ogc", "aj", "cp", "ad",
        "ca", "club", "clube", "futebol", "football", "calcio", "de", "do", "da",
        "the", "team",
    }
)  # fmt: skip


def normalizar(nome: str) -> str:
    """Minúsculas, sem acento e sem pontuação. Preserva todas as palavras."""
    texto = unicodedata.normalize("NFKD", nome.lower())
    texto = "".join(letra for letra in texto if not unicodedata.combining(letra))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", texto)).strip()


def chave(nome: str) -> str:
    """Forma de comparação: normalizada e sem os termos de forma jurídica.

    Nunca devolve vazio — um clube chamado só ``AC`` continua sendo ``ac``, e
    perder o nome inteiro faria clubes diferentes colidirem numa chave vazia.
    """
    palavras = [palavra for palavra in normalizar(nome).split() if palavra not in RUIDO]
    return " ".join(palavras) or normalizar(nome)
