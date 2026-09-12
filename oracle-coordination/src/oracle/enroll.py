"""Bind one participant's SSH public key to a restricted forced command on the Oracle host."""

import os
import sys
from pathlib import Path

from .policy import OracleError
from .transport import forced_command

KEY_TYPES = ("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521")


def parse_public_key(public_key: str) -> tuple[str, str, str]:
    """Return (type, base64 material, comment) or raise INVALID_KEY. Only single-line public keys are accepted."""
    line = (public_key or "").strip()
    if not line or "\n" in line or "\r" in line or "PRIVATE KEY" in line:
        raise OracleError("INVALID_KEY", "Expected one OpenSSH public key line")
    fields = line.split(None, 2)
    if len(fields) < 2 or fields[0] not in KEY_TYPES:
        raise OracleError("INVALID_KEY", "Public key must start with one of " + ", ".join(KEY_TYPES))
    material = fields[1]
    if len(material) < 16 or not all(c.isalnum() or c in "+/=" for c in material):
        raise OracleError("INVALID_KEY", "Public key material is not base64")
    comment = fields[2].strip() if len(fields) == 3 else ""
    if any(c.isspace() and c != " " for c in comment):
        raise OracleError("INVALID_KEY", "Key comment must be a single line")
    return fields[0], material, comment


def restricted_line(executable: str, db: str, principal: str, key_line: str) -> str:
    command = forced_command(executable, db, principal)
    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    return 'restrict,command="' + escaped + '" ' + key_line


def bridge_yaml(principal: str, project: str) -> str:
    return "\n".join(
        [
            "principal: " + principal,
            "project_id: " + project,
            "repository: /absolute/path/to/" + project,
            "state_file: ~/.oracle/" + principal + "-" + project + "-session.db",
            "transport: ssh",
            "host: ORACLE_USER@ORACLE_HOST",
            "port: 22",
            "identity_file: ~/.oracle/keys/" + principal + "_ed25519",
            "known_hosts: ~/.oracle/known_hosts",
            "command: oracle-server serve-ssh",
            "heartbeat_seconds: 60",
            "",
        ]
    )


def enroll(
    oracle,
    actor,
    human,
    project,
    public_key,
    *,
    role="developer",
    authorized_keys=None,
    executable=None,
    db=None,
    dry_run=False,
) -> dict:
    """Grant membership and install exactly one restricted authorized_keys entry for the key.

    Membership is granted first because Oracle.grant enforces owner authority; the key binding is the
    transport half of the same identity, so a non-owner can never install a forced command.
    """
    key_type, material, comment = parse_public_key(public_key)
    key_line = " ".join(f for f in (key_type, material, comment) if f)
    if not oracle.store.record("projects", project):
        raise OracleError("NOT_FOUND", "Unknown project " + project)
    if role not in {"developer", "component_owner", "project_owner"}:
        raise OracleError("INVALID_ROLE", "Unsupported role")
    if dry_run:
        oracle.policy.owner(actor, project)
    else:
        oracle.grant(actor, project, human, role)
    executable = str(executable or Path(sys.executable).with_name("oracle-server"))
    db = str(Path(db or oracle.store.path).expanduser().resolve())
    path = Path(authorized_keys or Path.home() / ".ssh" / "authorized_keys").expanduser()
    line = restricted_line(executable, db, human, key_line)
    result = {
        "human": human,
        "project": project,
        "role": role,
        "authorized_keys": str(path),
        "line": line,
        "bridge_yaml": bridge_yaml(human, project),
        "next_steps": [
            "Send the participant the Oracle host address and the fingerprint of the server host key "
            "(ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub) through a trusted channel.",
            "The participant fills in host/repository in the bridge YAML and runs: "
            "oracle-bridge --config <yaml> register <registration.json>",
            "Add the MCP server in Claude Code: claude mcp add --scope user oracle -- "
            "<venv>/bin/oracle-bridge --config <yaml> mcp",
        ],
    }
    if dry_run:
        return {"status": "dry_run", **result}
    existing = path.read_text() if path.exists() else ""
    if any(material in entry.split() for entry in existing.splitlines()):
        return {"status": "already_enrolled", **result}
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a") as stream:
        if existing and not existing.endswith("\n"):
            stream.write("\n")
        stream.write(line + "\n")
    os.chmod(path, 0o600)
    # The audit row records where the binding lives, never the key material itself.
    record = {"project": project, "role": role, "key_type": key_type, "key_comment": comment}
    record.update({"authorized_keys": str(path), "executable": executable, "db": db})
    with oracle.store.transaction():
        oracle.store.audit(actor, "participant_enrolled", human, record)
    return {"status": "enrolled", **result}
