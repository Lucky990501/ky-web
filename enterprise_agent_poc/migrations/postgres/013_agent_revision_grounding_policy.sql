-- Revision-owned, opt-in grounding contract. Historical rows remain NULL/OFF.
ALTER TABLE agent_template_versions
  ADD COLUMN IF NOT EXISTS grounding_policy JSONB;

-- The database also rejects direct writes that bypass application validation.
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'agent_revision_grounding_policy_v1'
      AND conrelid = 'agent_template_versions'::regclass
  ) THEN
    ALTER TABLE agent_template_versions
      ADD CONSTRAINT agent_revision_grounding_policy_v1 CHECK (
        grounding_policy IS NULL OR
        (grounding_policy = '{"enabled":true,"mode":"claim_audit_v1","max_corrections":1}'::jsonb
         AND grounding_policy->>'max_corrections' = '1')
      );
  END IF;
END $$;
