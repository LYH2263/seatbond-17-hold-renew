from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    database_url: str = "postgresql+psycopg2://seatbond:seatbond@localhost:5442/seatbond"
    seed_on_empty: bool = True
    hold_ttl_minutes: int = 30  # initial lifetime of a new hold
    renew_extension_minutes: int = 30  # extra time granted per renewal
    max_renewals: int = 2  # renewal quota per hold


settings = Settings()
