"""trava de palpite no banco

Revision ID: 0003
Revises: c2a8e71737f6
Create Date: 2026-07-31

A regra "não se palpita depois do apito" é garantida **aqui**, não na
aplicação. Um worker atrasado, um endpoint novo que alguém esqueceu de
proteger, um script de correção rodado à mão — nada disso pode abrir brecha.
Se a checagem estiver só no Python, ela vale até o dia em que um caminho novo
escrever na tabela.

A trigger dispara em INSERT e em UPDATE. Em UPDATE ela também impede trocar o
``fixture_id`` de um palpite, que seria a maneira óbvia de contornar a regra.

``settle`` legítimo não passa por aqui: a apuração escreve em
``prediction_scores``, nunca em ``predictions``.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "c2a8e71737f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TRIGGER_FUNCTION = """
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

    -- Jogo adiado reabre para palpite: a nova data ainda nao chegou, e seria
    -- injusto manter travado um palpite que ninguem pode mais revisar.
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

CREATE_TRIGGER = """
CREATE TRIGGER trg_predictions_lock
    BEFORE INSERT OR UPDATE ON predictions
    FOR EACH ROW EXECUTE FUNCTION enforce_prediction_lock();
"""

# Mesma ideia para os palpites de bônus, que têm o instante de trava na
# própria linha (campeão, G-4, rebaixados travam no início da temporada).
BONUS_FUNCTION = """
CREATE OR REPLACE FUNCTION enforce_bonus_prediction_lock() RETURNS trigger AS $$
BEGIN
    IF now() >= NEW.locks_at THEN
        RAISE EXCEPTION 'palpite de bonus fechado desde %', NEW.locks_at
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

CREATE_BONUS_TRIGGER = """
CREATE TRIGGER trg_bonus_predictions_lock
    BEFORE INSERT OR UPDATE ON bonus_predictions
    FOR EACH ROW EXECUTE FUNCTION enforce_bonus_prediction_lock();
"""

# A apuração precisa gravar points_awarded/settled_at DEPOIS da trava. Sem esta
# exceção, apurar um palpite de bonus seria impossivel.
BONUS_FUNCTION_SETTLE_AWARE = """
CREATE OR REPLACE FUNCTION enforce_bonus_prediction_lock() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND NEW.payload IS NOT DISTINCT FROM OLD.payload
       AND NEW.kind = OLD.kind
       AND NEW.reference_id = OLD.reference_id THEN
        -- Só apuração: o palpite em si não mudou.
        RETURN NEW;
    END IF;

    IF now() >= NEW.locks_at THEN
        RAISE EXCEPTION 'palpite de bonus fechado desde %', NEW.locks_at
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(TRIGGER_FUNCTION)
    op.execute(CREATE_TRIGGER)
    op.execute(BONUS_FUNCTION)
    op.execute(CREATE_BONUS_TRIGGER)
    op.execute(BONUS_FUNCTION_SETTLE_AWARE)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_bonus_predictions_lock ON bonus_predictions")
    op.execute("DROP FUNCTION IF EXISTS enforce_bonus_prediction_lock()")
    op.execute("DROP TRIGGER IF EXISTS trg_predictions_lock ON predictions")
    op.execute("DROP FUNCTION IF EXISTS enforce_prediction_lock()")
