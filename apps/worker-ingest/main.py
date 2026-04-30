import os
import time


def main() -> None:
    """Placeholder ingest worker entrypoint."""
    app_env = os.getenv("APP_ENV", "development")
    print(f"worker-ingest started in {app_env} mode")
    print("ingest pipeline placeholder is idle")

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
