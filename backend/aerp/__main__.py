from __future__ import annotations

import uvicorn

from aerp.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "aerp.api.main:app",
        host=settings.aerp_host,
        port=settings.aerp_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
