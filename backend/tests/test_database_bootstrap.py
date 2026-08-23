import pytest
from app.config import Settings

from domains.admin.infrastructure import seed as admin_seed
from infrastructure.database_bootstrap import initialize_database
from infrastructure.database_migrations import ALEMBIC_SCRIPT_PATH, HEAD_REVISION, to_migration_database_url


MAX_ALEMBIC_VERSION_LENGTH = 32
GAME_STATE_INTEGRITY_REVISION = "0012_game_state_integrity"
PREVIOUS_HEAD_REVISION = "0011_game_player_history_indexes"


def _game_state_integrity_migration_file():
    return ALEMBIC_SCRIPT_PATH / "versions" / f"{GAME_STATE_INTEGRITY_REVISION}.py"


@pytest.mark.asyncio
async def test_initialize_database_runs_migrations_before_seeding(monkeypatch):
    calls: list[object] = []
    engine = object()

    def fake_register_models() -> None:
        calls.append("register")

    async def fake_run_database_migrations() -> None:
        calls.append("migrate")

    async def fake_seed_starter_puzzles(received_engine) -> None:
        calls.append(("seed", received_engine))

    async def fake_seed_first_admin(received_engine) -> None:
        calls.append(("admin", received_engine))

    async def fake_seed_demo_tournaments(received_engine) -> None:
        calls.append(("demo", received_engine))

    async def fake_seed_default_shop_items(received_engine) -> None:
        calls.append(("shop", received_engine))

    monkeypatch.setattr("infrastructure.database_bootstrap.register_models", fake_register_models)
    monkeypatch.setattr("infrastructure.database_bootstrap.run_database_migrations", fake_run_database_migrations)
    monkeypatch.setattr("infrastructure.database_bootstrap.seed_starter_puzzles", fake_seed_starter_puzzles)
    monkeypatch.setattr("infrastructure.database_bootstrap.seed_default_shop_items", fake_seed_default_shop_items)
    monkeypatch.setattr("infrastructure.database_bootstrap.seed_demo_tournaments", fake_seed_demo_tournaments)
    monkeypatch.setattr("infrastructure.database_bootstrap.seed_first_admin", fake_seed_first_admin)

    await initialize_database(engine)

    assert calls == ["register", "migrate", ("seed", engine), ("shop", engine), ("demo", engine), ("admin", engine)]


def test_to_migration_database_url_rewrites_asyncpg_for_alembic():
    assert (
        to_migration_database_url("postgresql+asyncpg://user:password@db.example.invalid:5432/app")
        == "postgresql+psycopg://user:password@db.example.invalid:5432/app"
    )


def test_alembic_revision_ids_fit_version_table():
    assert len(HEAD_REVISION) <= MAX_ALEMBIC_VERSION_LENGTH

    for migration_file in (ALEMBIC_SCRIPT_PATH / "versions").glob("*.py"):
        namespace: dict[str, object] = {}
        exec(migration_file.read_text(encoding="utf-8"), namespace)
        revision = namespace.get("revision")
        assert isinstance(revision, str)
        assert len(revision) <= MAX_ALEMBIC_VERSION_LENGTH, migration_file.name


def test_game_state_integrity_revision_is_current_head():
    assert HEAD_REVISION == GAME_STATE_INTEGRITY_REVISION


def test_game_state_integrity_migration_file_exists():
    migration_file = _game_state_integrity_migration_file()

    assert migration_file.exists(), f"{migration_file.name} is missing"

    namespace: dict[str, object] = {}
    exec(migration_file.read_text(encoding="utf-8"), namespace)

    assert namespace.get("revision") == GAME_STATE_INTEGRITY_REVISION
    assert namespace.get("down_revision") == PREVIOUS_HEAD_REVISION


@pytest.mark.asyncio
async def test_seed_first_admin_creates_predictable_local_admin(monkeypatch):
    added_users = []

    class FakeResult:
        def scalar_one_or_none(self):
            return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _statement):
            return FakeResult()

        def add(self, user):
            added_users.append(user)

        async def commit(self):
            pass

        async def rollback(self):
            raise AssertionError("seeding the first admin either commits or raises, it never rolls back")

    def fake_sessionmaker(_engine, expire_on_commit=False):
        assert expire_on_commit is False
        return lambda: FakeSession()

    monkeypatch.setattr(admin_seed, "async_sessionmaker", fake_sessionmaker)
    monkeypatch.setattr(admin_seed.settings, "SEED_ADMIN_EMAIL", "admin@chessview.app")
    monkeypatch.setattr(admin_seed.settings, "SEED_ADMIN_USERNAME", "admin")
    monkeypatch.setattr(admin_seed.settings, "SEED_ADMIN_PASSWORD", "admin123")
    monkeypatch.setattr(admin_seed, "hash_password", lambda password: f"hashed:{password}")

    await admin_seed.seed_first_admin(object())

    assert len(added_users) == 1
    assert added_users[0].email == "admin@chessview.app"
    assert added_users[0].username == "admin"
    assert added_users[0].password == "hashed:admin123"
    assert added_users[0].role == "admin"


@pytest.mark.asyncio
async def test_seed_first_admin_skips_when_seed_password_uses_safe_default(monkeypatch):
    added_users = []
    commits = []

    class FakeResult:
        def scalar_one_or_none(self):
            return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute(self, _statement):
            return FakeResult()

        def add(self, user):
            added_users.append(user)

        async def commit(self):
            commits.append("commit")

    def fake_sessionmaker(_engine, expire_on_commit=False):
        assert expire_on_commit is False
        return lambda: FakeSession()

    default_password = Settings.model_fields["SEED_ADMIN_PASSWORD"].default
    assert default_password == ""

    monkeypatch.setattr(admin_seed, "async_sessionmaker", fake_sessionmaker)
    monkeypatch.setattr(admin_seed.settings, "FIRST_ADMIN_EMAIL", None)
    monkeypatch.setattr(admin_seed.settings, "SEED_ADMIN_EMAIL", "admin@chessview.app")
    monkeypatch.setattr(admin_seed.settings, "SEED_ADMIN_USERNAME", "admin")
    monkeypatch.setattr(admin_seed.settings, "SEED_ADMIN_PASSWORD", default_password)

    await admin_seed.seed_first_admin(object())

    assert added_users == []
    assert commits == []


class _AlembicResult:
    def __init__(self, first_result=None) -> None:
        self._first_result = first_result

    def first(self):
        return self._first_result


class _AlembicConnection:
    def __init__(self, first_result=None) -> None:
        self.executed = []
        self.first_result = first_result

    def execute(self, statement):
        self.executed.append(statement)
        return _AlembicResult(self.first_result)


class _AlembicOpRecorder:
    def __init__(self, first_result=None) -> None:
        self.indexes: dict[tuple[str, str], tuple[str, ...]] = {}
        self.columns: dict[tuple[str, str], object] = {}
        self.foreign_keys: dict[tuple[str, str], tuple[str, ...]] = {}
        self.unique_constraints: dict[tuple[str, str], tuple[str, ...]] = {}
        self.connection = _AlembicConnection(first_result)

    def get_bind(self):
        return self.connection

    def add_column(self, table_name, column):
        self.columns[(table_name, column.name)] = column

    def create_foreign_key(self, name, source_table, referent_table, local_cols, remote_cols, **_kwargs):
        self.foreign_keys[(source_table, name)] = (
            referent_table,
            tuple(local_cols),
            tuple(remote_cols),
        )

    def create_unique_constraint(self, name, table_name, columns, **_kwargs):
        self.unique_constraints[(table_name, name)] = tuple(columns)

    def create_index(self, name, table_name, columns, **_kwargs):
        self.indexes[(table_name, name)] = tuple(str(column) for column in columns)

    def __getattr__(self, _name):
        def _noop(*_args, **_kwargs):
            return None

        return _noop


def test_games_by_player_indexes_are_declared_in_migrations():
    recorder = _AlembicOpRecorder()

    for migration_file in sorted((ALEMBIC_SCRIPT_PATH / "versions").glob("*.py")):
        namespace: dict[str, object] = {}
        exec(migration_file.read_text(encoding="utf-8"), namespace)
        namespace["op"] = recorder
        namespace["upgrade"]()

    assert recorder.indexes[("games", "ix_games_white_id_started_at_desc")] == (
        "white_id",
        "started_at DESC",
    )
    assert recorder.indexes[("games", "ix_games_black_id_started_at_desc")] == (
        "black_id",
        "started_at DESC",
    )


def test_game_state_integrity_migration_declares_columns_and_constraints():
    recorder = _AlembicOpRecorder()
    migration_file = ALEMBIC_SCRIPT_PATH / "versions" / f"{GAME_STATE_INTEGRITY_REVISION}.py"
    namespace: dict[str, object] = {}
    exec(migration_file.read_text(encoding="utf-8"), namespace)
    namespace["op"] = recorder

    namespace["upgrade"]()

    assert ("games", "pending_draw_offer_by_user_id") in recorder.columns
    assert ("games", "pending_draw_offer_at") in recorder.columns
    assert ("games", "white_disconnect_grace_remaining_ms") in recorder.columns
    assert ("games", "black_disconnect_grace_remaining_ms") in recorder.columns
    assert recorder.columns[("games", "pending_draw_offer_by_user_id")].nullable is True
    assert recorder.columns[("games", "pending_draw_offer_at")].nullable is True
    assert recorder.columns[("games", "white_disconnect_grace_remaining_ms")].nullable is False
    assert recorder.columns[("games", "black_disconnect_grace_remaining_ms")].nullable is False
    assert str(recorder.columns[("games", "white_disconnect_grace_remaining_ms")].server_default.arg) == "20000"
    assert str(recorder.columns[("games", "black_disconnect_grace_remaining_ms")].server_default.arg) == "20000"
    assert recorder.foreign_keys[("games", "fk_games_pending_draw_offer_by_user_id_users")] == (
        "users",
        ("pending_draw_offer_by_user_id",),
        ("id",),
    )
    assert recorder.unique_constraints[("moves", "uq_moves_game_move_number")] == ("game_id", "move_number")
    assert recorder.connection.executed
    duplicate_guard_sql = str(recorder.connection.executed[0]).lower()
    assert "group by" in duplicate_guard_sql
    assert "having" in duplicate_guard_sql
