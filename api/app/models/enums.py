"""Enums de domínio.

Todos são materializados como VARCHAR + CHECK (``native_enum=False``), não como
tipo ENUM nativo do Postgres. Enum nativo obriga a escrever ALTER TYPE à mão em
toda migration que acrescenta um valor, e o autogenerate do Alembic não enxerga
a mudança — é uma fonte silenciosa de migration quebrada.
"""

from __future__ import annotations

import enum


class FixtureStatus(enum.StrEnum):
    """Estado de um jogo.

    Máquina de estados::

        SCHEDULED → LIVE → HT → LIVE → FT/AET/PEN → FINISHED
                  ↘ POSTPONED / SUSPENDED / CANCELLED / ABANDONED

    Só ``FINISHED`` dispara apuração. O status cru do provedor nunca entra no
    banco sem passar pela camada de tradução.
    """

    SCHEDULED = "SCHEDULED"
    LIVE = "LIVE"
    HT = "HT"
    FT = "FT"
    AET = "AET"
    PEN = "PEN"
    FINISHED = "FINISHED"
    POSTPONED = "POSTPONED"
    SUSPENDED = "SUSPENDED"
    CANCELLED = "CANCELLED"
    ABANDONED = "ABANDONED"

    @property
    def is_final(self) -> bool:
        """Terminou e vale pontuação."""
        return self is FixtureStatus.FINISHED

    @property
    def is_void(self) -> bool:
        """Não acontece: ninguém pontua e o jogo sai do denominador."""
        return self in {FixtureStatus.CANCELLED, FixtureStatus.ABANDONED}

    @property
    def is_open_for_predictions(self) -> bool:
        """Ainda aceita palpite, do ponto de vista do status.

        O corte de verdade é ``kickoff_at``, garantido por trigger no banco.
        Isto aqui só cobre o caso do jogo adiado, que reabre.
        """
        return self in {FixtureStatus.SCHEDULED, FixtureStatus.POSTPONED}


class CompetitionType(enum.StrEnum):
    LEAGUE = "league"
    CUP = "cup"
    LEAGUE_CUP = "league_cup"
    GROUP_KNOCKOUT = "group_knockout"


class PoolKind(enum.StrEnum):
    SEASON = "season"
    """Bolão de campeonato: as rodadas vêm da temporada."""

    CUSTOM = "custom"
    """Rodada montada pelo organizador, com jogos de qualquer campeonato."""


class PoolStatus(enum.StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    RUNNING = "running"
    FINISHED = "finished"
    ARCHIVED = "archived"


class PoolVisibility(enum.StrEnum):
    PRIVATE = "private"
    """Só entra quem tem o código de convite."""

    UNLISTED = "unlisted"
    """Qualquer usuário autenticado com o link entra."""

    PUBLIC = "public"
    """Aparece na lista de bolões da instância."""


class MembershipRole(enum.StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    PLAYER = "player"

    @property
    def can_manage(self) -> bool:
        return self in {MembershipRole.OWNER, MembershipRole.ADMIN}


class MembershipStatus(enum.StrEnum):
    ACTIVE = "active"
    LEFT = "left"
    REMOVED = "removed"


class ScoringMode(enum.StrEnum):
    CLASSIC = "classic"
    SIMPLE = "simple"
    CUSTOM = "custom"


class CriterionKey(enum.StrEnum):
    """Critérios de pontuação, do mais específico ao menos.

    A ordem desta declaração é só documentação: a avaliação real acontece em
    ordem decrescente de pontos, porque no modo personalizado o organizador
    pode inverter os valores.
    """

    EXACT = "exact"
    WINNER_AND_ONE_SCORE = "winner_and_one_score"
    WINNER_AND_GOAL_DIFF = "winner_and_goal_diff"
    DRAW = "draw"
    WINNER_ONLY = "winner_only"
    ONE_SCORE_ONLY = "one_score_only"
    MISS = "miss"


class BonusKind(enum.StrEnum):
    CHAMPION = "champion"
    TOP4 = "top4"
    RELEGATED = "relegated"
    KNOCKOUT_ADVANCE = "knockout_advance"


class SyncKind(enum.StrEnum):
    CATALOG = "catalog"
    FIXTURES = "fixtures"
    LIVE = "live"
    RECONCILE = "reconcile"
    IMPORT_SEASON = "import_season"


class SyncStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class NotificationTemplate(enum.StrEnum):
    ROUND_OPEN = "round_open"
    PREDICTION_REMINDER = "prediction_reminder"
    ROUND_CLOSED = "round_closed"
    FIXTURE_SETTLED = "fixture_settled"
    ROUND_SETTLED = "round_settled"
    POSITION_CHANGED = "position_changed"
    SCORE_CORRECTED = "score_corrected"
    POOL_FINISHED = "pool_finished"
    MEMBER_JOINED = "member_joined"


class NotificationStatus(enum.StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"
    """Usuário optou por não receber, ou o canal não está configurado."""


class NotificationChannelKind(enum.StrEnum):
    IN_APP = "in_app"
    TELEGRAM = "telegram"
    LOG = "log"


class AuditAction(enum.StrEnum):
    POOL_CREATED = "pool_created"
    POOL_UPDATED = "pool_updated"
    POOL_DELETED = "pool_deleted"
    SCORING_CONFIG_VERSIONED = "scoring_config_versioned"
    MEMBER_JOINED = "member_joined"
    MEMBER_REMOVED = "member_removed"
    FIXTURE_SETTLED = "fixture_settled"
    FIXTURE_RESETTLED = "fixture_resettled"
    SCORE_CORRECTED = "score_corrected"
    STANDINGS_RECOMPUTED = "standings_recomputed"
    ACCOUNT_DELETED = "account_deleted"
    # `audit_log.action` é `String(64)`, não enum do banco: acrescentar valor
    # aqui não pede migration.
    USER_LEVEL_CHANGED = "user_level_changed"
    USER_ACCESS_CHANGED = "user_access_changed"


class ReportKind(enum.StrEnum):
    """O que a pessoa está mandando."""

    BUG = "bug"
    FEEDBACK = "feedback"
    IDEIA = "ideia"


class ReportSeverity(enum.StrEnum):
    """Quanto atrapalha. Só faz sentido em bug, e a tela reflete isso."""

    BAIXA = "baixa"
    MEDIA = "media"
    ALTA = "alta"
    CRITICA = "critica"


class ReportStatus(enum.StrEnum):
    """Onde o relato está na fila."""

    ABERTO = "aberto"
    TRIADO = "triado"
    FAZENDO = "fazendo"
    RESOLVIDO = "resolvido"
    DESCARTADO = "descartado"

    @property
    def is_final(self) -> bool:
        return self in {ReportStatus.RESOLVIDO, ReportStatus.DESCARTADO}
