"""Exercise real OpenSSH RPC using dedicated, restricted demo keys on the Oracle host."""

import asyncio
import json
from pathlib import Path
import subprocess
from datetime import datetime, timezone

from oracle.demo import run_demo
from oracle.transport import SSHTransport, forced_command


root = Path(__file__).resolve().parents[1]
run = root / "runtime" / ("ssh-demo-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"))
keys = run / "keys"
keys.mkdir(parents=True, mode=0o700)
ssh = Path.home() / ".ssh"
ssh.mkdir(mode=0o700, exist_ok=True)
authorized = ssh / "authorized_keys"
known = keys / "known_hosts"
host_key = Path("/etc/ssh/ssh_host_ed25519_key.pub").read_text().split()
known.write_text("127.0.0.1 " + host_key[0] + " " + host_key[1] + "\n")
known.chmod(0o600)
entries = []
for human in ["alice", "bob", "carol"]:
    key = keys / (human + "_ed25519")
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key), "-C", "oracle-demo-" + human],
        check=True,
    )
    command = forced_command(str(root / ".venv/bin/oracle-server"), str(run / "oracle.db"), human)
    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    entries.append('restrict,command="' + escaped + '" ' + key.with_suffix(".pub").read_text().strip() + "\n")
# Append only isolated forced-command credentials; existing authorized keys are preserved.
with authorized.open("a") as stream:
    stream.write("\n" + "".join(entries))
authorized.chmod(0o600)


def factory(human, db):
    return SSHTransport(
        "dell@127.0.0.1", identity_file=str(keys / (human + "_ed25519")), known_hosts=str(known), timeout=30
    )


result = asyncio.run(run_demo(run, factory))
result["key_scope"] = "One principal per key; restricted forced command; project RPC only"
result["deployment_scope"] = "Three simulated machines on one host using real SSH connections"
print(json.dumps(result, indent=2))
