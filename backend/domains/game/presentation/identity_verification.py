"""Presentation helpers for game-bound identity verification outcomes."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


async def broadcast_identity_verification_forfeit(game_id: UUID, stopped_game: object, session: AsyncSession) -> None:
    from domains.game.presentation.ws_handler import _broadcast_game_over

    await _broadcast_game_over(
        stopped_game,
        session,
        getattr(stopped_game, "termination_reason", None) or "identity_verification_failed",
    )
