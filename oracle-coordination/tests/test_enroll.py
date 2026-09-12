import pytest

from oracle.enroll import enroll
from oracle.policy import OracleError

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyMaterialForTests0000000000000000000000 dave@laptop"


def test_enroll_installs_one_restricted_key_and_grants_membership(ecosystem, tmp_path):
    oracle, _ = ecosystem
    keys = tmp_path / "authorized_keys"
    result = enroll(oracle, "owner", "dave", "backend", KEY, authorized_keys=keys, executable="/opt/oracle-server")
    assert result["status"] == "enrolled"
    lines = keys.read_text().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith('restrict,command="') and "ORACLE_PRINCIPAL=dave" in lines[0]
    assert "ORACLE_DB=" in lines[0] and lines[0].endswith(" " + KEY)
    assert keys.stat().st_mode & 0o777 == 0o600
    assert oracle.policy.role("dave", "backend") == "developer"
    assert "principal: dave" in result["bridge_yaml"] and "transport: ssh" in result["bridge_yaml"]
    audit = oracle.store.rows("SELECT * FROM audit WHERE action='participant_enrolled'")
    assert len(audit) == 1 and audit[0]["object_id"] == "dave" and "FakeKeyMaterial" not in audit[0]["data"]
    again = enroll(oracle, "owner", "dave", "backend", KEY, authorized_keys=keys, executable="/opt/oracle-server")
    assert again["status"] == "already_enrolled"
    assert len(keys.read_text().splitlines()) == 1


def test_enroll_appends_after_existing_entries(ecosystem, tmp_path):
    oracle, _ = ecosystem
    keys = tmp_path / "authorized_keys"
    keys.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExistingAdminKey000000000000000000000000000 admin")
    enroll(oracle, "owner", "dave", "backend", KEY, authorized_keys=keys)
    lines = keys.read_text().splitlines()
    assert len(lines) == 2 and lines[0].endswith(" admin") and "ORACLE_PRINCIPAL=dave" in lines[1]


def test_enroll_refuses_non_owner_bad_key_missing_project(ecosystem, tmp_path):
    oracle, _ = ecosystem
    keys = tmp_path / "authorized_keys"
    with pytest.raises(OracleError) as forbidden:
        enroll(oracle, "alice", "dave", "backend", KEY, authorized_keys=keys)
    assert forbidden.value.code == "FORBIDDEN"
    for bad in ["not a key", "ssh-ed25519", "ssh-ed25519 AAAA\nssh-ed25519 BBBB", "-----BEGIN OPENSSH PRIVATE KEY-----"]:
        with pytest.raises(OracleError) as invalid:
            enroll(oracle, "owner", "dave", "backend", bad, authorized_keys=keys)
        assert invalid.value.code == "INVALID_KEY"
    with pytest.raises(OracleError) as missing:
        enroll(oracle, "owner", "dave", "nowhere", KEY, authorized_keys=keys)
    assert missing.value.code == "NOT_FOUND"
    assert not keys.exists()
    assert oracle.policy.role("dave", "backend") is None


def test_enroll_dry_run_writes_nothing(ecosystem, tmp_path):
    oracle, _ = ecosystem
    keys = tmp_path / "authorized_keys"
    result = enroll(oracle, "owner", "dave", "backend", KEY, authorized_keys=keys, dry_run=True)
    assert result["status"] == "dry_run" and "ORACLE_PRINCIPAL=dave" in result["line"]
    assert not keys.exists()
    assert not oracle.store.rows("SELECT 1 FROM audit WHERE action='participant_enrolled'")
    assert oracle.policy.role("dave", "backend") is None
    with pytest.raises(OracleError):
        enroll(oracle, "alice", "dave", "backend", KEY, authorized_keys=keys, dry_run=True)
