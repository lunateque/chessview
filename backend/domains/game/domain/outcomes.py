"""Outcome and lifecycle transitions for games."""

from datetime import datetime, timedelta
from uuid import UUID

import chess

from domains.game.domain.clock import ClockSnapshot
from domains.game.domain.entities import Game
from domains.game.domain.exceptions import (
    DrawOfferNotAcceptable,
    DrawOfferNotFound,
    DrawOfferPending,
    GameAccessDenied,
    GameNotActive,
)
from domains.game.domain.value_objects import GameResult, GameStatus


def apply_board_outcome(game: Game, board: chess.Board, now: datetime) -> None:
    if board.is_checkmate():
        _finish_game(
            game,
            status=GameStatus.CHECKMATE,
            result=GameResult.WHITE_WINS if board.turn == chess.BLACK else GameResult.BLACK_WINS,
            reason="checkmate",
            now=now,
        )
        return

    if board.is_stalemate():
        _finish_game(game, status=GameStatus.STALEMATE, result=GameResult.DRAW, reason="stalemate", now=now)
        return

    if board.is_insufficient_material() or board.is_fifty_moves():
        _finish_game(game, status=GameStatus.DRAW, result=GameResult.DRAW, reason="draw", now=now)
        return

    if board.is_repetition():
        _finish_game(game, status=GameStatus.DRAW, result=GameResult.DRAW, reason="repetition", now=now)
        return

    game.last_clock_started_at = now


def resign_game(game: Game, user_id: UUID, snapshot: ClockSnapshot, now: datetime) -> None:
    result = GameResult.BLACK_WINS if user_id == game.white_id else GameResult.WHITE_WINS
    game.white_time_ms = snapshot.white_time_ms
    game.black_time_ms = snapshot.black_time_ms
    _finish_game(game, status=GameStatus.RESIGNED, result=result, reason="resignation", now=now)


def forfeit_game_for_identity_failure(game: Game, user_id: UUID, snapshot: ClockSnapshot, now: datetime) -> None:
    result = GameResult.BLACK_WINS if user_id == game.white_id else GameResult.WHITE_WINS
    game.white_time_ms = snapshot.white_time_ms
    game.black_time_ms = snapshot.black_time_ms
    _finish_game(game, status=GameStatus.RESIGNED, result=result, reason="identity_verification_failed", now=now)


def offer_draw(game: Game, user_id: UUID, now: datetime) -> None:
    _require_active_participant(game, user_id)
    if game.pending_draw_offer_by_user_id is None or game.pending_draw_offer_by_user_id == user_id:
        game.pending_draw_offer_by_user_id = user_id
        game.pending_draw_offer_at = now
        return
    raise DrawOfferPending()


def accept_draw(game: Game, user_id: UUID, snapshot: ClockSnapshot, now: datetime) -> None:
    _require_active_participant(game, user_id)
    _require_opponent_can_resolve_draw_offer(game, user_id)
    game.white_time_ms = snapshot.white_time_ms
    game.black_time_ms = snapshot.black_time_ms
    _finish_game(game, status=GameStatus.DRAW, result=GameResult.DRAW, reason="draw_agreement", now=now)


def decline_draw(game: Game, user_id: UUID) -> UUID:
    _require_active_participant(game, user_id)
    offerer_id = _require_opponent_can_resolve_draw_offer(game, user_id)
    clear_draw_offer(game)
    return offerer_id

def timeout_game(game: Game, losing_player_id: UUID, snapshot: ClockSnapshot, now: datetime, reason: str) -> None:
    if losing_player_id == game.white_id:
        white_time_ms = 0
        black_time_ms = snapshot.black_time_ms
        result = GameResult.BLACK_WINS
    else:
        white_time_ms = snapshot.white_time_ms
        black_time_ms = 0
        result = GameResult.WHITE_WINS

    game.white_time_ms = white_time_ms
    game.black_time_ms = black_time_ms
    _finish_game(game, status=GameStatus.TIMEOUT, result=result, reason=reason, now=now)


def abort_game(game: Game, snapshot: ClockSnapshot, now: datetime, reason: str) -> None:
    game.white_time_ms = snapshot.white_time_ms
    game.black_time_ms = snapshot.black_time_ms
    _finish_game(game, status=GameStatus.ABORTED, result=None, reason=reason, now=now)


def pause_for_disconnect(game: Game, user_id: UUID, snapshot: ClockSnapshot, now: datetime, grace_ms: int) -> None:
    game.white_time_ms = snapshot.white_time_ms
    game.black_time_ms = snapshot.black_time_ms
    game.last_clock_started_at = None
    game.disconnected_player_id = user_id
    game.disconnect_grace_deadline_at = now + timedelta(milliseconds=grace_ms)


def resume_after_reconnect(game: Game, now: datetime) -> None:
    if game.disconnected_player_id is not None and game.disconnect_grace_deadline_at is not None:
        remaining_ms = max(0, int((game.disconnect_grace_deadline_at - now).total_seconds() * 1000))
        _set_disconnect_grace_remaining_ms(game, game.disconnected_player_id, remaining_ms)
    game.disconnected_player_id = None
    game.disconnect_grace_deadline_at = None
    game.last_clock_started_at = now


def clear_disconnect_state(game: Game) -> None:
    game.disconnected_player_id = None
    game.disconnect_grace_deadline_at = None



def clear_draw_offer(game: Game) -> None:
    game.pending_draw_offer_by_user_id = None
    game.pending_draw_offer_at = None


def set_disconnect_grace_remaining_ms(game: Game, user_id: UUID, remaining_ms: int) -> None:
    _set_disconnect_grace_remaining_ms(game, user_id, max(0, remaining_ms))

def _finish_game(
    game: Game,
    *,
    status: GameStatus,
    result: GameResult | None,
    reason: str,
    now: datetime,
) -> None:
    game.status = status
    game.result = result
    game.termination_reason = reason
    game.ended_at = now
    game.last_clock_started_at = None
    clear_disconnect_state(game)
    clear_draw_offer(game)



def _require_active_participant(game: Game, user_id: UUID) -> None:
    if game.status != GameStatus.ACTIVE:
        raise GameNotActive()
    if user_id not in {game.white_id, game.black_id}:
        raise GameAccessDenied()


def _require_opponent_can_resolve_draw_offer(game: Game, user_id: UUID) -> UUID:
    offerer_id = game.pending_draw_offer_by_user_id
    if offerer_id is None:
        raise DrawOfferNotFound()
    if offerer_id == user_id:
        raise DrawOfferNotAcceptable()
    return offerer_id


def _set_disconnect_grace_remaining_ms(game: Game, user_id: UUID, remaining_ms: int) -> None:
    if user_id == game.white_id:
        game.white_disconnect_grace_remaining_ms = remaining_ms
    elif user_id == game.black_id:
        game.black_disconnect_grace_remaining_ms = remaining_ms