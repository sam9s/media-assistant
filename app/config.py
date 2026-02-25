from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # qBittorrent
    QB_URL: str = "https://downloads.sam9scloud.in"
    QB_USERNAME: str
    QB_PASSWORD: str

    # Jackett — tracker search proxy (replaces direct PrivateHD RSS)
    JACKETT_URL: str = "http://jackett:9117"
    JACKETT_API_KEY: str = ""   # copy from Jackett dashboard after first run

    # Jellyfin
    JELLYFIN_URL: str = "https://movies.sam9scloud.in"
    JELLYFIN_API_KEY: str = "d5c97c8f30f1418a9573f8806b8ea334"

    # iptorrents — full RSS base URL (without the q= search param)
    IPTORRENTS_RSS_BASE_URL: str

    # PrivateHD — RSS passkey/PID (find at privatehd.to → profile → RSS or passkey section)
    PRIVATEHD_PID: str = ""   # leave blank to disable PrivateHD search

    # TMDB — for movie metadata, poster art, IMDb links
    TMDB_API_KEY: str = "0022c77a66930474249f273d4d79457b"

    # API Security — secret key OpenClaw must send in X-API-Key header
    API_KEY: str

    class Config:
        env_file = ".env"


settings = Settings()
