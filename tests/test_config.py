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


def test_repr_does_not_leak_secrets(tmp_path):
    settings = Settings.load(root=tmp_path, environ={"REPLICATE_API_TOKEN": "secret-value"})
    assert "secret-value" not in repr(settings)


def test_require_wavespeed_names_where_to_get_it(tmp_path):
    settings = Settings.load(root=tmp_path, environ={})
    with pytest.raises(MissingSettingError) as caught:
        settings.require("WAVESPEED_API_KEY")
    assert str(caught.value) == (
        "WAVESPEED_API_KEY is not set. Add it to .env; get it at https://wavespeed.ai → "
        "Dashboard → API Keys. Already use PiAPI? Prepare with --provider piapi instead."
    )


@pytest.mark.parametrize(
    "name", ["REPLICATE_API_TOKEN", "WAVESPEED_API_KEY", "PIAPI_API_KEY", "HF_TOKEN", "OTHER"]
)
def test_missing_key_messages_have_no_parentheses(tmp_path, name):
    with pytest.raises(MissingSettingError) as caught:
        Settings.load(root=tmp_path, environ={}).require(name)
    text = str(caught.value)
    assert "(" not in text and text.startswith(f"{name} is not set. Add it to .env; get it at ")


def test_require_piapi_mentions_the_creator_plan(tmp_path):
    with pytest.raises(MissingSettingError) as caught:
        Settings.load(root=tmp_path, environ={}).require("PIAPI_API_KEY")
    assert "https://piapi.ai → Workspace → API Keys. Saved faces need the Creator plan." in str(
        caught.value
    )
