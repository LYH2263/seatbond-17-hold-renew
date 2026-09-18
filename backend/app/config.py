from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    database_url: str = "postgresql+psycopg2://seatbond:seatbond@localhost:5442/seatbond"
    seed_on_empty: bool = True
    # 持座时效：创建后多少秒到期，以及每条记录最多可续期次数
    hold_ttl_seconds: int = 120
    max_renewals: int = 2


settings = Settings()
