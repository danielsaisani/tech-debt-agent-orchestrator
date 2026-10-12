"""Service settings, read from the environment (and a local .env when present)."""

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REQUIRED = ("devin_api_key", "devin_org_id", "github_token", "github_webhook_secret", "target_repo")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Required unless devin_simulate is on: the service refuses to start without these.
    devin_api_key: str = ""
    devin_org_id: str = ""
    github_token: str = ""
    github_webhook_secret: str = ""
    target_repo: str = ""  # "owner/name" of the fork Devin works on

    trigger_label: str = "tech-debt"
    poll_interval_seconds: float = 30
    run_timeout_minutes: float = 60
    devin_max_acu_limit: int = 10
    database_path: str = "data/runs.db"
    slack_webhook_url: str | None = None

    # Reconciliation sweep for labeled issues that no webhook reported.
    sync_on_startup: bool = True
    sync_max_runs: int = 5

    # Simulation mode: in-process fakes instead of Devin, GitHub and Slack.
    devin_simulate: bool = False
    devin_simulate_polls: int = 2

    devin_api_base: str = "https://api.devin.ai/v3"
    github_api_base: str = "https://api.github.com"

    @model_validator(mode="after")
    def _require_credentials(self):
        if self.devin_simulate:
            self.github_webhook_secret = self.github_webhook_secret or "simulate"
            self.target_repo = self.target_repo or "simulated/superset"
            return self
        missing = [name.upper() for name in REQUIRED if not getattr(self, name)]
        if missing:
            raise ValueError("missing required settings: " + ", ".join(missing))
        return self
