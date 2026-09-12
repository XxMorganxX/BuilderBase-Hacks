import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from .protocol import Model


class BridgeConfig(Model):
    principal: str
    project_id: str
    repository: str = "."
    state_file: str = ".oracle/bridge.db"
    heartbeat_seconds: int = Field(default=60, ge=5, le=3600)
    transport: Literal["ssh", "local"] = "ssh"
    host: str = ""
    command: str = "oracle-server serve-ssh"
    identity_file: str | None = None
    known_hosts: str | None = None
    port: int = Field(default=22, ge=1, le=65535)
    server_db: str | None = None


def read_bridge(path):
    config = BridgeConfig.model_validate(yaml.safe_load(Path(path).read_text()))
    base = Path(path).resolve().parent
    for key in ["repository", "state_file", "identity_file", "known_hosts", "server_db"]:
        value = getattr(config, key)
        if value:
            setattr(config, key, str((base / Path(value).expanduser()).resolve()))
    return config


def build_bridge(config):
    from .bridge import Bridge
    from .transport import LocalTransport, SSHTransport

    if config.transport == "local":
        from .service import Oracle
        from .store import Store

        if not config.server_db:
            raise ValueError("Local transport requires server_db")
        transport = LocalTransport(Oracle(Store(config.server_db)), config.principal)
    else:
        transport = SSHTransport(
            config.host,
            command=config.command,
            identity_file=config.identity_file,
            known_hosts=config.known_hosts,
            port=config.port,
        )
    return Bridge(config.state_file, transport, config.principal, config.project_id, config.repository)


def default_db():
    return os.environ.get("ORACLE_DB", str(Path.home() / ".local/state/oracle/oracle.db"))
