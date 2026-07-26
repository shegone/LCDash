import os
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import settings
from scripts.index_knowledge import run_index


def main():
    interval = max(
        int(
            os.getenv(
                "MINDSHARE_KNOWLEDGE_INDEX_INTERVAL_SECONDS",
                "3600",
            )
        ),
        300,
    )
    while True:
        try:
            result = run_index(
                source_dir=settings.mindshare_knowledge_source_dir,
                library_key="mindshare",
            )
            print({"mindshare_knowledge_index": result}, flush=True)
        except Exception as exc:
            print({"mindshare_knowledge_index_error": str(exc)}, flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
