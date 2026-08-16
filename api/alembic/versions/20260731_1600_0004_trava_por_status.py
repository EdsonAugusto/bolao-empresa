"""trava de palpite considera o status do jogo

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-31

A versão anterior da trigger olhava só o relógio: recusava palpite quando
``now() >= kickoff_at``. Isso deixa um buraco real no modo manual, onde o
organizador lança o placar pela tela: um jogo pode estar ``FINISHED`` e já
apurado enquanto o ``kickoff_at`` ainda está no futuro — porque o placar foi
lançado antes da hora marcada, ou porque a data veio errada no CSV.

Nesse estado a trigger antiga **aceitava** palpite num jogo já pontuado, o que
permitiria alguém palpitar sabendo o resultado.

Agora vale o status primeiro: só ``SCHEDULED`` (ainda vai acontecer) e
``POSTPONED`` (remarcado, reabre) aceitam escrita.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NOVA_FUNCAO = """
CREATE OR REPLACE FUNCTION enforce_prediction_lock() RETURNS trigger AS $$
DECLARE
    fixture_kickoff timestamptz;
    fixture_state   text;
BEGIN
    SELECT kickoff_at, status
      INTO fixture_kickoff, fixture_state
      FROM fixtures
     WHERE id = NEW.fixture_id;

    IF fixture_kickoff IS NULL THEN
        RAISE EXCEPTION 'jogo % nao existe', NEW.fixture_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF TG_OP = 'UPDATE' AND NEW.fixture_id <> OLD.fixture_id THEN
        RAISE EXCEPTION 'nao e permitido mover um palpite para outro jogo'
            USING ERRCODE = 'check_violation';
    END IF;

    -- Adiado reabre: a nova data ainda nao chegou.
    IF fixture_state = 'POSTPONED' THEN
        RETURN NEW;
    END IF;

    -- Qualquer status que nao seja "ainda vai acontecer" fecha o palpite,
    -- independente do relogio.
    IF fixture_state <> 'SCHEDULED' THEN
        RAISE EXCEPTION
            'palpite fechado: o jogo % esta em %', NEW.fixture_id, fixture_state
            USING ERRCODE = 'check_violation';
    END IF;

    IF now() >= fixture_kickoff THEN
        RAISE EXCEPTION
            'palpite fechado: o jogo % comecou em %', NEW.fixture_id, fixture_kickoff
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

FUNCAO_ANTERIOR = """
CREATE OR REPLACE FUNCTION enforce_prediction_lock() RETURNS trigger AS $$
DECLARE
    fixture_kickoff timestamptz;
    fixture_state   text;
BEGIN
    SELECT kickoff_at, status
      INTO fixture_kickoff, fixture_state
      FROM fixtures
     WHERE id = NEW.fixture_id;

    IF fixture_kickoff IS NULL THEN
        RAISE EXCEPTION 'jogo % nao existe', NEW.fixture_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF fixture_state = 'POSTPONED' THEN
        RETURN NEW;
    END IF;

    IF now() >= fixture_kickoff THEN
        RAISE EXCEPTION
            'palpite fechado: o jogo % comecou em %', NEW.fixture_id, fixture_kickoff
            USING ERRCODE = 'check_violation';
    END IF;

    IF TG_OP = 'UPDATE' AND NEW.fixture_id <> OLD.fixture_id THEN
        RAISE EXCEPTION 'nao e permitido mover um palpite para outro jogo'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(NOVA_FUNCAO)


def downgrade() -> None:
    op.execute(FUNCAO_ANTERIOR)
