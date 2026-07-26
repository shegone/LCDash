import os
import time

from scripts.index_knowledge import run_index


def main():
    interval = max(int(os.getenv("KNOWLEDGE_INDEX_INTERVAL_SECONDS", "3600")), 300)
    while True:
        try:
            print({"knowledge_index": run_index()}, flush=True)
        except Exception as exc:
            print({"knowledge_index_error": str(exc)}, flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
