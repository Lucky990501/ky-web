"""One bounded /preview/ route addition; no Production application switch."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

root = Path('/opt/enterprise-agent-workbench-preview')
site = Path('/etc/nginx/sites-enabled/workbench.luckio.cn').resolve(strict=True)
assert site == Path('/etc/nginx/sites-available/workbench.luckio.cn')
original = site.read_bytes()
assert b'listen 443' in original and b'/preview' not in original
anchor = b'    location ~ ^/api/v1/tasks/'
assert original.count(anchor) == 1
backup = root / ('nginx-before-' + str(int(time.time())) + '.conf')
shutil.copy2(site, backup)
units = ['enterprise-agent-api.service', 'enterprise-agent-mcp.service', 'enterprise-agent-worker.service']
def state():
    return {'release': str(Path('/opt/enterprise-agent-workbench/release-current').resolve()),
            'pids': subprocess.check_output(['systemctl','show',*units,'-p','MainPID','--value'],text=True).split()}
before = state()
(root/'production-before.json').write_text(json.dumps({**before,'nginx_sha256':hashlib.sha256(original).hexdigest(),'backup':str(backup)}))
snippet = Path('/etc/nginx/snippets/customer-preview.conf')
assert not snippet.exists(), 'Existing Preview route needs explicit inspection'
shutil.copy2(root/'app/enterprise_agent_poc/deploy/customer-preview.nginx.conf', snippet)
assert site.read_bytes() == original, 'Concurrent Nginx change; stop'
site.write_bytes(original.replace(anchor,b'    include /etc/nginx/snippets/customer-preview.conf;\n\n'+anchor))
tested = subprocess.run(['nginx','-t'],capture_output=True,text=True)
if tested.returncode:
    site.write_bytes(original)
    raise RuntimeError('nginx -t failed; original config restored without reload')
subprocess.run(['systemctl','reload','nginx'],check=True)
assert state() == before, 'Production PID/release changed; investigate without automatic rollback'
print(json.dumps({'preview_route':'INSTALLED','nginx_test':'PASS','reload_only':True,'production_pid_release_unchanged':True}))
