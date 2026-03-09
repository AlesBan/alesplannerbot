from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Life AI Assistant"
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_BASE_URL")
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")
    database_url: str = Field(default="sqlite:///./life_assistant.db", alias="DATABASE_URL")
    timezone: str = Field(default="UTC", alias="TIMEZONE")

    yougile_api_key: str = Field(default="", alias="YOU_GILE_API_KEY")
    yougile_base_url: str = Field(default="https://api.yougile.com", alias="YOU_GILE_BASE_URL")
    yougile_email: str = Field(default="", alias="YOU_GILE_EMAIL")
    yougile_password: str = Field(default="", alias="YOU_GILE_PASSWORD")

    google_calendar_id: str = Field(default="primary", alias="GOOGLE_CALENDAR_ID")
    google_credentials_path: str = Field(default="./credentials/google-credentials.json", alias="GOOGLE_CREDENTIALS_PATH")
    google_credentials_json: str = Field(default="", alias="GOOGLE_CREDENTIALS_JSON")
    google_token_path: str = Field(default="./credentials/google-token.json", alias="GOOGLE_TOKEN_PATH")
    google_scopes: str = Field(default="https://www.googleapis.com/auth/calendar", alias="GOOGLE_SCOPES")

    morning_plan_hour: int = Field(default=8, alias="MORNING_PLAN_HOUR")
    evening_review_hour: int = Field(default=21, alias="EVENING_REVIEW_HOUR")
    max_daily_work_minutes: int = Field(default=480, alias="MAX_DAILY_WORK_MINUTES")


@lru_cache
def get_settings() -> Settings:
    return Settings()
