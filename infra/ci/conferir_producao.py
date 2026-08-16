"""Confere invariantes do Compose de produção. Roda no CI.

Existe porque as duas coisas verificadas aqui falham em silêncio: o Compose
aceita a configuração, tudo sobe, e o problema só aparece como um banco
alcançável da internet ou um deploy que se comporta como desenvolvimento.

    python3 infra/ci/conferir_producao.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

#: Único serviço que pode publicar porta no host. Postgres e Redis alcançáveis
#: de fora num servidor com IP público é o erro que este arquivo existe para
#: pegar antes de alguém pagar por ele.
PODE_PUBLICAR = {"caddy"}

#: Um bind-mount de código-fonte em produção significa que a imagem não é o
#: artefato: reconstruir apaga a alteração sem deixar rastro.
BIND_PERMITIDO = "Caddyfile"


def config_resolvida() -> str:
    ambiente = {
        "BOLAO_DOMINIO": "exemplo.test",
        "BOLAO_ACME_EMAIL": "ci@exemplo.test",
        "CLOUDFLARE_API_TOKEN": "token-de-ci",
        "POSTGRES_USER": "bolao",
        "POSTGRES_PASSWORD": "senha-de-ci",
        "POSTGRES_DB": "bolao",
    }
    saida = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.prod.yml", "config"],
        cwd=RAIZ,
        env={**os.environ, **ambiente},
        capture_output=True,
        text=True,
        check=True,
    )
    return saida.stdout


def main() -> int:
    texto = config_resolvida()
    problemas: list[str] = []
    servico = ""

    for linha in texto.split("\n"):
        casou = re.match(r"^  ([\w-]+):$", linha)
        if casou:
            servico = casou.group(1)
            continue
        if "published:" in linha and servico not in PODE_PUBLICAR:
            problemas.append(f"{servico} publica porta no host: {linha.strip()}")
        eh_source = linha.strip().startswith("source:")
        if eh_source and BIND_PERMITIDO not in linha:
            caminho = linha.split("source:", 1)[1].strip()
            # Volume nomeado aparece sem caminho absoluto; bind-mount, com.
            if "/" in caminho or "\\" in caminho:
                problemas.append(f"{servico} monta caminho do host: {caminho}")

    if problemas:
        print("docker-compose.prod.yml tem configuração imprópria para servidor público:")
        for item in problemas:
            print(f"  - {item}")
        return 1

    print("  só a borda publica porta, e nenhum código vem por bind-mount")
    return 0


if __name__ == "__main__":
    sys.exit(main())
