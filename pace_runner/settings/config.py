from dynaconf import Dynaconf
from datetime import datetime
import os, sys, pytz
from pathlib import Path


_NOW = datetime.now(pytz.timezone("Asia/Kolkata"))
_BASE_DIR = Path(__file__).resolve().parent.parent


def _get_start_ts(tz: str) -> datetime:
    return _NOW.astimezone(pytz.timezone(tz))


def _get_now(tz: str) -> str:
    return datetime.now().astimezone(pytz.timezone(tz)).isoformat()


###################
# Create Settings #
###################
_USER_TOML_PATH = os.environ.get("PACE_RUNNER_SETTINGS_TOML")

config = Dynaconf(
    preload=[_BASE_DIR.joinpath("settings", "settings.toml").as_posix()],
    settings_files=[_USER_TOML_PATH] if _USER_TOML_PATH else [],
    # to enable overriding of single variables at runtime
    environments=True,
    envvar_prefix="PACE_RUNNER",
    # to enable merging of user defined and base settings
    load_dotenv=True,
    # jinja variables
    _get_now=_get_now,
    _get_start_ts=_get_start_ts,
    now=_NOW,
    partition_date=_NOW.strftime("%Y/%m/%d"),
    root_dir=_BASE_DIR.as_posix(),
    merge_enabled=True,
)
