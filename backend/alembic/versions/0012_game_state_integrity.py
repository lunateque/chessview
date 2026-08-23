"""Persist draw offers and game-state integrity fields."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0012_game_state_integrity"
down_revision = "0011_game_player_history_indexes"
branch_labels = None
depends_on = None


FK_PENDING_DRAW_OFFER = "fk_games_pending_draw_offer_by_user_id_users"
UQ_MOVES_GAME_MOVE_NUMBER = "uq_moves_game_move_number"
DUPLICATE_MOVE_SQL = """
SELECT game_id, move_number
FROM moves
GROUP BY game_id, move_number
HAVING count(*) > 1
LIMIT 1
"""


def upgrade() -> None:
    _fail_on_duplicate_move_numbers()

    op.add_column(
        "games",
        sa.Column("pending_draw_offer_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "games",
        sa.Column("pending_draw_offer_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "games",
        sa.Column(
            "white_disconnect_grace_remaining_ms",
            sa.Integer(),
            server_default=sa.text("20000"),
            nullable=False,
        ),
    )
    op.add_column(
        "games",
        sa.Column(
            "black_disconnect_grace_remaining_ms",
            sa.Integer(),
            server_default=sa.text("20000"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        FK_PENDING_DRAW_OFFER,
        "games",
        "users",
        ["pending_draw_offer_by_user_id"],
        ["id"],
    )
    op.execute(
        sa.text(
            """
            UPDATE games
            SET white_disconnect_grace_remaining_ms = GREATEST(
                0,
                (EXTRACT(EPOCH FROM (disconnect_grace_deadline_at - now())) * 1000)::integer
            )
            WHERE disconnected_player_id = white_id
              AND disconnect_grace_deadline_at IS NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE games
            SET black_disconnect_grace_remaining_ms = GREATEST(
                0,
                (EXTRACT(EPOCH FROM (disconnect_grace_deadline_at - now())) * 1000)::integer
            )
            WHERE disconnected_player_id = black_id
              AND disconnect_grace_deadline_at IS NOT NULL
            """
        )
    )
    op.create_unique_constraint(
        UQ_MOVES_GAME_MOVE_NUMBER,
        "moves",
        ["game_id", "move_number"],
    )


def downgrade() -> None:
    op.drop_constraint(UQ_MOVES_GAME_MOVE_NUMBER, "moves", type_="unique")
    op.drop_constraint(FK_PENDING_DRAW_OFFER, "games", type_="foreignkey")
    op.drop_column("games", "black_disconnect_grace_remaining_ms")
    op.drop_column("games", "white_disconnect_grace_remaining_ms")
    op.drop_column("games", "pending_draw_offer_at")
    op.drop_column("games", "pending_draw_offer_by_user_id")


def _fail_on_duplicate_move_numbers() -> None:
    duplicate = op.get_bind().execute(sa.text(DUPLICATE_MOVE_SQL)).first()
    if duplicate is not None:
        raise RuntimeError("Cannot add uq_moves_game_move_number while duplicate move numbers exist")
