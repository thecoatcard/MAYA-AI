import argparse
from pathlib import Path

import uvicorn
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local MAYA API")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--port", type=int, default=8000, choices=range(1, 65536), metavar="PORT")
    arguments = parser.parse_args()
    if arguments.env_file is not None and not arguments.env_file.is_file():
        parser.error("The requested environment file does not exist")
    try:
        settings = Settings(_env_file=arguments.env_file)
    except ValidationError:
        parser.exit(2, "Invalid MAYA configuration; check required variables and URL formats.\n")
    # Forwarded-for headers are not trusted: the per-address rate limits key on the direct
    # peer, and a client-chosen header would let anyone rotate their identity.
    uvicorn.run(
        create_app(settings),
        host="127.0.0.1",
        port=arguments.port,
        access_log=False,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
