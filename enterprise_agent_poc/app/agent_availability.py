"""One read-side availability contract for productized Agents.

Legacy CATALOG Agents retain their existing route. A productized Agent is
available only when its Template is published, its current pointer names a
published Revision of that Template, and (for a tenant) the exact current
Revision's Instance is enabled. An aborted release is never resolvable even
if a later ordinary write accidentally restores other fields.
"""


def release_provenance_available(conn, *, postgres: bool) -> bool:
    if not postgres:
        return False
    return bool(conn.execute("SELECT to_regclass('public.agent_release_operations') AS name").fetchone()["name"])


def release_aborted(conn, template_id: str, *, postgres: bool) -> bool:
    if not release_provenance_available(conn, postgres=postgres):
        return False  # 014 may be pending on an existing deployment.
    return bool(conn.execute(
        "SELECT 1 FROM agent_release_operations o JOIN agent_release_artifacts a "
        "ON a.operation_id=o.operation_id WHERE a.artifact_type='template' "
        "AND a.artifact_id=? AND o.status='aborted' LIMIT 1", (template_id,)
    ).fetchone())


def retired_validation_task(conn, task_id: str, *, postgres: bool) -> bool:
    if not release_provenance_available(conn, postgres=postgres):
        return False
    return bool(conn.execute(
        "SELECT 1 FROM agent_release_operations o JOIN agent_release_artifacts a "
        "ON a.operation_id=o.operation_id WHERE o.status='aborted' "
        "AND a.artifact_type='runtime_validation' AND a.artifact_id=?", (task_id,)
    ).fetchone())


def retired_validation_conversation(conn, conversation_id: str, *, postgres: bool) -> bool:
    if not release_provenance_available(conn, postgres=postgres):
        return False
    return bool(conn.execute(
        "SELECT 1 FROM agent_release_operations o JOIN agent_release_artifacts a "
        "ON a.operation_id=o.operation_id AND a.artifact_type='runtime_validation' "
        "JOIN tasks t ON t.id=a.artifact_id WHERE o.status='aborted' "
        "AND t.conversation_id=?", (conversation_id,)
    ).fetchone())


def current_published_revision(conn, template_id: str, *, postgres: bool) -> str | None:
    if release_aborted(conn, template_id, postgres=postgres):
        return None
    row = conn.execute(
        "SELECT v.id FROM agent_templates t JOIN agent_template_versions v "
        "ON v.id=t.current_published_version_id AND v.agent_template_id=t.id "
        "WHERE t.id=? AND t.definition_source='productized' "
        "AND t.lifecycle_status='published' AND v.status='published'", (template_id,)
    ).fetchone()
    return row["id"] if row else None


def tenant_available(conn, tenant_id: str, template_id: str, *, postgres: bool) -> bool:
    revision_id = current_published_revision(conn, template_id, postgres=postgres)
    return bool(revision_id and conn.execute(
        "SELECT 1 FROM tenant_agent_instances WHERE tenant_id=? AND agent_id=? "
        "AND agent_template_version_id=? AND status='enabled'",
        (tenant_id, template_id, revision_id),
    ).fetchone())
