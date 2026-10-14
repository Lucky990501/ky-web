"""Ordered private input bindings, not the generations[] output contract.

Postgres installations require the separately approved mapping migration. No
runtime DDL or implicit migration is performed against PostgreSQL here.
"""
from __future__ import annotations

import json
import re

CONTRACT = "task-reference-images-v2"
MAX_REFERENCES = 5
SQLITE_FIXTURE_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_reference_images (
 task_id TEXT NOT NULL REFERENCES tasks(id),
 attachment_id TEXT NOT NULL UNIQUE REFERENCES chat_image_attachments(id),
 tenant_id TEXT NOT NULL REFERENCES tenants(id), user_id TEXT NOT NULL REFERENCES users(id),
 position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 4),
 input_count INTEGER NOT NULL CHECK(input_count BETWEEN 1 AND 5 AND position<input_count),
 purpose TEXT NOT NULL CHECK(length(trim(purpose)) BETWEEN 1 AND 120),
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64 AND content_sha256 NOT GLOB '*[^a-f0-9]*'),
 confirmation_sha256 TEXT NOT NULL CHECK(length(confirmation_sha256)=64 AND confirmation_sha256 NOT GLOB '*[^a-f0-9]*'),
 dispatch_state TEXT NOT NULL DEFAULT 'ready' CHECK(dispatch_state IN ('ready','submitted','completed','unknown')),
 dispatch_fingerprint TEXT CHECK(dispatch_fingerprint IS NULL OR length(dispatch_fingerprint)=64 AND dispatch_fingerprint NOT GLOB '*[^a-f0-9]*'), result_json TEXT,
 PRIMARY KEY(task_id,position)
);
CREATE TRIGGER IF NOT EXISTS task_reference_images_owner BEFORE INSERT ON task_reference_images
WHEN NOT EXISTS (SELECT 1 FROM tasks t JOIN chat_image_attachments a ON a.id=NEW.attachment_id
 WHERE t.id=NEW.task_id AND t.agent_id='image-agent' AND t.tenant_id=NEW.tenant_id
 AND t.user_id=NEW.user_id AND a.tenant_id=t.tenant_id AND a.user_id=t.user_id
 AND EXISTS (SELECT 1 FROM users u WHERE u.id=t.user_id AND u.tenant_id=t.tenant_id)
 AND (a.task_id IS NULL OR a.task_id=t.id))
 OR NEW.dispatch_state<>'ready' OR NEW.dispatch_fingerprint IS NOT NULL OR NEW.result_json IS NOT NULL
BEGIN SELECT RAISE(ABORT,'reference_owner_mismatch'); END;
CREATE TRIGGER IF NOT EXISTS task_reference_images_immutable BEFORE UPDATE ON task_reference_images
WHEN NEW.task_id!=OLD.task_id OR NEW.attachment_id!=OLD.attachment_id OR NEW.tenant_id!=OLD.tenant_id
 OR NEW.user_id!=OLD.user_id OR NEW.position!=OLD.position OR NEW.input_count!=OLD.input_count OR NEW.purpose!=OLD.purpose
 OR NEW.content_sha256!=OLD.content_sha256
 OR NEW.confirmation_sha256!=OLD.confirmation_sha256
 OR NOT COALESCE((OLD.dispatch_state='ready' AND NEW.dispatch_state='submitted' AND NEW.position=0
         AND NEW.dispatch_fingerprint IS NOT NULL AND NEW.result_json IS NULL
      OR OLD.dispatch_state='submitted' AND NEW.dispatch_state IN ('completed','unknown') AND NEW.position=0
         AND NEW.dispatch_fingerprint=OLD.dispatch_fingerprint
         AND (NEW.dispatch_state='completed' AND NEW.result_json IS NOT NULL
              OR NEW.dispatch_state='unknown' AND NEW.result_json IS NULL)),0)
BEGIN SELECT RAISE(ABORT,'reference_immutable_or_invalid_transition'); END;
CREATE TRIGGER IF NOT EXISTS task_reference_images_retention BEFORE DELETE ON task_reference_images
BEGIN SELECT RAISE(ABORT,'reference_retention_required'); END;
"""


def selection(ids, purposes=None):
    if not isinstance(ids, list) or len(ids) > MAX_REFERENCES or any(not isinstance(v, str) for v in ids) or len(set(ids)) != len(ids):
        raise ValueError("参考图须为1～5个不重复的附件ID。")
    if any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9-]{36}", value) for value in ids):
        raise ValueError("参考图片ID无效。")
    purposes = purposes if purposes is not None else [f"参考图{i+1}" for i in range(len(ids))]
    if len(purposes) != len(ids) or any(not isinstance(p, str) or not 1 <= len(p.strip()) <= 120 for p in purposes):
        raise ValueError("每张参考图必须有1～120字用途。")
    return [(value, i, purposes[i].strip()) for i, value in enumerate(ids)]


def available(conn, postgres):
    if postgres:
        return bool(conn.execute("SELECT to_regclass('task_reference_images') AS name").fetchone()["name"])
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='task_reference_images'").fetchone())


def bound(conn, postgres, task_id, tenant_id):
    rows = []
    if available(conn, postgres):
        rows = conn.execute(
            "SELECT a.*,m.position,m.input_count,m.purpose,m.content_sha256,m.confirmation_sha256,m.tenant_id AS bound_tenant,m.user_id AS bound_user,"
            "t.user_id AS task_user,t.agent_id,m.dispatch_state,m.dispatch_fingerprint,m.result_json "
            "FROM task_reference_images m JOIN tasks t ON t.id=m.task_id "
            "LEFT JOIN chat_image_attachments a ON a.id=m.attachment_id WHERE t.id=? AND t.tenant_id=? ORDER BY m.position",
            (task_id, tenant_id)).fetchall()
    if rows:
        result = [dict(row) for row in rows]
        if len(result) > MAX_REFERENCES or any(
            r["id"] is None or r["position"] != i or r["input_count"] != len(result) or r["tenant_id"] != tenant_id or r["bound_tenant"] != tenant_id
            or r["task_id"] not in {None, task_id} or r["confirmation_sha256"] != result[0]["confirmation_sha256"]
            or r["user_id"] != r["task_user"] or r["bound_user"] != r["task_user"] or r["agent_id"] != "image-agent"
            for i, r in enumerate(result)
        ):
            raise ValueError("reference_binding_integrity")
        return result
    legacy = conn.execute(
        "SELECT a.* FROM chat_image_attachments a JOIN tasks t ON t.id=a.task_id "
        "WHERE t.id=? AND t.tenant_id=? AND t.agent_id='image-agent' AND a.tenant_id=t.tenant_id AND a.user_id=t.user_id",
        (task_id, tenant_id)).fetchone()
    return [{**dict(legacy), "position": 0, "purpose": "参考图1", "content_sha256": None}] if legacy else []


def confirmation(tenant_id, user_id, text, conversation_id, items, hashes):
    import hashlib
    raw = {"tenant_id": tenant_id, "user_id": user_id, "agent_id": "image-agent", "text": text,
           "conversation_id": conversation_id, "inputs": [(identifier, position, purpose, hashes.get(identifier)) for identifier, position, purpose in items]}
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def prior_confirmation(conn, postgres, tenant_id, user_id, items, digest):
    if len(items) <= 1 or not available(conn, postgres):
        return None
    # Serialize the same ordered confirmation and competing subsets without a
    # second queue or operation framework. UNIQUE remains the final DB guard.
    for identifier, _, _ in sorted(items):
        conn.execute("SELECT id FROM chat_image_attachments WHERE id=? AND tenant_id=? AND user_id=?" + (" FOR UPDATE" if postgres else ""),
                     (identifier, tenant_id, user_id)).fetchone()
    rows = [conn.execute("SELECT task_id,position,confirmation_sha256 FROM task_reference_images WHERE attachment_id=? AND tenant_id=? AND user_id=?",
                         (identifier, tenant_id, user_id)).fetchone() for identifier, _, _ in items]
    if not any(rows):
        return None
    if (not all(rows) or len({r["task_id"] for r in rows}) != 1
            or any(r["position"] != i or r["confirmation_sha256"] != digest for i, r in enumerate(rows))):
        raise ValueError("reference_confirmation_conflict")
    row = conn.execute("SELECT * FROM tasks WHERE id=? AND tenant_id=? AND user_id=? AND agent_id='image-agent'",
                       (rows[0]["task_id"], tenant_id, user_id)).fetchone()
    if not row:
        raise ValueError("reference_confirmation_integrity")
    return {**dict(row), "_submission_reused": True}


def bind(conn, postgres, task_id, tenant_id, user_id, items, hashes, confirmation_sha256=None):
    mapped = available(conn, postgres)
    if len(items) > 1 and not mapped:
        raise ValueError("MULTI_REFERENCE_SCHEMA_NOT_INSTALLED")
    for attachment_id, position, purpose in items:
        suffix = " FOR UPDATE" if postgres else ""
        row = conn.execute("SELECT * FROM chat_image_attachments WHERE id=? AND tenant_id=? AND user_id=?" + suffix,
                           (attachment_id, tenant_id, user_id)).fetchone()
        if not row or row["task_id"] is not None:
            raise ValueError("参考图片不存在、无权使用或已经发送。")
        if mapped and conn.execute("SELECT 1 FROM task_reference_images WHERE attachment_id=?", (attachment_id,)).fetchone():
            raise ValueError("参考图片已经发送。")
        digest = hashes.get(attachment_id)
        if len(items) > 1 and (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)):
            raise ValueError("reference_manifest_hash_required")
        # Legacy task_id UNIQUE is immutable. Only single-reference tasks write
        # that compatibility pointer; all multi-reference ownership is in m.
        if len(items) == 1:
            claimed = conn.execute("UPDATE chat_image_attachments SET task_id=? WHERE id=? AND tenant_id=? AND user_id=? AND task_id IS NULL",
                                   (task_id, attachment_id, tenant_id, user_id))
            if claimed.rowcount != 1:
                raise ValueError("参考图片已经发送。")
        if mapped and digest:
            conn.execute("INSERT INTO task_reference_images(task_id,attachment_id,tenant_id,user_id,position,input_count,purpose,content_sha256,confirmation_sha256) VALUES (?,?,?,?,?,?,?,?,?)",
                         (task_id, attachment_id, tenant_id, user_id, position, len(items), purpose, digest, confirmation_sha256))


def manifest(references):
    return [{"id": r["id"], "position": r["position"], "purpose": r["purpose"], "sha256": r["content_sha256"]}
            for r in references]


def claim_dispatch(store, task_id, tenant_id, request_fingerprint):
    with store.connection() as conn:
        claimed = conn.execute(
            "UPDATE task_reference_images SET dispatch_state='submitted',dispatch_fingerprint=? "
            "WHERE task_id=? AND tenant_id=? AND position=0 AND dispatch_state='ready' "
            "AND EXISTS (SELECT 1 FROM tasks t WHERE t.id=task_reference_images.task_id AND t.tenant_id=? AND t.user_id=task_reference_images.user_id AND t.agent_id='image-agent' AND t.status='running' AND t.stage<>'cancelling')",
            (request_fingerprint, task_id, tenant_id, tenant_id))
        if claimed.rowcount == 1:
            return None
        row = conn.execute("SELECT dispatch_state,dispatch_fingerprint,result_json FROM task_reference_images WHERE task_id=? AND tenant_id=? AND position=0",
                           (task_id, tenant_id)).fetchone()
        if row and row["dispatch_state"] == "completed" and row["dispatch_fingerprint"] == request_fingerprint:
            return json.loads(row["result_json"])
        raise ValueError("reference_dispatch_unknown_or_consumed")


def finish_dispatch(store, task_id, tenant_id, result=None):
    with store.connection() as conn:
        changed = conn.execute(
            "UPDATE task_reference_images SET dispatch_state=?,result_json=? "
            "WHERE task_id=? AND tenant_id=? AND position=0 AND dispatch_state='submitted'",
            ("completed" if result is not None else "unknown", json.dumps(result, ensure_ascii=False) if result is not None else None, task_id, tenant_id))
        if changed.rowcount != 1:
            raise ValueError("reference_dispatch_state_conflict")


# No exact llm-api.net + sunburst-c multi-input qualification is approved.
# Immutable code registry, not a user/env/Tool flag. A later reviewed successor
# may add an exact provider/model/endpoint + field + limit + evidence hash.
VERIFIED_MULTI_REFERENCE_CONTRACTS = ()
EDIT_ENDPOINT = "https://llm-api.net/v1/images/edits"


def multi_file_field(provider, model, count):
    from app.image_request_contract import ImageRequestError
    for entry in VERIFIED_MULTI_REFERENCE_CONTRACTS:
        if (entry.get("provider"), entry.get("model"), entry.get("endpoint")) == (provider, model, EDIT_ENDPOINT):
            if (entry.get("status") == "VERIFIED" and 2 <= count <= min(MAX_REFERENCES, entry.get("max_inputs", 0))
                    and entry.get("file_field") in {"image", "image[]"}
                    and re.fullmatch(r"[a-f0-9]{64}", entry.get("evidence_sha256", ""))):
                return entry["file_field"]
    raise ImageRequestError("multi_reference_provider_not_verified", "当前网关及模型的多参考图能力尚未核验，未创建付费执行；请保留全部素材，或明确选择一张后另建单图任务。")
