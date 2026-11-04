import pytest

from gulliver.config import ConfigError, connection_from_dsn, load_settings


def test_dsn_parsing():
    assert connection_from_dsn("data.duckdb").params == {"path": "data.duckdb"}
    assert connection_from_dsn("duckdb:///tmp/x.duckdb").params["path"] == "/tmp/x.duckdb"
    assert connection_from_dsn(":memory:").params == {"path": ":memory:"}
    pg = connection_from_dsn("postgresql://me:secret@db:5432/app")
    assert pg.engine == "postgres"
    assert "secret" not in str(pg.public_dict())
    bq = connection_from_dsn("bigquery://my-proj?location=EU")
    assert bq.engine == "bigquery" and bq.params == {"project": "my-proj", "location": "EU"}
    sf = connection_from_dsn("snowflake://acme-xy12?warehouse=WH&user=ME")
    assert sf.engine == "snowflake" and sf.params["warehouse"] == "WH"
    with pytest.raises(ConfigError):
        connection_from_dsn("mysql://x")


def test_toml_connections_and_budget(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GULLIVER_DSN", raising=False)
    (tmp_path / "gulliver.toml").write_text(
        'default_connection = "bq"\n'
        "[connections.local]\nengine = \"duckdb\"\npath = \"x.duckdb\"\n"
        "[connections.bq]\ndsn = \"bigquery://proj\"\nprice_per_tib_usd = 5.0\n"
        "[budget]\nmax_query_usd = 2.5\n"
    )
    monkeypatch.setenv("GULLIVER_DAY_USD", "40")
    s = load_settings()
    assert s.default_connection == "bq"
    assert s.connection().params["price_per_tib_usd"] == 5.0
    assert s.connection("local").engine == "duckdb"
    assert s.budget.max_query_usd == 2.5
    assert s.budget.day_usd == 40.0
    with pytest.raises(ConfigError):
        s.connection("nope")


def test_unknown_budget_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "gulliver.toml").write_text("[budget]\nmax_qeury_usd = 1\n")
    with pytest.raises(ConfigError):
        load_settings()


def test_default_is_in_memory_duckdb(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GULLIVER_DSN", raising=False)
    monkeypatch.delenv("GULLIVER_CONFIG", raising=False)
    s = load_settings()
    assert s.connection().engine == "duckdb"
    assert s.connection().params["path"] == ":memory:"


def test_example_config_loads(monkeypatch):
    from pathlib import Path

    example = Path(__file__).resolve().parent.parent / "gulliver.example.toml"
    monkeypatch.delenv("GULLIVER_DSN", raising=False)
    s = load_settings(config_path=example)
    assert set(s.connections) == {"local", "bq", "sf", "pg"}
    assert s.connection("bq").engine == "bigquery"
    assert s.connection("pg").engine == "postgres"
    assert s.budget.max_query_usd == 1.0


def _write_budget(tmp_path, body):
    (tmp_path / "gulliver.toml").write_text("[budget]\n" + body)


def test_non_finite_budget_from_toml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for body in ("max_query_usd = nan\n", "day_usd = inf\n", "max_bytes = inf\n", "max_runtime_s = -inf\n"):
        _write_budget(tmp_path, body)
        with pytest.raises(ConfigError) as excinfo:
            load_settings()
        key = body.split(" = ")[0]
        assert key in str(excinfo.value)


def test_non_finite_budget_from_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for env_key, key, value in (("GULLIVER_MAX_QUERY_USD", "max_query_usd", "nan"),
                                ("GULLIVER_DAY_USD", "day_usd", "inf"),
                                ("GULLIVER_MAX_BYTES", "max_bytes", "-inf"),
                                ("GULLIVER_REPEAT_WINDOW_MIN", "repeat_window_min", "nan")):
        monkeypatch.setenv(env_key, value)
        with pytest.raises(ConfigError) as excinfo:
            load_settings()
        assert key in str(excinfo.value)
        monkeypatch.delenv(env_key)


def test_negative_budget_rejected_and_zero_allowed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for body in ("max_query_usd = -1\n", "max_output_rows = -1\n", "agent_day_usd = -5\n"):
        _write_budget(tmp_path, body)
        with pytest.raises(ConfigError) as excinfo:
            load_settings()
        assert body.split(" = ")[0] in str(excinfo.value)

    _write_budget(tmp_path, "max_output_rows = 0\nsession_usd = 0\nagent_day_usd = 0\n")
    s = load_settings()
    assert s.budget.max_output_rows == 0
    assert s.budget.session_usd == 0
    assert s.budget.agent_day_usd == {"default": 0}


def test_non_finite_agent_day_usd_table(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for body in ('agent_day_usd = {default = nan}\n', 'agent_day_usd = {default = 5, "claude-code" = -1}\n'):
        _write_budget(tmp_path, body)
        with pytest.raises(ConfigError) as excinfo:
            load_settings()
        assert "agent_day_usd" in str(excinfo.value)
