"""Authorization is independent of model decisions and caller-supplied identities."""

from .protocol import Visibility


class OracleError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class Policy:
    def __init__(self, store):
        self.store = store

    def role(self, human: str, project: str) -> str | None:
        row = self.store.one(
            "SELECT role FROM permissions WHERE project_id=? AND human_id=?", (project, human)
        )
        return row["role"] if row else None

    def member(self, human: str, project: str):
        if not self.role(human, project):
            raise OracleError("FORBIDDEN", "Project access denied")

    def owner(self, human: str, project: str):
        if self.role(human, project) not in {"project_owner", "administrator"}:
            raise OracleError("FORBIDDEN", "A project owner decision is required")

    def session(self, human: str, session_id: str, project: str | None = None):
        value = self.store.record("sessions", session_id)
        if not value or value["human_id"] != human or (project and value["project_id"] != project):
            raise OracleError("FORBIDDEN", "Session access denied")
        self.member(human, value["project_id"])
        return value

    def shared(self, provider: str, consumer: str, subject: str) -> bool:
        return (
            provider == consumer
            or self.store.one(
                "SELECT 1 FROM shares WHERE provider=? AND consumer=? AND subject=?",
                (provider, consumer, subject),
            )
            is not None
        )

    def related_projects(self, left: str, right: str, subject: str) -> bool:
        return self.shared(left, right, subject) or self.shared(right, left, subject)

    def visible(self, fact: dict, human: str, project: str) -> bool:
        if not self.role(human, project):
            return False
        scope = fact["visibility"]
        if scope == Visibility.ORACLE_ONLY:
            return False
        if scope == Visibility.OWNER_ONLY:
            return fact["owner_id"] == human
        if scope == Visibility.PROJECT:
            return fact["project_id"] == project
        if scope == Visibility.TEAM:
            source = self.store.record("projects", fact["project_id"])
            dest = self.store.record("projects", project)
            return bool(
                source
                and dest
                and source["team_id"] == dest["team_id"]
                and source["organization_id"] == dest["organization_id"]
            )
        if scope == Visibility.PUBLIC_CONTRACT:
            source = self.store.record("projects", fact["project_id"])
            dest = self.store.record("projects", project)
            return bool(source and dest and source["organization_id"] == dest["organization_id"])
        return fact["project_id"] == project or (
            project in fact.get("consumer_projects", [])
            and self.shared(fact["project_id"], project, fact["subject"])
        )
