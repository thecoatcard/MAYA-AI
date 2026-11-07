import argparse
from pathlib import Path

import uvicorn
from pydantic import ValidationError

from app.agents.checkpointer import ensure_compatible_event_loop
from app.core.config import Settings
from app.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local MAYA API")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--port", type=int, default=8000, choices=range(1, 65536), metavar="PORT")
    arguments = parser.parse_args()
    env_files: list[Path] = []
    if arguments.env_file is not None:
        if not arguments.env_file.is_file():
            parser.error("The requested environment file does not exist")
        env_files.append(arguments.env_file)
        # If the requested file is an example template (.env.example),
        # overlay private .env if present.
        if arguments.env_file.name.endswith(".example"):
            private_env = arguments.env_file.with_name(
                arguments.env_file.name.removesuffix(".example")
            )
            if private_env.is_file() and private_env not in env_files:
                env_files.append(private_env)
    else:
        for candidate in (Path(".env"), Path("../.env")):
            if candidate.is_file():
                env_files.append(candidate)
                break

    try:
        settings = Settings(_env_file=env_files if env_files else None)
    except ValidationError:
        parser.exit(2, "Invalid MAYA configuration; check required variables and URL formats.\n")
    ensure_compatible_event_loop()
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
