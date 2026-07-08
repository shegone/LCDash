from dataclasses import dataclass
from dotenv import load_dotenv
import os


load_dotenv()


@dataclass
class Settings:
    token_url: str = os.getenv("CENTRALSQUARE_TOKEN_URL", "")
    cad_base_url: str = os.getenv("CENTRALSQUARE_CAD_BASE_URL", "")
    system_base_url: str = os.getenv("CENTRALSQUARE_SYSTEM_BASE_URL", "")
    username: str = os.getenv("CENTRALSQUARE_USERNAME", "")
    password: str = os.getenv("CENTRALSQUARE_PASSWORD", "")
    from_header: str = os.getenv("CENTRALSQUARE_FROM_HEADER", "LCDash")
    debug: bool = os.getenv("LCDASH_DEBUG", "true").lower() == "true"


settings = Settings()