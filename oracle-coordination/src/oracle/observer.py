"""Deterministic local metadata only; no file contents or transcripts leave by default."""

import subprocess
from pathlib import Path

from .protocol import ContextPacket, FileChanges, GitState


def inspect_git(repository: str | Path):
    root = Path(repository).resolve()

    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=10)
        if result.returncode:
            return None
        return result.stdout.decode(errors="replace").rstrip("\n")

    head = git("rev-parse", "HEAD")
    branch = git("symbolic-ref", "--short", "-q", "HEAD")
    status = git("status", "--porcelain=v1", "-z", "--untracked-files=normal")
    if status is None:
        return GitState(), []
    records, paths, index = status.split("\0"), [], 0
    while index < len(records):
        entry = records[index]
        index += 1
        if not entry:
            continue
        path = entry[3:]
        parts = Path(path).parts
        # Suppress credential/runtime metadata before crossing the transport.
        if not any(
            part.startswith(".env") or part in {".ssh", "runtime", ".oracle", ".git", ".venv"}
            for part in parts
        ):
            paths.append(path)
        if entry[0] in "RC" or entry[1] in "RC":
            index += 1
    return GitState(branch=branch, head=head, dirty=bool(status)), paths[:200]


def compile_context(session_id, repository, **semantic):
    state, paths = inspect_git(repository)
    return ContextPacket(session_id=session_id, git=state, files=FileChanges(modified=paths), **semantic)
