"""Public slug -> stable internal identity. Legacy references never change."""
import re

from app.agent_catalog import CATALOG


def resolve_agent_reference(conn, reference, *, postgres=False):
    if not isinstance(reference,str):raise LookupError('Unknown Agent reference')
    if reference in CATALOG:
        return reference
    if not isinstance(reference, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", reference) or len(reference) > 100:
        raise LookupError("Unknown Agent reference")
    # UUID references remain accepted for historical URLs, never advertised.
    row = conn.execute("SELECT id FROM agent_templates WHERE definition_source='productized' AND (slug=? OR id=?)", (reference, reference)).fetchone()
    from app.agent_availability import current_published_revision
    if not row or not current_published_revision(conn, row["id"], postgres=postgres):
        raise LookupError("Unknown Agent reference")
    return row['id']
