import pytest

from character_swap.config import MissingSettingError, Settings, read_env_file


def test_reads_env_file_ignoring_comments_and_quotes(tmp_path):
    (tmp_path / ".env").write_text("# comment\nA=1\nB=\"two\"\n\nC='three'\nBROKEN\n")
    assert read_env_file(tmp_path / ".env") == {"A": "1", "B": "two", "C": "three"}


def test_missing_env_file_is_empty(tmp_path):
    assert read_env_file(tmp_path / ".env") == {}


def test_environment_overrides_env_file(tmp_path):
    (tmp_path / ".env").write_text("REPLICATE_API_TOKEN=from-file\n")
    settings = Settings.load(root=tmp_path, environ={"REPLICATE_API_TOKEN": "from-env"})
    assert settings.require("REPLICATE_API_TOKEN") == "from-env"


def test_require_missing_names_variable_and_where_to_get_it(tmp_path):
    settings = Settings.load(root=tmp_path, environ={})
    with pytest.raises(MissingSettingError, match="REPLICATE_API_TOKEN.*replicate.com"):
        settings.require("REPLICATE_API_TOKEN")


def test_runs_dir_defaults_to_project_runs_and_can_be_overridden(tmp_path):
    assert Settings.load(root=tmp_path, environ={}).runs_dir == tmp_path / "runs"
    custom = Settings.load(root=tmp_path, environ={"CHARSWAP_RUNS_DIR": str(tmp_path / "x")})
    assert custom.runs_dir == tmp_path / "x"


def test_hosting_mode_is_r2_only_when_all_r2_keys_present(tmp_path):
    keys = {"R2_ACCOUNT_ID": "a", "R2_ACCESS_KEY_ID": "b", "R2_SECRET_ACCESS_KEY": "c"}
    assert Settings.load(root=tmp_path, environ=keys).hosting_mode == "tmpfiles"
    keys["R2_BUCKET"] = "d"
    assert Settings.load(root=tmp_path, environ=keys).hosting_mode == "r2"


def test_repr_does_not_leak_secrets(tmp_path):
    settings = Settings.load(root=tmp_path, environ={"REPLICATE_API_TOKEN": "secret-value"})
    assert "secret-value" not in repr(settings)
