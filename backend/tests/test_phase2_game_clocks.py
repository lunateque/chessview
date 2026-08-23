from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from domains.game.application.commands import (
    AcceptDrawCommand,
    DeclineDrawCommand,
    MakeMoveCommand,
    OfferDrawCommand,
)
from domains.game.application.services import GameService
from domains.game.domain.entities import Game, Move
from domains.game.domain.exceptions import (
    DrawOfferNotFound,
    DrawOfferPending,
    GameAccessDenied,
)
from domains.game.domain.policies import DEFAULT_DISCONNECT_GRACE_SECONDS


class InMemoryGameRepository:
    def __init__(self) -> None:
        self.games: dict = {}
        self.moves: dict = {}

    async def create(self, game: Game) -> Game:
        self.games[game.id] = game
        self.moves[game.id] = []
        return game

    async def get_by_id(self, game_id):
        return self.games.get(game_id)

    async def get_by_id_for_update(self, game_id):
        return await self.get_by_id(game_id)

    async def get_active_by_user(self, user_id):
        for game in self.games.values():
            if game.status == "active" and user_id in (game.white_id, game.black_id):
                return game
        return None

    async def get_active_by_user_for_update(self, user_id):
        return await self.get_active_by_user(user_id)

    async def list_active(self):
        return [game for game in self.games.values() if game.status == "active"]

    async def update(self, game: Game) -> Game:
        self.games[game.id] = game
        return game

    async def list_by_user(self, user_id, page=1, size=20):
        games = [game for game in self.games.values() if user_id in (game.white_id, game.black_id)]
        return games, len(games)

    async def add_move(self, move: Move) -> Move:
        current_moves = self.moves.setdefault(move.game_id, [])
        move.id = len(current_moves) + 1
        current_moves.append(move)
        return move

    async def get_moves(self, game_id):
        return list(self.moves.get(game_id, []))

    async def get_move_counts(self, game_ids):
        return {game_id: len(self.moves.get(game_id, [])) for game_id in game_ids}

    async def commit(self):
        return None

    async def rollback(self):
        return None



def _make_active_game(white_id, black_id, now, **overrides):
    game = Game(
        white_id=white_id,
        black_id=black_id,
        last_clock_started_at=now,
        started_at=now,
        **overrides,
    )
    _set_disconnect_budgets(game)
    return game


def _set_disconnect_budgets(game, white_ms=20_000, black_ms=20_000):
    game.white_disconnect_grace_remaining_ms = white_ms
    game.black_disconnect_grace_remaining_ms = black_ms
    return game


def _remaining_disconnect_budget_ms(game, user_id):
    if user_id == game.white_id:
        return game.white_disconnect_grace_remaining_ms
    return game.black_disconnect_grace_remaining_ms


def _assert_disconnect_timeout(game, losing_player_id, now):
    assert game.status == "timeout"
    assert game.termination_reason == "disconnect_timeout"
    assert game.ended_at == now
    if losing_player_id == game.white_id:
        assert game.result == "0-1"
    else:
        assert game.result == "1-0"


@pytest.mark.asyncio
async def test_disconnect_grace_auto_aborts_game_before_meaningful_start(monkeypatch):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    now = datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc)

    game = Game(
        white_id=white_id,
        black_id=black_id,
        last_clock_started_at=now,
        started_at=now,
    )
    await repo.create(game)

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: now)
    disconnected_game = await service.mark_disconnected(white_id)
    assert disconnected_game is not None

    deadline = now + timedelta(seconds=DEFAULT_DISCONNECT_GRACE_SECONDS + 1)
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: deadline)
    finished_games = await service.monitor_active_games()

    assert len(finished_games) == 1
    assert finished_games[0].status == "aborted"
    assert finished_games[0].result is None
    assert finished_games[0].termination_reason == "auto_abort"


@pytest.mark.asyncio
async def test_disconnect_grace_times_out_meaningfully_started_game(monkeypatch):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    now = datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc)

    game = Game(
        white_id=white_id,
        black_id=black_id,
        last_clock_started_at=now,
        started_at=now,
    )
    await repo.create(game)

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: now)
    await service.make_move(MakeMoveCommand(game_id=game.id, user_id=white_id, uci="e2e4"))
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: now + timedelta(seconds=1))
    await service.make_move(MakeMoveCommand(game_id=game.id, user_id=black_id, uci="e7e5"))

    disconnect_time = now + timedelta(seconds=2)
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: disconnect_time)
    await service.mark_disconnected(white_id)

    deadline = disconnect_time + timedelta(seconds=DEFAULT_DISCONNECT_GRACE_SECONDS + 1)
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: deadline)
    finished_games = await service.monitor_active_games()

    assert len(finished_games) == 1
    assert finished_games[0].status == "timeout"
    assert finished_games[0].result == "0-1"
    assert finished_games[0].termination_reason == "disconnect_timeout"



@pytest.mark.asyncio
async def test_disconnect_grace_partial_budget_persists_and_reused_on_next_disconnect(monkeypatch):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    start = datetime(2026, 4, 13, 12, 30, tzinfo=timezone.utc)
    game = _make_active_game(white_id, black_id, start)
    await repo.create(game)

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: start)
    disconnected_game = await service.mark_disconnected(white_id)
    assert disconnected_game is game
    assert game.disconnect_grace_deadline_at == start + timedelta(seconds=20)

    reconnected_at = start + timedelta(seconds=5)
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: reconnected_at)
    reconnected_game = await service.mark_reconnected(white_id)

    assert reconnected_game is game
    assert game.status == "active"
    assert game.disconnected_player_id is None
    assert game.disconnect_grace_deadline_at is None
    assert _remaining_disconnect_budget_ms(game, white_id) == 15_000

    second_disconnect_at = reconnected_at + timedelta(seconds=2)
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: second_disconnect_at)
    await service.mark_disconnected(white_id)

    assert game.disconnected_player_id == white_id
    assert game.disconnect_grace_deadline_at == second_disconnect_at + timedelta(milliseconds=15_000)


@pytest.mark.asyncio
@pytest.mark.parametrize("delta", [timedelta(0), timedelta(milliseconds=1)], ids=["exact", "after"])
async def test_reconnect_at_or_after_disconnect_deadline_auto_aborts_without_meaningful_play(monkeypatch, delta):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    start = datetime(2026, 4, 13, 13, 0, tzinfo=timezone.utc)
    game = _make_active_game(white_id, black_id, start)
    await repo.create(game)

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: start)
    await service.mark_disconnected(white_id)

    reconnect_at = start + timedelta(seconds=20) + delta
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: reconnect_at)
    reconnected_game = await service.mark_reconnected(white_id)

    assert reconnected_game is game
    assert game.status == "aborted"
    assert game.result is None
    assert game.termination_reason == "auto_abort"
    assert game.ended_at == reconnect_at
    assert game.disconnected_player_id is None
    assert game.disconnect_grace_deadline_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("delta", [timedelta(0), timedelta(milliseconds=1)], ids=["exact", "after"])
async def test_reconnect_at_or_after_disconnect_deadline_times_out_meaningful_game(monkeypatch, delta):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    start = datetime(2026, 4, 13, 13, 30, tzinfo=timezone.utc)
    game = _make_active_game(white_id, black_id, start)
    await repo.create(game)

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: start)
    await service.make_move(MakeMoveCommand(game_id=game.id, user_id=white_id, uci="e2e4"))
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: start + timedelta(seconds=1))
    await service.make_move(MakeMoveCommand(game_id=game.id, user_id=black_id, uci="e7e5"))

    disconnected_at = start + timedelta(seconds=2)
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: disconnected_at)
    await service.mark_disconnected(white_id)

    reconnect_at = disconnected_at + timedelta(seconds=20) + delta
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: reconnect_at)
    reconnected_game = await service.mark_reconnected(white_id)

    assert reconnected_game is game
    _assert_disconnect_timeout(game, white_id, reconnect_at)
    assert game.disconnected_player_id is None
    assert game.disconnect_grace_deadline_at is None


@pytest.mark.asyncio
async def test_opponent_reconnect_at_disconnect_deadline_finalizes_disconnected_player(monkeypatch):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    start = datetime(2026, 4, 13, 13, 40, tzinfo=timezone.utc)
    game = _make_active_game(white_id, black_id, start)
    await repo.create(game)

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: start)
    await service.make_move(MakeMoveCommand(game_id=game.id, user_id=white_id, uci="e2e4"))
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: start + timedelta(seconds=1))
    await service.make_move(MakeMoveCommand(game_id=game.id, user_id=black_id, uci="e7e5"))

    disconnected_at = start + timedelta(seconds=2)
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: disconnected_at)
    await service.mark_disconnected(white_id)

    deadline = disconnected_at + timedelta(seconds=20)
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: deadline)
    reconnected_game = await service.mark_reconnected(black_id)

    assert reconnected_game is game
    _assert_disconnect_timeout(game, white_id, deadline)


@pytest.mark.asyncio
async def test_move_at_disconnect_deadline_finalizes_timeout_without_applying_move(monkeypatch):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    start = datetime(2026, 4, 13, 13, 45, tzinfo=timezone.utc)
    game = _make_active_game(white_id, black_id, start)
    await repo.create(game)

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: start)
    await service.make_move(MakeMoveCommand(game_id=game.id, user_id=white_id, uci="e2e4"))
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: start + timedelta(seconds=1))
    await service.make_move(MakeMoveCommand(game_id=game.id, user_id=black_id, uci="e7e5"))

    disconnected_at = start + timedelta(seconds=2)
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: disconnected_at)
    await service.mark_disconnected(white_id)

    deadline = disconnected_at + timedelta(seconds=20)
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: deadline)
    finished_game, move = await service.make_move(
        MakeMoveCommand(game_id=game.id, user_id=white_id, uci="g1f3")
    )

    assert move is None
    assert len(await repo.get_moves(game.id)) == 2
    _assert_disconnect_timeout(finished_game, white_id, deadline)


@pytest.mark.parametrize("action", ["move", "offer_draw", "accept_draw", "decline_draw"])
@pytest.mark.asyncio
async def test_nonparticipant_cannot_finalize_expired_disconnect(monkeypatch, action):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    outsider_id = uuid4()
    start = datetime(2026, 4, 13, 13, 50, tzinfo=timezone.utc)
    game = _make_active_game(white_id, black_id, start)
    await repo.create(game)

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: start)
    await service.mark_disconnected(white_id)
    deadline = start + timedelta(seconds=20)
    game.pending_draw_offer_by_user_id = white_id
    game.pending_draw_offer_at = start
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: deadline)

    with pytest.raises(GameAccessDenied):
        if action == "move":
            await service.make_move(
                MakeMoveCommand(game_id=game.id, user_id=outsider_id, uci="e2e4")
            )
        elif action == "offer_draw":
            await service.offer_draw(OfferDrawCommand(game_id=game.id, user_id=outsider_id))
        elif action == "accept_draw":
            await service.accept_draw(AcceptDrawCommand(game_id=game.id, user_id=outsider_id))
        else:
            await service.decline_draw(
                DeclineDrawCommand(game_id=game.id, user_id=outsider_id)
            )

    assert game.status == "active"
    assert game.disconnected_player_id == white_id
    assert game.disconnect_grace_deadline_at == deadline
    assert game.pending_draw_offer_by_user_id == white_id
    assert game.pending_draw_offer_at == start


@pytest.mark.asyncio
async def test_zero_disconnect_budget_auto_aborts_immediately_without_meaningful_play(monkeypatch):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    now = datetime(2026, 4, 13, 14, 0, tzinfo=timezone.utc)
    game = _make_active_game(white_id, black_id, now)
    _set_disconnect_budgets(game, white_ms=0)
    await repo.create(game)

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: now)
    disconnected_game = await service.mark_disconnected(white_id)

    assert disconnected_game is game
    assert game.status == "aborted"
    assert game.result is None
    assert game.termination_reason == "auto_abort"
    assert game.ended_at == now
    assert game.disconnected_player_id is None
    assert game.disconnect_grace_deadline_at is None


@pytest.mark.asyncio
async def test_zero_disconnect_budget_times_out_immediately_after_meaningful_play(monkeypatch):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    start = datetime(2026, 4, 13, 14, 30, tzinfo=timezone.utc)
    game = _make_active_game(white_id, black_id, start)
    await repo.create(game)

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: start)
    await service.make_move(MakeMoveCommand(game_id=game.id, user_id=white_id, uci="e2e4"))
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: start + timedelta(seconds=1))
    await service.make_move(MakeMoveCommand(game_id=game.id, user_id=black_id, uci="e7e5"))
    _set_disconnect_budgets(game, white_ms=0)

    disconnected_at = start + timedelta(seconds=2)
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: disconnected_at)
    disconnected_game = await service.mark_disconnected(white_id)

    assert disconnected_game is game
    _assert_disconnect_timeout(game, white_id, disconnected_at)
    assert game.disconnected_player_id is None
    assert game.disconnect_grace_deadline_at is None


@pytest.mark.asyncio
async def test_second_disconnect_during_active_grace_keeps_original_player_and_deadline(monkeypatch):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    start = datetime(2026, 4, 13, 15, 0, tzinfo=timezone.utc)
    game = _make_active_game(white_id, black_id, start)
    await repo.create(game)

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: start)
    await service.mark_disconnected(white_id)
    original_deadline = game.disconnect_grace_deadline_at

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: start + timedelta(seconds=5))
    await service.mark_disconnected(black_id)

    assert game.disconnected_player_id == white_id
    assert game.disconnect_grace_deadline_at == original_deadline

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: start + timedelta(seconds=6))
    await service.mark_disconnected(white_id)

    assert game.disconnected_player_id == white_id
    assert game.disconnect_grace_deadline_at == original_deadline

@pytest.mark.asyncio
async def test_clock_timeout_triggers_when_active_player_flags(monkeypatch):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    start = datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc)

    game = Game(
        white_id=white_id,
        black_id=black_id,
        initial_time_ms=1_000,
        white_time_ms=1_000,
        black_time_ms=1_000,
        last_clock_started_at=start,
        started_at=start,
    )
    await repo.create(game)

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: start + timedelta(seconds=2))
    finished_games = await service.monitor_active_games()

    assert len(finished_games) == 1
    assert finished_games[0].status == "timeout"
    assert finished_games[0].result == "0-1"
    assert finished_games[0].termination_reason == "clock_timeout"


@pytest.mark.asyncio
async def test_accept_draw_finalizes_game_through_application_service(monkeypatch):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    now = datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc)

    game = Game(
        white_id=white_id,
        black_id=black_id,
        white_time_ms=300_000,
        black_time_ms=300_000,
        last_clock_started_at=now - timedelta(seconds=3),
        started_at=now - timedelta(seconds=3),
    )
    await repo.create(game)
    game.pending_draw_offer_by_user_id = white_id
    game.pending_draw_offer_at = now

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: now)
    finished_game = await service.accept_draw(AcceptDrawCommand(game_id=game.id, user_id=black_id))

    assert finished_game.status == "draw"
    assert finished_game.result == "1/2-1/2"
    assert finished_game.white_time_ms == 297_000
    assert finished_game.black_time_ms == 300_000
    assert finished_game.termination_reason == "draw_agreement"
    assert finished_game.ended_at == now
    assert finished_game.last_clock_started_at is None


@pytest.mark.asyncio
async def test_accept_draw_without_pending_offer_errors_and_keeps_game_active(monkeypatch):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    now = datetime(2026, 4, 13, 12, 5, tzinfo=timezone.utc)
    game = Game(
        white_id=white_id,
        black_id=black_id,
        white_time_ms=300_000,
        black_time_ms=300_000,
        last_clock_started_at=now,
        started_at=now,
    )
    await repo.create(game)

    with pytest.raises(DrawOfferNotFound):
        await service.accept_draw(AcceptDrawCommand(game_id=game.id, user_id=black_id))

    assert game.status == "active"
    assert game.result is None
    assert game.termination_reason is None


@pytest.mark.asyncio
async def test_offer_draw_records_pending_offer_and_rejects_counter_offer(monkeypatch):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    now = datetime(2026, 4, 13, 12, 10, tzinfo=timezone.utc)
    game = Game(white_id=white_id, black_id=black_id, last_clock_started_at=now, started_at=now)
    await repo.create(game)

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: now)
    offer_draw = getattr(service, "offer_draw", None)
    assert offer_draw is not None
    offered_game = await offer_draw(OfferDrawCommand(game_id=game.id, user_id=white_id))

    assert offered_game.status == "active"
    assert offered_game.pending_draw_offer_by_user_id == white_id
    assert offered_game.pending_draw_offer_at == now

    with pytest.raises(DrawOfferPending):
        await offer_draw(OfferDrawCommand(game_id=game.id, user_id=black_id))
    assert offered_game.pending_draw_offer_by_user_id == white_id


@pytest.mark.asyncio
async def test_offer_draw_by_same_player_refreshes_existing_pending_offer(monkeypatch):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    first = datetime(2026, 4, 13, 12, 15, tzinfo=timezone.utc)
    second = first + timedelta(seconds=7)
    game = Game(white_id=white_id, black_id=black_id, last_clock_started_at=first, started_at=first)
    await repo.create(game)

    offer_draw = getattr(service, "offer_draw", None)
    assert offer_draw is not None
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: first)
    await offer_draw(OfferDrawCommand(game_id=game.id, user_id=white_id))
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: second)
    offered_game = await offer_draw(OfferDrawCommand(game_id=game.id, user_id=white_id))

    assert offered_game.status == "active"
    assert offered_game.pending_draw_offer_by_user_id == white_id
    assert offered_game.pending_draw_offer_at == second


@pytest.mark.asyncio
async def test_decline_draw_by_opponent_clears_pending_offer_and_returns_offerer(monkeypatch):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    offered_at = datetime(2026, 4, 13, 12, 20, tzinfo=timezone.utc)
    game = Game(white_id=white_id, black_id=black_id, last_clock_started_at=offered_at, started_at=offered_at)
    game.pending_draw_offer_by_user_id = white_id
    game.pending_draw_offer_at = offered_at
    await repo.create(game)

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: offered_at)
    decline_draw = getattr(service, "decline_draw", None)
    assert decline_draw is not None
    result = await decline_draw(DeclineDrawCommand(game_id=game.id, user_id=black_id))
    declined_game, offerer_id = result if isinstance(result, tuple) else (result, None)

    assert declined_game.status == "active"
    assert offerer_id == white_id
    assert declined_game.pending_draw_offer_by_user_id is None
    assert declined_game.pending_draw_offer_at is None


@pytest.mark.asyncio
async def test_black_disconnect_grace_budget_persists_without_changing_white_budget(
    monkeypatch,
):
    repo = InMemoryGameRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    start = datetime(2026, 4, 13, 12, 45, tzinfo=timezone.utc)
    game = _make_active_game(white_id, black_id, start)
    _set_disconnect_budgets(game, white_ms=20_000, black_ms=12_000)
    await repo.create(game)

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: start)
    disconnected_game = await service.mark_disconnected(black_id)
    assert disconnected_game is game
    assert game.disconnect_grace_deadline_at == start + timedelta(seconds=12)

    reconnected_at = start + timedelta(seconds=5)
    monkeypatch.setattr(
        "domains.game.application.services.utc_now", lambda: reconnected_at
    )
    reconnected_game = await service.mark_reconnected(black_id)

    assert reconnected_game is game
    assert game.status == "active"
    assert game.disconnected_player_id is None
    assert game.disconnect_grace_deadline_at is None
    assert _remaining_disconnect_budget_ms(game, white_id) == 20_000
    assert _remaining_disconnect_budget_ms(game, black_id) == 7_000

    second_disconnect_at = reconnected_at + timedelta(seconds=2)
    monkeypatch.setattr(
        "domains.game.application.services.utc_now", lambda: second_disconnect_at
    )
    await service.mark_disconnected(black_id)

    assert game.disconnected_player_id == black_id
    assert game.disconnect_grace_deadline_at == second_disconnect_at + timedelta(
        milliseconds=7_000
    )
    assert _remaining_disconnect_budget_ms(game, white_id) == 20_000

@pytest.mark.asyncio
async def test_monitor_does_not_lock_active_game_that_is_not_due(monkeypatch):
    class LockTrackingRepository(InMemoryGameRepository):
        def __init__(self) -> None:
            super().__init__()
            self.locked_game_ids = []

        async def get_by_id_for_update(self, game_id):
            self.locked_game_ids.append(game_id)
            return await super().get_by_id_for_update(game_id)

    repo = LockTrackingRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    now = datetime(2026, 4, 13, 12, 1, tzinfo=timezone.utc)
    game = _make_active_game(white_id, black_id, now)
    await repo.create(game)

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: now)
    finished_games = await service.monitor_active_games()

    assert finished_games == []
    assert repo.locked_game_ids == []
    assert game.status == "active"

@pytest.mark.asyncio
async def test_disconnect_finalization_uses_move_counts_without_loading_full_moves(
    monkeypatch,
):
    class MoveCountOnlyRepository(InMemoryGameRepository):
        def __init__(self) -> None:
            super().__init__()
            self.move_count_requests = []

        async def get_moves(self, game_id):
            raise AssertionError("disconnect finalization should not load full moves")

        async def get_move_counts(self, game_ids):
            self.move_count_requests.append(tuple(game_ids))
            return {game_id: 2 for game_id in game_ids}

    repo = MoveCountOnlyRepository()
    service = GameService(repo)
    white_id = uuid4()
    black_id = uuid4()
    start = datetime(2026, 4, 13, 12, 2, tzinfo=timezone.utc)
    game = _make_active_game(white_id, black_id, start)
    await repo.create(game)

    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: start)
    await service.mark_disconnected(white_id)

    deadline = start + timedelta(seconds=20)
    monkeypatch.setattr("domains.game.application.services.utc_now", lambda: deadline)
    finished_games = await service.monitor_active_games()

    assert repo.move_count_requests == [(game.id,)]
    assert len(finished_games) == 1
    _assert_disconnect_timeout(finished_games[0], white_id, deadline)
