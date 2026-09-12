"""Transport-independent RPC, with a restricted SSH command as the initial wire."""

import asyncio
import json
import shlex
from abc import ABC, abstractmethod

from .policy import OracleError


class TransportUnavailable(Exception):
    pass


class Transport(ABC):
    @abstractmethod
    async def send(self, message: dict) -> dict: ...

    async def receive(self, session_id: str, after=0):
        return await self.send({"op": "poll", "session_id": session_id, "after": after})

    async def health(self):
        return await self.send({"op": "health"})


class SSHTransport(Transport):
    def __init__(
        self,
        host,
        *,
        command="oracle-server serve-ssh",
        identity_file=None,
        known_hosts=None,
        port=22,
        timeout=20,
    ):
        if not host or host.startswith("-") or any(c.isspace() for c in host):
            raise ValueError("Invalid SSH destination")
        self.host, self.command, self.port, self.timeout = host, command, int(port), timeout
        self.identity_file, self.known_hosts = identity_file, known_hosts

    async def send(self, message):
        args = [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "-p",
            str(self.port),
        ]
        if self.identity_file:
            args += ["-i", self.identity_file, "-o", "IdentitiesOnly=yes"]
        if self.known_hosts:
            args += ["-o", "UserKnownHostsFile=" + self.known_hosts]
        args += [self.host, self.command]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(json.dumps(message).encode()), self.timeout
            )
        except (TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.wait()
            raise TransportUnavailable("SSH request timed out") from None
        if proc.returncode != 0:
            raise TransportUnavailable("SSH transport failed: " + stderr.decode(errors="replace")[:300])
        try:
            result = json.loads(stdout)
        except (ValueError, UnicodeError):
            raise TransportUnavailable("Server returned an invalid RPC response") from None
        if not result.get("ok"):
            error = result.get("error", {})
            raise OracleError(
                error.get("code", "SERVER_ERROR"), error.get("message", "Oracle request failed")
            )
        return result["result"]


class LocalTransport(Transport):
    """Explicit in-process adapter for tests and trusted host-local tools."""

    def __init__(self, oracle, principal):
        self.oracle, self.principal = oracle, principal

    async def send(self, message):
        from .rpc import dispatch

        return dispatch(self.oracle, self.principal, message)


def forced_command(executable, db, principal):
    """An administrator installs this command against a specific SSH public key."""
    return (
        "env ORACLE_DB="
        + shlex.quote(db)
        + " ORACLE_PRINCIPAL="
        + shlex.quote(principal)
        + " "
        + shlex.quote(executable)
        + " serve-ssh"
    )
