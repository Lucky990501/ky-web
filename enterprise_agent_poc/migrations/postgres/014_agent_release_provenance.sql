-- Release ownership is separate from the Agent product graph. No historical
-- Agent is adopted and no product row is changed by this migration.
CREATE TABLE IF NOT EXISTS agent_release_operations (
  operation_id TEXT PRIMARY KEY,
  operation_type TEXT NOT NULL CHECK (operation_type = 'agent_provision'),
  release_identity TEXT NOT NULL,
  source_identity TEXT NOT NULL CHECK (source_identity ~ '^[0-9a-f]{40}$'),
  manifest_identity TEXT NOT NULL CHECK (manifest_identity ~ '^[0-9a-f]{64}$'),
  agent_slug TEXT NOT NULL CHECK (agent_slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  pre_state TEXT NOT NULL CHECK (pre_state = 'ABSENT'),
  pre_state_evidence JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'provisioning' CHECK (status IN
    ('provisioning','staged','published','enabled','committed','aborting','aborted','manual_recovery_required')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  commit_point_at TIMESTAMPTZ,
  aborted_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  created_by TEXT NOT NULL REFERENCES users(id),
  CHECK (pre_state_evidence = '{"template_count":0,"instance_count":0,"published_revision_count":0}'::jsonb),
  CHECK ((status = 'committed') = (commit_point_at IS NOT NULL)),
  CHECK ((status = 'aborted') = (aborted_at IS NOT NULL)),
  CHECK (completed_at IS NULL OR status IN ('committed','aborted'))
);
-- An aborted attempt retains its history but no longer holds the slug lease.
CREATE UNIQUE INDEX IF NOT EXISTS agent_release_active_slug
  ON agent_release_operations(agent_slug) WHERE status <> 'aborted';

CREATE TABLE IF NOT EXISTS agent_release_artifacts (
  id BIGSERIAL PRIMARY KEY,
  operation_id TEXT NOT NULL REFERENCES agent_release_operations(operation_id) ON DELETE RESTRICT,
  artifact_type TEXT NOT NULL CHECK (artifact_type IN
    ('template','revision','skill_binding','tool_binding','tenant_instance','identity_seal','runtime_validation')),
  artifact_id TEXT NOT NULL,
  identity JSONB NOT NULL CHECK (jsonb_typeof(identity) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (operation_id, artifact_type, artifact_id)
);
CREATE INDEX IF NOT EXISTS agent_release_artifact_identity
  ON agent_release_artifacts(artifact_type,artifact_id);

CREATE TABLE IF NOT EXISTS agent_release_events (
  id BIGSERIAL PRIMARY KEY,
  operation_id TEXT NOT NULL REFERENCES agent_release_operations(operation_id) ON DELETE RESTRICT,
  event_type TEXT NOT NULL,
  evidence JSONB NOT NULL CHECK (jsonb_typeof(evidence) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE OR REPLACE FUNCTION guard_agent_release_operation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'agent_release_operation_delete_forbidden'; END IF;
  IF (to_jsonb(NEW) - 'status' - 'commit_point_at' - 'aborted_at' - 'completed_at') <>
     (to_jsonb(OLD) - 'status' - 'commit_point_at' - 'aborted_at' - 'completed_at') THEN
    RAISE EXCEPTION 'agent_release_identity_immutable';
  END IF;
  IF OLD.status IN ('committed','aborted') OR NOT (
    (OLD.status='provisioning' AND NEW.status IN ('staged','aborting','manual_recovery_required')) OR
    (OLD.status='staged' AND NEW.status IN ('published','aborting','manual_recovery_required')) OR
    (OLD.status='published' AND NEW.status IN ('enabled','committed','aborting','manual_recovery_required')) OR
    (OLD.status='enabled' AND NEW.status IN ('committed','aborting','manual_recovery_required')) OR
    (OLD.status='aborting' AND NEW.status IN ('aborted','manual_recovery_required'))
  ) THEN RAISE EXCEPTION 'agent_release_transition_forbidden'; END IF;
  IF NEW.status='committed' AND (NEW.commit_point_at IS NULL OR NEW.completed_at IS NULL OR NEW.aborted_at IS NOT NULL) THEN
    RAISE EXCEPTION 'agent_release_commit_evidence_required';
  END IF;
  IF NEW.status='aborted' AND (NEW.aborted_at IS NULL OR NEW.completed_at IS NULL OR NEW.commit_point_at IS NOT NULL) THEN
    RAISE EXCEPTION 'agent_release_abort_evidence_required';
  END IF;
  INSERT INTO agent_release_events(operation_id,event_type,evidence)
    VALUES (OLD.operation_id,
      CASE WHEN NEW.status='committed' THEN 'commit_point'
           WHEN NEW.status='aborted' THEN 'abort_completed'
           ELSE 'status_transition' END,
      jsonb_build_object('from',OLD.status,'to',NEW.status));
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION guard_agent_release_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'agent_release_evidence_append_only';
END $$;

CREATE OR REPLACE FUNCTION guard_agent_release_artifact_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE current_status TEXT;
BEGIN
  SELECT status INTO current_status FROM agent_release_operations WHERE operation_id=NEW.operation_id FOR UPDATE;
  IF current_status NOT IN ('provisioning','staged','published','enabled','aborting') THEN
    RAISE EXCEPTION 'agent_release_artifact_after_terminal_state';
  END IF;
  RETURN NEW;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='agent_release_operation_guard') THEN
    CREATE TRIGGER agent_release_operation_guard BEFORE UPDATE OR DELETE ON agent_release_operations
      FOR EACH ROW EXECUTE FUNCTION guard_agent_release_operation();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='agent_release_operation_no_truncate') THEN
    CREATE TRIGGER agent_release_operation_no_truncate BEFORE TRUNCATE ON agent_release_operations
      FOR EACH STATEMENT EXECUTE FUNCTION guard_agent_release_append_only();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='agent_release_artifact_guard') THEN
    CREATE TRIGGER agent_release_artifact_guard BEFORE UPDATE OR DELETE ON agent_release_artifacts
      FOR EACH ROW EXECUTE FUNCTION guard_agent_release_append_only();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='agent_release_artifact_no_truncate') THEN
    CREATE TRIGGER agent_release_artifact_no_truncate BEFORE TRUNCATE ON agent_release_artifacts
      FOR EACH STATEMENT EXECUTE FUNCTION guard_agent_release_append_only();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='agent_release_artifact_insert_guard') THEN
    CREATE TRIGGER agent_release_artifact_insert_guard BEFORE INSERT ON agent_release_artifacts
      FOR EACH ROW EXECUTE FUNCTION guard_agent_release_artifact_insert();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='agent_release_event_guard') THEN
    CREATE TRIGGER agent_release_event_guard BEFORE UPDATE OR DELETE ON agent_release_events
      FOR EACH ROW EXECUTE FUNCTION guard_agent_release_append_only();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='agent_release_event_no_truncate') THEN
    CREATE TRIGGER agent_release_event_no_truncate BEFORE TRUNCATE ON agent_release_events
      FOR EACH STATEMENT EXECUTE FUNCTION guard_agent_release_append_only();
  END IF;
END $$;
