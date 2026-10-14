-- PROPOSAL ONLY. Not in migrations/postgres; not executable by migrate.py.
-- Integrate after the separately frozen WeChat 016 and approved Schema floor.
-- No changes to Migration015 or its task_id UNIQUE. No legacy row backfill.
CREATE TABLE task_reference_images (
 task_id TEXT NOT NULL REFERENCES tasks(id),
 attachment_id TEXT NOT NULL UNIQUE REFERENCES chat_image_attachments(id),
 tenant_id TEXT NOT NULL REFERENCES tenants(id), user_id TEXT NOT NULL REFERENCES users(id),
 position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 4),
 input_count INTEGER NOT NULL CHECK(input_count BETWEEN 1 AND 5 AND position<input_count),
 purpose TEXT NOT NULL CHECK(length(btrim(purpose)) BETWEEN 1 AND 120),
 content_sha256 TEXT NOT NULL CHECK(content_sha256 ~ '^[a-f0-9]{64}$'),
 confirmation_sha256 TEXT NOT NULL CHECK(confirmation_sha256 ~ '^[a-f0-9]{64}$'),
 dispatch_state TEXT NOT NULL DEFAULT 'ready' CHECK(dispatch_state IN ('ready','submitted','completed','unknown')),
 dispatch_fingerprint TEXT CHECK(dispatch_fingerprint ~ '^[a-f0-9]{64}$'),
 result_json TEXT,
 PRIMARY KEY(task_id,position)
);
CREATE FUNCTION task_reference_images_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'reference_retention_required'; END IF;
 IF TG_OP='INSERT' THEN
  -- Locks serialize a competing single/multi claim for the same upload.
  PERFORM 1 FROM chat_image_attachments a WHERE a.id=NEW.attachment_id FOR UPDATE;
  IF NOT EXISTS (SELECT 1 FROM tasks t JOIN chat_image_attachments a ON a.id=NEW.attachment_id
    WHERE t.id=NEW.task_id AND t.agent_id='image-agent' AND t.tenant_id=NEW.tenant_id AND t.user_id=NEW.user_id
      AND a.tenant_id=t.tenant_id AND a.user_id=t.user_id AND (a.task_id IS NULL OR a.task_id=t.id)
      AND EXISTS (SELECT 1 FROM users u WHERE u.id=t.user_id AND u.tenant_id=t.tenant_id))
   THEN RAISE EXCEPTION 'reference_owner_mismatch'; END IF;
  IF NEW.dispatch_state<>'ready' OR NEW.dispatch_fingerprint IS NOT NULL OR NEW.result_json IS NOT NULL
   THEN RAISE EXCEPTION 'reference_initial_state'; END IF;
 ELSIF TG_OP='UPDATE' THEN
  IF ROW(NEW.task_id,NEW.attachment_id,NEW.tenant_id,NEW.user_id,NEW.position,NEW.input_count,NEW.purpose,NEW.content_sha256,NEW.confirmation_sha256)
     IS DISTINCT FROM ROW(OLD.task_id,OLD.attachment_id,OLD.tenant_id,OLD.user_id,OLD.position,OLD.input_count,OLD.purpose,OLD.content_sha256,OLD.confirmation_sha256)
    THEN RAISE EXCEPTION 'reference_identity_immutable'; END IF;
  IF NOT COALESCE((OLD.dispatch_state='ready' AND NEW.dispatch_state='submitted' AND NEW.position=0
           AND NEW.dispatch_fingerprint IS NOT NULL AND NEW.result_json IS NULL
       OR OLD.dispatch_state='submitted' AND NEW.dispatch_state IN ('completed','unknown') AND NEW.position=0
           AND NEW.dispatch_fingerprint=OLD.dispatch_fingerprint
           AND (NEW.dispatch_state='completed' AND NEW.result_json IS NOT NULL
                OR NEW.dispatch_state='unknown' AND NEW.result_json IS NULL)),FALSE)
    THEN RAISE EXCEPTION 'reference_invalid_transition'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER task_reference_images_guard BEFORE INSERT OR UPDATE OR DELETE ON task_reference_images
 FOR EACH ROW EXECUTE FUNCTION task_reference_images_guard();
CREATE FUNCTION task_reference_images_complete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT count(*) FROM task_reference_images WHERE task_id=NEW.task_id)<>NEW.input_count
  OR EXISTS (SELECT 1 FROM task_reference_images WHERE task_id=NEW.task_id AND input_count<>NEW.input_count)
  OR EXISTS (SELECT 1 FROM task_reference_images WHERE task_id=NEW.task_id AND confirmation_sha256<>NEW.confirmation_sha256)
  THEN RAISE EXCEPTION 'reference_binding_incomplete'; END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER task_reference_images_complete AFTER INSERT ON task_reference_images
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION task_reference_images_complete();
