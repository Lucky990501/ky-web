-- Expand only. Never run on PRIMARY/Production without migration admission.
-- One durable action record; tasks, runs and historical receipts are untouched.
CREATE TABLE IF NOT EXISTS wechat_draft_operations (
  id TEXT PRIMARY KEY,
  environment TEXT NOT NULL CHECK (environment IN ('test','production')),
  tenant_id TEXT NOT NULL REFERENCES tenants(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  confirmation_key TEXT NOT NULL CHECK (confirmation_key ~ '^[a-f0-9]{64}$'),
  action_task_id TEXT UNIQUE NOT NULL REFERENCES tasks(id),
  action_run_id TEXT UNIQUE NOT NULL REFERENCES run_traces(run_id),
  source_task_id TEXT NOT NULL REFERENCES tasks(id),
  source_run_id TEXT NOT NULL REFERENCES run_traces(run_id),
  source_message_id TEXT NOT NULL REFERENCES messages(id),
  context_id TEXT NOT NULL REFERENCES agent_execution_contexts(id),
  agent_id TEXT NOT NULL REFERENCES agent_templates(id),
  agent_revision_id TEXT NOT NULL REFERENCES agent_template_versions(id),
  skill_revision_id TEXT NOT NULL REFERENCES skill_versions(id),
  article_version TEXT NOT NULL CHECK (article_version ~ '^[a-f0-9]{64}$'),
  prepare_manifest_sha256 TEXT NOT NULL CHECK (prepare_manifest_sha256 ~ '^[a-f0-9]{64}$'),
  account_identity TEXT NOT NULL CHECK (account_identity ~ '^[a-f0-9]{64}$'),
  secret_version INTEGER NOT NULL CHECK (secret_version > 0),
  capability_identity TEXT NOT NULL CHECK (capability_identity ~ '^[a-f0-9]{64}$'),
  binding_json JSONB NOT NULL CHECK (jsonb_typeof(binding_json)='object'),
  state TEXT NOT NULL DEFAULT 'QUEUED' CHECK (state IN ('QUEUED','UPLOADING','SUBMITTING','VERIFYING','UNKNOWN','CONFIRMED','FAILED','REVOKED')),
  revision BIGINT NOT NULL DEFAULT 1 CHECK (revision > 0),
  lease_id TEXT,
  lease_expires_at TIMESTAMPTZ,
  intent_json JSONB NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(intent_json)='array'),
  upload_receipts_json JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(upload_receipts_json)='object'),
  draft_media_id TEXT,
  verification_json JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(verification_json)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(environment,tenant_id,user_id,confirmation_key),
  CHECK(action_task_id <> source_task_id AND action_run_id <> source_run_id),
  CHECK((lease_id IS NULL) = (lease_expires_at IS NULL)),
  CHECK(draft_media_id IS NULL OR (draft_media_id ~ '^[a-zA-Z0-9_-]+$' AND char_length(draft_media_id) BETWEEN 1 AND 256)),
  CHECK(state <> 'CONFIRMED' OR (draft_media_id IS NOT NULL
    AND verification_json->>'media_id'=draft_media_id
    AND verification_json->>'account_identity'=account_identity
    AND verification_json->>'manifest_sha256'=prepare_manifest_sha256
    AND verification_json->>'matched'='true'
    AND verification_json->>'readback_sha256' ~ '^[a-f0-9]{64}$') IS TRUE)
);

CREATE OR REPLACE FUNCTION guard_wechat_draft_operation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item JSONB;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'WECHAT_OPERATION_DELETE_BLOCKED'; END IF;
  IF TG_OP='INSERT' THEN
    IF NEW.state <> 'QUEUED' OR NEW.revision <> 1 OR NEW.intent_json <> '[]'::jsonb
      OR NEW.upload_receipts_json <> '{}'::jsonb OR NEW.verification_json <> '{}'::jsonb
      OR NEW.draft_media_id IS NOT NULL OR NEW.lease_id IS NOT NULL THEN
      RAISE EXCEPTION 'WECHAT_OPERATION_INITIAL_STATE';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM tasks s JOIN tasks a ON a.id=NEW.action_task_id
      JOIN users u ON u.id=NEW.user_id AND u.tenant_id=NEW.tenant_id
      JOIN conversations cv ON cv.id=s.conversation_id AND cv.tenant_id=NEW.tenant_id AND cv.agent_id=NEW.agent_id
      JOIN conversation_owners o ON o.conversation_id=cv.id AND o.user_id=NEW.user_id AND o.deleted_at IS NULL
      JOIN messages m ON m.id=NEW.source_message_id AND m.conversation_id=cv.id AND m.role='assistant'
      JOIN task_results tr ON tr.task_id=s.id AND tr.result_json::jsonb->>'assistant_message_id'=m.id
      JOIN run_traces sr ON sr.run_id=NEW.source_run_id AND sr.run_id=s.run_id AND sr.tenant_id=s.tenant_id AND sr.agent_id=s.agent_id AND sr.conversation_id=cv.id AND sr.status='completed'
      JOIN run_traces ar ON ar.run_id=NEW.action_run_id AND ar.run_id=a.run_id AND ar.tenant_id=a.tenant_id AND ar.agent_id=a.agent_id AND ar.conversation_id=cv.id
      JOIN task_agent_contexts sc ON sc.task_id=s.id AND sc.context_id=NEW.context_id
      JOIN task_agent_contexts ac ON ac.task_id=a.id AND ac.context_id=NEW.context_id
      JOIN agent_execution_contexts c ON c.id=NEW.context_id AND c.tenant_id=NEW.tenant_id AND c.agent_id=NEW.agent_id AND c.agent_template_version_id=NEW.agent_revision_id
      JOIN agent_template_version_skills b ON b.agent_template_version_id=NEW.agent_revision_id AND b.skill_version_id=NEW.skill_revision_id
      WHERE s.id=NEW.source_task_id AND s.tenant_id=NEW.tenant_id AND s.user_id=NEW.user_id AND s.agent_id=NEW.agent_id AND s.status='completed'
      AND a.tenant_id=NEW.tenant_id AND a.user_id=NEW.user_id AND a.agent_id=NEW.agent_id AND a.conversation_id=cv.id AND a.status='queued'
      AND u.account_status='enabled'
      AND ar.payload::jsonb->>'execution_context_id'=NEW.context_id
      AND ar.payload::jsonb->>'wechat_operation_id'=NEW.id
      AND NEW.binding_json->>'context_id'=NEW.context_id
      AND NEW.binding_json->>'manifest_sha256'=NEW.prepare_manifest_sha256
      AND NEW.binding_json->>'article_version'=NEW.article_version
      AND NEW.binding_json->>'account_identity'=NEW.account_identity
    ) THEN RAISE EXCEPTION 'WECHAT_OPERATION_ASSOCIATION'; END IF;
  ELSE
    IF (to_jsonb(NEW) - ARRAY['state','revision','lease_id','lease_expires_at','intent_json','upload_receipts_json','draft_media_id','verification_json','updated_at'])
      IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['state','revision','lease_id','lease_expires_at','intent_json','upload_receipts_json','draft_media_id','verification_json','updated_at']) THEN
      RAISE EXCEPTION 'WECHAT_OPERATION_IMMUTABLE';
    END IF;
    IF NEW.revision <> OLD.revision+1 THEN RAISE EXCEPTION 'WECHAT_OPERATION_CAS'; END IF;
    IF NEW.state <> OLD.state AND NOT (
      (OLD.state='QUEUED' AND NEW.state IN ('UPLOADING','FAILED','REVOKED','UNKNOWN')) OR
      (OLD.state='UPLOADING' AND NEW.state IN ('SUBMITTING','UNKNOWN','FAILED','REVOKED')) OR
      (OLD.state='SUBMITTING' AND NEW.state IN ('VERIFYING','UNKNOWN')) OR
      (OLD.state='VERIFYING' AND NEW.state IN ('CONFIRMED','UNKNOWN')) OR
      (OLD.state='UNKNOWN' AND NEW.state='VERIFYING' AND OLD.draft_media_id IS NOT NULL)
    ) THEN RAISE EXCEPTION 'WECHAT_OPERATION_TRANSITION'; END IF;
    -- Evidence is append only, not a mutable task_results slot.
    IF jsonb_array_length(NEW.intent_json) < jsonb_array_length(OLD.intent_json)
      OR NOT NEW.upload_receipts_json @> OLD.upload_receipts_json
      OR (OLD.draft_media_id IS NOT NULL AND NEW.draft_media_id IS DISTINCT FROM OLD.draft_media_id)
      OR (OLD.state IN ('CONFIRMED','FAILED','REVOKED') AND
          (NEW.intent_json,NEW.upload_receipts_json,NEW.verification_json,NEW.draft_media_id)
          IS DISTINCT FROM (OLD.intent_json,OLD.upload_receipts_json,OLD.verification_json,OLD.draft_media_id)) THEN
      RAISE EXCEPTION 'WECHAT_OPERATION_EVIDENCE_IMMUTABLE';
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(OLD.intent_json) LOOP
      IF NOT NEW.intent_json @> jsonb_build_array(item) THEN RAISE EXCEPTION 'WECHAT_INTENT_IMMUTABLE'; END IF;
    END LOOP;
    IF NEW.intent_json <> OLD.intent_json AND (OLD.state NOT IN ('UPLOADING','SUBMITTING')
      OR NEW.state NOT IN ('UPLOADING','SUBMITTING') OR jsonb_array_length(NEW.intent_json)<>jsonb_array_length(OLD.intent_json)+1) THEN
      RAISE EXCEPTION 'WECHAT_INTENT_TRANSITION';
    END IF;
    IF NEW.upload_receipts_json <> OLD.upload_receipts_json AND OLD.state NOT IN ('UPLOADING','SUBMITTING') THEN
      RAISE EXCEPTION 'WECHAT_RECEIPT_TRANSITION';
    END IF;
  END IF;
  NEW.updated_at := CURRENT_TIMESTAMP;
  RETURN NEW;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='wechat_draft_operation_guard') THEN
    CREATE TRIGGER wechat_draft_operation_guard BEFORE INSERT OR UPDATE OR DELETE ON wechat_draft_operations
      FOR EACH ROW EXECUTE FUNCTION guard_wechat_draft_operation();
  END IF;
END $$;
