from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # qBittorrent
    QB_URL: str = "https://downloads.sam9scloud.in"
    QB_USERNAME: str
    QB_PASSWORD: str

    # Jackett â€” tracker search proxy (replaces direct PrivateHD RSS)
    JACKETT_URL: str = "http://jackett:9117"
    JACKETT_API_KEY: str = ""   # copy from Jackett dashboard after first run

    # Jellyfin
    JELLYFIN_URL: str = "https://movies.sam9scloud.in"
    JELLYFIN_API_KEY: str = "d5c97c8f30f1418a9573f8806b8ea334"

    # iptorrents â€” full RSS base URL (without the q= search param)
    IPTORRENTS_RSS_BASE_URL: str

    # PrivateHD â€” RSS passkey/PID (find at privatehd.to â†’ profile â†’ RSS or passkey section)
    PRIVATEHD_PID: str = ""   # leave blank to disable PrivateHD search

    # TMDB â€” for movie metadata, poster art, IMDb links
    TMDB_API_KEY: str = "0022c77a66930474249f273d4d79457b"

    # API Security â€” secret key OpenClaw must send in X-API-Key header
    API_KEY: str

    # Kavita â€” ebook library manager
    # Use 172.17.0.1 (Docker bridge host IP) so the sam-media-api container
    # can reach Kavita on the host port â€” same pattern as qBittorrent webhook.
    KAVITA_URL: str = "http://172.17.0.1:8091"
    KAVITA_USERNAME: str = ""
    KAVITA_PASSWORD: str = ""

    # Anna's Archive â€” optional DDoS-Guard session cookie for slow_download access.
    # Leave blank until you have a verified browser session from annas-archive.gl.
    # Set to the full Cookie header value from a logged-in browser session.
    ANNA_ARCHIVE_COOKIE: str = ""

    # OpenSubtitles - automatic subtitle download for completed videos
    OPENSUBTITLES_API_KEY: str = ""
    OPENSUBTITLES_USERNAME: str = ""
    OPENSUBTITLES_PASSWORD: str = ""
    OPENSUBTITLES_LANGUAGES: str = "en"
    OPENSUBTITLES_PREFER_SDH: bool = False
    OPENSUBTITLES_AUTO_DOWNLOAD: bool = True
    OPENSUBTITLES_PROXY_URL: str = "http://172.17.0.1:8876"

    # SubDL - simpler manual subtitle search source
    SUBDL_API_KEY: str = ""
    SUBDL_LANGUAGES: str = "EN"

    class Config:
        env_file = ".env"


settings = Settings()


