-- Platform Data Contract only. No conversion, down migration or history rewrite.
-- Block concurrent V2 writes until initialization and guards commit together.
LOCK TABLE agent_templates,agent_template_versions,agent_template_version_skills,
  agent_template_version_tools,agent_template_tests,tenant_agent_instances,
  agent_execution_contexts,conversation_agent_contexts,task_agent_contexts IN SHARE ROW EXCLUSIVE MODE;

CREATE TABLE IF NOT EXISTS platform_compatibility_state (
  scope TEXT PRIMARY KEY CHECK (scope='agent_data_contract'),
  epoch TEXT NOT NULL,
  epoch_rank INTEGER NOT NULL,
  contract_id TEXT NOT NULL DEFAULT 'agent-productized-v1' CHECK (contract_id='agent-productized-v1'),
  advanced_at TIMESTAMPTZ,
  advanced_by_release_id TEXT,
  advanced_by_source_commit TEXT,
  advance_origin TEXT NOT NULL CHECK (advance_origin IN ('initial_legacy','migration_detection','controlled_advance')),
  CHECK ((epoch='legacy_v1' AND epoch_rank=1 AND advance_origin='initial_legacy'
    AND advanced_at IS NULL AND advanced_by_release_id IS NULL AND advanced_by_source_commit IS NULL)
    OR (epoch='productized_v1' AND epoch_rank=2 AND advanced_at IS NOT NULL
      AND ((advance_origin='migration_detection' AND advanced_by_release_id IS NULL AND advanced_by_source_commit IS NULL)
        OR (advance_origin='controlled_advance' AND advanced_by_release_id ~ '^[A-Za-z0-9._-]+$'
          AND advanced_by_source_commit ~ '^[0-9a-f]{40}$'
          AND advanced_by_release_id IS NOT NULL AND advanced_by_source_commit IS NOT NULL))))
);

-- Current V2 evidence is sufficient to initialize upward, never to lower state.
DO $$ DECLARE has_v2 BOOLEAN; current_state RECORD;
BEGIN
  IF EXISTS(SELECT 1 FROM agent_template_versions v JOIN agent_templates t ON t.id=v.agent_template_id WHERE t.definition_source<>'productized')
    OR EXISTS(SELECT 1 FROM tenant_agent_instances i JOIN agent_templates t ON t.id=i.agent_id
      WHERE t.definition_source<>'productized' AND (i.agent_template_version_id IS NOT NULL OR i.instance_id IS NOT NULL OR i.overrides_json IS NOT NULL))
    OR EXISTS(SELECT 1 FROM agent_execution_contexts c JOIN agent_templates t ON t.id=c.agent_id WHERE t.definition_source<>'productized') THEN
    RAISE EXCEPTION 'compatibility_epoch_data_contract_conflict';
  END IF;
  SELECT EXISTS(SELECT 1 FROM agent_templates WHERE definition_source='productized')
    OR EXISTS(SELECT 1 FROM agent_template_versions)
    OR EXISTS(SELECT 1 FROM agent_template_version_skills)
    OR EXISTS(SELECT 1 FROM agent_template_version_tools)
    OR EXISTS(SELECT 1 FROM agent_template_tests)
    OR EXISTS(SELECT 1 FROM tenant_agent_instances WHERE agent_template_version_id IS NOT NULL OR instance_id IS NOT NULL OR overrides_json IS NOT NULL)
    OR EXISTS(SELECT 1 FROM agent_execution_contexts)
    OR EXISTS(SELECT 1 FROM conversation_agent_contexts)
    OR EXISTS(SELECT 1 FROM task_agent_contexts) INTO has_v2;
  SELECT * INTO current_state FROM platform_compatibility_state WHERE scope='agent_data_contract' FOR UPDATE;
  IF FOUND THEN
    IF current_state.contract_id <> 'agent-productized-v1'
      OR NOT ((current_state.epoch='legacy_v1' AND current_state.epoch_rank=1)
        OR (current_state.epoch='productized_v1' AND current_state.epoch_rank=2))
      OR (has_v2 AND current_state.epoch <> 'productized_v1') THEN
      RAISE EXCEPTION 'compatibility_epoch_inconsistent';
    END IF;
  ELSE
    -- An applied 011 with a missing singleton is corruption, not first install.
    IF to_regclass('public.schema_migrations') IS NOT NULL THEN
      IF EXISTS(SELECT 1 FROM schema_migrations WHERE version='011') THEN
        RAISE EXCEPTION 'compatibility_epoch_missing';
      END IF;
    END IF;
    INSERT INTO platform_compatibility_state(scope,epoch,epoch_rank,advanced_at,advance_origin)
    VALUES ('agent_data_contract',CASE WHEN has_v2 THEN 'productized_v1' ELSE 'legacy_v1' END,
      CASE WHEN has_v2 THEN 2 ELSE 1 END,CASE WHEN has_v2 THEN CURRENT_TIMESTAMP ELSE NULL END,
      CASE WHEN has_v2 THEN 'migration_detection' ELSE 'initial_legacy' END);
  END IF;
END $$;

CREATE OR REPLACE FUNCTION guard_platform_compatibility_state() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP IN ('DELETE','TRUNCATE') THEN RAISE EXCEPTION 'compatibility_epoch_delete_forbidden'; END IF;
  IF TG_OP='UPDATE' THEN
    IF NEW.scope <> OLD.scope OR NEW.contract_id <> OLD.contract_id
      OR NEW.epoch_rank < OLD.epoch_rank
      OR (OLD.epoch_rank=2 AND to_jsonb(NEW) <> to_jsonb(OLD))
      OR (OLD.epoch_rank=1 AND NOT (NEW.epoch='productized_v1' AND NEW.epoch_rank=2 AND NEW.advance_origin='controlled_advance')) THEN
      RAISE EXCEPTION 'compatibility_epoch_transition_forbidden';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION require_productized_compatibility_epoch() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE current_epoch TEXT; current_rank INTEGER;
BEGIN
  IF TG_TABLE_NAME='agent_templates' THEN
    IF NEW.definition_source <> 'productized' THEN RETURN NEW; END IF;
  END IF;
  IF TG_TABLE_NAME='tenant_agent_instances' THEN
    IF NEW.agent_template_version_id IS NULL AND NEW.instance_id IS NULL AND NEW.overrides_json IS NULL THEN RETURN NEW; END IF;
  END IF;
  SELECT epoch,epoch_rank INTO current_epoch,current_rank FROM platform_compatibility_state
    WHERE scope='agent_data_contract' AND contract_id='agent-productized-v1' FOR SHARE;
  IF current_epoch IS DISTINCT FROM 'productized_v1' OR current_rank IS DISTINCT FROM 2 THEN
    RAISE EXCEPTION 'compatibility_epoch_not_advanced';
  END IF;
  RETURN NEW;
END $$;
DO $$ DECLARE item TEXT; BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='guard_platform_compatibility_state') THEN
    CREATE TRIGGER guard_platform_compatibility_state BEFORE UPDATE OR DELETE ON platform_compatibility_state
      FOR EACH ROW EXECUTE FUNCTION guard_platform_compatibility_state();
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='guard_platform_compatibility_truncate') THEN
    CREATE TRIGGER guard_platform_compatibility_truncate BEFORE TRUNCATE ON platform_compatibility_state
      FOR EACH STATEMENT EXECUTE FUNCTION guard_platform_compatibility_state();
  END IF;
  FOREACH item IN ARRAY ARRAY['agent_templates','agent_template_versions','agent_template_version_skills',
    'agent_template_version_tools','agent_template_tests','tenant_agent_instances','agent_execution_contexts',
    'conversation_agent_contexts','task_agent_contexts'] LOOP
    IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='require_epoch_' || item) THEN
      EXECUTE 'CREATE TRIGGER ' || quote_ident('require_epoch_' || item) || ' BEFORE INSERT OR UPDATE ON '
        || quote_ident(item) || ' FOR EACH ROW EXECUTE FUNCTION require_productized_compatibility_epoch()';
    END IF;
  END LOOP;
END $$;
