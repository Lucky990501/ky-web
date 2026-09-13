-- Expand only the Runtime Test status domain; never rewrite historical rows.
DO $$
DECLARE definition TEXT;
BEGIN
  SELECT pg_get_constraintdef(oid) INTO definition FROM pg_constraint
    WHERE conrelid='agent_template_tests'::regclass
      AND conname='agent_template_tests_status_check' AND contype='c';
  IF definition IS NULL THEN
    RAISE EXCEPTION 'Runtime Test status constraint missing; controlled review required';
  END IF;
  IF position('queued' in definition)=0 THEN
    IF EXISTS (SELECT 1 FROM agent_template_tests WHERE status NOT IN ('passed','failed','invalidated')) THEN
      RAISE EXCEPTION 'Unexpected historical Runtime Test status';
    END IF;
    ALTER TABLE agent_template_tests DROP CONSTRAINT agent_template_tests_status_check;
    ALTER TABLE agent_template_tests ADD CONSTRAINT agent_template_tests_status_check
      CHECK (status IN ('queued','running','passed','failed','invalidated'));
  END IF;
END $$;
