import pytest
from pydantic import ValidationError

from app.config import Settings
from tests.conftest import make_settings


def test_startup_fails_when_a_required_variable_is_missing():
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, devin_api_key="k", devin_org_id="org-x", github_token="t")
    assert "missing required settings: GITHUB_WEBHOOK_SECRET, TARGET_REPO" in str(excinfo.value)


def test_credentials_are_optional_in_simulation_mode():
    settings = Settings(_env_file=None, devin_simulate=True)
    assert settings.devin_api_key == ""
    assert settings.github_webhook_secret == "simulate"
    assert settings.target_repo == "simulated/superset"


def test_simulation_mode_keeps_values_that_are_provided():
    settings = Settings(
        _env_file=None, devin_simulate=True, github_webhook_secret="s", target_repo="me/superset"
    )
    assert (settings.github_webhook_secret, settings.target_repo) == ("s", "me/superset")


def test_optional_settings_have_defaults(tmp_path):
    settings = make_settings(tmp_path)
    assert settings.trigger_label == "tech-debt"
    assert settings.poll_interval_seconds == 30
    assert settings.run_timeout_minutes == 60
    assert settings.devin_max_acu_limit == 10
    assert settings.slack_webhook_url is None
    assert settings.devin_simulate is False
    assert settings.sync_on_startup is True
    assert settings.sync_max_runs == 5
    assert settings.devin_api_base == "https://api.devin.ai/v3"


def test_settings_are_read_from_the_environment(monkeypatch):
    for name, value in {
        "DEVIN_API_KEY": "k",
        "DEVIN_ORG_ID": "org-x",
        "GITHUB_TOKEN": "t",
        "GITHUB_WEBHOOK_SECRET": "s",
        "TARGET_REPO": "me/superset",
        "RUN_TIMEOUT_MINUTES": "15",
    }.items():
        monkeypatch.setenv(name, value)
    settings = Settings(_env_file=None)
    assert settings.target_repo == "me/superset"
    assert settings.run_timeout_minutes == 15
