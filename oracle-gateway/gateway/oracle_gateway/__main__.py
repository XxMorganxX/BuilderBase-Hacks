"""Run the gateway directly: python -m oracle_gateway

Honours ORACLE_GATEWAY_PORT and ORACLE_LOG_LEVEL. The container image calls
uvicorn itself on a fixed internal port; this is for running on a host.
"""

from __future__ import annotations

import uvicorn

from .config import SETTINGS


def main() -> None:
    uvicorn.run(
        "oracle_gateway.main:app",
        host="0.0.0.0",
        port=SETTINGS.port,
        log_level=SETTINGS.log_level,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
