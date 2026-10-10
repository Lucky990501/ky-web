"""Owner-only PREPARE preview lookup. This is NOT an Action execution grant.

The client selects a message, never a task, receipt, file, tenant or credential.
Existing V1 receipts pin HTML but not the complete upload bundle. Consequently
this reader must not be used to authorize CREATE_DRAFT from a historical file.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from app.wechat_prepare_action import safe_html


class PrepareReadError(PermissionError):
    def __init__(self):
        super().__init__('WECHAT_PREPARE_NOT_AVAILABLE')


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def identity(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':')).encode('utf-8'))


def _uuid(value):
    return isinstance(value, str) and str(uuid.UUID(value)) == value


def _read_regular(path: Path, maximum: int) -> bytes:
    # Reject all redirecting ancestors, including Windows junctions. The data
    # root remains server-owned; this is not a reader for user-writable mounts.
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError()
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
        raise ValueError()
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                 getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(fd, 'rb') as handle:
        opened = os.fstat(handle.fileno())
        if not os.path.samestat(before, opened):
            raise ValueError()
        raw = handle.read(maximum + 1)
        after = os.fstat(handle.fileno())
    if (len(raw) != before.st_size or len(raw) > maximum or
            before.st_mtime_ns != after.st_mtime_ns or not os.path.samestat(after, path.stat())):
        raise ValueError()
    return raw


class WechatPrepareReader:
    def __init__(self, store, data_dir):
        self.store, self.data_dir = store, Path(data_dir).absolute()

    def read(self, principal, agent_id, message_id):
        try:
            return self._read(principal, agent_id, message_id)
        except Exception:
            # Never expose file paths, raw receipts, SQL, article or credentials.
            raise PrepareReadError() from None

    def bundle(self, principal, agent_id, message_id):
        """Trusted server use only; never serialize this result into an API."""
        try:
            return self._read(principal,agent_id,message_id,internal=True)
        except Exception:
            raise PrepareReadError() from None

    def _read(self, principal, agent_id, message_id, internal=False):
        if not all(isinstance(x, str) and re.fullmatch(r'[a-zA-Z0-9_:-]{1,160}', x)
                   for x in (agent_id, message_id, principal.tenant_id, principal.user_id)):
            raise ValueError()
        with self.store.connection() as conn:
            # Ownership is current DB state, not role/email/client declarations.
            rows = conn.execute("""
                SELECT t.id AS task_id,t.run_id,t.conversation_id,m.content,
                       r.result_json,c.id AS context_id,c.agent_template_version_id,
                       c.runtime_profile_id,c.tool_policy_snapshot,v.payload AS run_payload
                FROM messages m JOIN conversation_owners o ON o.conversation_id=m.conversation_id
                JOIN conversations cv ON cv.id=m.conversation_id
                JOIN users u ON u.id=o.user_id AND u.tenant_id=cv.tenant_id
                JOIN tasks t ON t.conversation_id=cv.id AND t.tenant_id=cv.tenant_id
                    AND t.user_id=u.id AND t.agent_id=cv.agent_id
                JOIN task_results r ON r.task_id=t.id
                JOIN task_agent_contexts tc ON tc.task_id=t.id
                JOIN agent_execution_contexts c ON c.id=tc.context_id
                    AND c.tenant_id=t.tenant_id AND c.agent_id=t.agent_id
                JOIN conversation_agent_contexts cc ON cc.conversation_id=cv.id AND cc.context_id=c.id
                JOIN agent_templates a ON a.id=c.agent_id
                JOIN agent_template_versions av ON av.id=c.agent_template_version_id AND av.agent_template_id=a.id
                JOIN tenant_agent_instances i ON i.instance_id=c.instance_id
                    AND i.tenant_id=c.tenant_id AND i.agent_id=c.agent_id
                JOIN run_traces v ON v.run_id=t.run_id AND v.tenant_id=t.tenant_id
                    AND v.agent_id=t.agent_id AND v.conversation_id=t.conversation_id
                WHERE m.id=? AND m.role='assistant' AND o.deleted_at IS NULL
                    AND u.id=? AND u.tenant_id=? AND u.account_status='enabled'
                    AND t.agent_id=? AND a.slug='wechat-official-account-writing'
                    AND t.status='completed' AND v.status='completed'
                    AND c.runtime_profile_id=cv.runtime_profile_id
                    AND i.status='enabled' AND av.status='published'
                    AND i.agent_template_version_id=av.id AND a.current_published_version_id=av.id
                """, (message_id, principal.user_id, principal.tenant_id, agent_id)).fetchall()
            matches = [dict(row) for row in rows
                       if strict_json(row['result_json']).get('assistant_message_id') == message_id]
            if len(matches) != 1:
                raise ValueError()
            task = matches[0]
            if strict_json(task['run_payload']).get('execution_context_id') != task['context_id']:
                raise ValueError()
            refs = [ref for ref in strict_json(task['tool_policy_snapshot'])['skill_refs']
                    if ref['slug'] == 'wechat-html-draft']
            if len(refs) != 1:
                raise ValueError()
            skill = refs[0]
            binding = conn.execute("""SELECT v.id FROM agent_template_version_skills b
                JOIN skill_versions v ON v.id=b.skill_version_id AND v.skill_id=b.skill_id
                JOIN skill_packages p ON p.skill_version_id=v.id
                WHERE b.agent_template_version_id=? AND v.id=? AND v.skill_id=?
                  AND v.version=? AND v.status='published' AND v.checksum=? AND p.sha256=v.checksum""",
                (task['agent_template_version_id'], skill['id'], skill['skill_id'], skill['version'], skill['checksum'])).fetchone()
            if not binding:
                raise ValueError()
            audits = conn.execute("SELECT payload FROM execution_events WHERE conversation_id=? AND event_type='skill.execution'",
                                  (task['conversation_id'],)).fetchall()
            receipts = []
            for row in audits:
                receipt = strict_json(row['payload'])
                if receipt.get('task_id') == task['task_id'] and receipt.get('status') == 'completed' and receipt.get('action') == 'PREPARE':
                    receipts.append(receipt)
            # Multiple successful PREPARE invocations require an explicit durable
            # article-version binding. Do not guess "latest" from timestamps.
            if len(receipts) != 1:
                raise ValueError()
            receipt = receipts[0]
        expected = dict(contract='SKILL_REVISION_RUNTIME_DISPATCH_V1', tenant_id=principal.tenant_id,
                        agent_id=agent_id, task_id=task['task_id'], run_id=task['run_id'],
                        action='PREPARE', status='completed', exit_status=0,
                        skill_key='wechat-html-draft', revision=skill['id'], version=skill['version'],
                        artifact_identity=skill['checksum'])
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise ValueError()
        if not _uuid(receipt['id']) or not _uuid(task['task_id']):
            raise ValueError()
        names = (principal.tenant_id, agent_id, task['runtime_profile_id'])
        if any(not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', name) for name in names):
            raise ValueError()
        workspace = self.data_dir.joinpath('runtime', *names, 'workspace', 'tasks', task['task_id'], receipt['id'])
        if not workspace.resolve().is_relative_to(self.data_dir.resolve()):
            raise ValueError()
        if strict_json(_read_regular(workspace/'receipt.json', 64 * 1024)) != receipt:
            raise ValueError()
        prefix = f"workspace:tasks/{task['task_id']}/{receipt['id']}/"
        artifacts = receipt['artifact_refs']
        allowed = {'run/prepared.html': ('text/html', 256 * 1024),
                   'verification.json': ('application/json', 64 * 1024),
                   'upload-manifest.v2.json': ('application/json',64*1024)}
        if not isinstance(artifacts, list) or len(artifacts) not in (2,3):
            raise ValueError()
        bodies = {}
        for artifact in artifacts:
            if set(artifact) != {'ref', 'mime_type', 'size_bytes', 'sha256'} or not artifact['ref'].startswith(prefix):
                raise ValueError()
            relative = artifact['ref'][len(prefix):]
            if relative not in allowed or relative in bodies:
                raise ValueError()
            mime, maximum = allowed[relative]
            raw = _read_regular(workspace/relative, maximum)
            if (artifact['mime_type'] != mime or type(artifact['size_bytes']) is not int or
                    artifact['size_bytes'] != len(raw) or digest(raw) != artifact['sha256']):
                raise ValueError()
            bodies[relative] = raw
        check = strict_json(bodies['verification.json'])
        if check.get('offline') is not True or type(check.get('wechat_calls')) is not int or check['wechat_calls'] != 0:
            raise ValueError()
        html = bodies['run/prepared.html'].decode('utf-8')
        safe_html(html)
        version = identity(dict(message_id=message_id, content_sha256=digest(task['content'].encode('utf-8')),
                                task_id=task['task_id'], run_id=task['run_id'],
                                agent_revision_id=task['agent_template_version_id'],
                                skill_revision_id=skill['id'], receipt_sha256=identity(receipt)))
        result=dict(agent_id=agent_id, message_id=message_id, article_version=version,
                    html=html, html_sha256=digest(bodies['run/prepared.html']),
                    action_authorized=False, upload_bundle_verified=False,
                    state='PREVIEW_ONLY', visual_verified=False)
        manifest_raw=bodies.get('upload-manifest.v2.json')
        if manifest_raw is not None:
            from app.wechat_prepare_action import image_sources
            manifest=strict_json(manifest_raw)
            expected=dict(contract='WECHAT_PREPARE_UPLOAD_MANIFEST_V2',tenant_id=principal.tenant_id,
                user_id=principal.user_id,agent_id=agent_id,source_task_id=task['task_id'],source_run_id=task['run_id'],
                skill_revision_id=skill['id'],skill_checksum=skill['checksum'])
            if any(manifest.get(k)!=v for k,v in expected.items()):raise ValueError()
            if manifest['environment'] not in ('test','production') or not re.fullmatch('[a-f0-9]{64}',manifest['account_identity']):raise ValueError()
            article=manifest['article']
            if set(article)!={'title','digest','html_sha256','cover','assets','images'}:raise ValueError()
            if (identity(article)!=manifest['content_version'] or article['html_sha256']!=result['html_sha256']
                or not 0<len(article['title'])<=64 or not 0<len(article['digest'])<=120):raise ValueError()
            assets=article['assets'];total=0
            if not isinstance(assets,dict) or not 1<=len(assets)<=12:raise ValueError()
            for name,pin in assets.items():
                if not re.fullmatch(r'assets/[a-zA-Z0-9_-]{1,48}\.(png|jpg|jpeg|webp)',name):raise ValueError()
                raw=_read_regular(workspace/name,2*1024*1024);total+=len(raw)
                if pin!={'sha256':digest(raw),'size_bytes':len(raw)}:raise ValueError()
            if total>8*1024*1024 or article['cover'] not in assets:raise ValueError()
            images=image_sources(html)
            if images!=article['images'] or any(name not in assets for name in images):raise ValueError()
            result.update(upload_bundle_verified=True,state='PREPARED')
            if internal:
                result.update(workspace=workspace,manifest=manifest,binding=dict(
                    tenant_id=principal.tenant_id,user_id=principal.user_id,environment=manifest['environment'],
                    agent_id=agent_id,agent_revision_id=task['agent_template_version_id'],skill_revision_id=skill['id'],
                    source_task_id=task['task_id'],source_run_id=task['run_id'],source_message_id=message_id,
                    context_id=task['context_id'],conversation_id=task['conversation_id'],article_version=version,
                    account_identity=manifest['account_identity'],manifest_sha256=digest(manifest_raw)))
        if internal and not result['upload_bundle_verified']:raise ValueError()
        return result
