param(
    [ValidateSet('review','owner','root','seal','revoke')][string]$Action='review',
    [Parameter(Mandatory)][string]$ReleaseId,
    [Parameter(Mandatory)][string]$EntryPath,
    [Parameter(Mandatory)][string]$EntrySha256,
    [Parameter(Mandatory)][string]$RequestPath,
    [Parameter(Mandatory)][string]$RequestSha256,
    [switch]$Execute
)
$ErrorActionPreference='Stop'
if($ReleaseId -notmatch '^[A-Za-z0-9._:-]{1,128}$' -or $EntrySha256 -notmatch '^[a-f0-9]{64}$' -or $RequestSha256 -notmatch '^[a-f0-9]{64}$'){throw 'Exact release and independent SHA256 required'}
$base='/opt/enterprise-agent-workbench'
$prepared="$base/shared/release-state/native-v2-preparation/$ReleaseId"
$installed="$base/releases/$ReleaseId/enterprise_agent_poc/scripts/native_v2_record_issuer.py"
if($EntryPath -notin @("$prepared/native_v2_record_issuer.py",$installed) -or $RequestPath -ne "$prepared/issuance-request.json"){throw 'Only exact POSIX preparation/installed entry paths allowed'}
if($EntryPath.Contains('\') -or $RequestPath.Contains('\')){throw 'Windows paths cannot designate Linux inputs'}
$packet=@{action=$Action;entry=$EntryPath;entry_sha256=$EntrySha256;request=$RequestPath;request_sha256=$RequestSha256}|ConvertTo-Json -Compress
$payload=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($packet))
$bootstrap=@'
import base64,hashlib,json,os,stat,sys
p=json.loads(base64.b64decode(sys.argv[1]))
assert os.getuid()==os.getgid()==0 and os.uname().nodename=='iv-yekqs6ez9cnu262hhsg0'
def verify(path,pin):
 from pathlib import Path
 q=Path(path);assert q.resolve(strict=True)==q
 for parent in q.parents:
  s=parent.stat();assert s.st_uid==0 and not s.st_mode&0o022
 with os.fdopen(os.open(q,os.O_RDONLY|os.O_NOFOLLOW),'rb') as f:
  s=os.fstat(f.fileno());assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022
  raw=f.read(1048577);assert len(raw)<=1048576 and hashlib.sha256(raw).hexdigest()==pin
verify(p['entry'],p['entry_sha256']);verify(p['request'],p['request_sha256'])
os.execv('/usr/bin/python3',['/usr/bin/python3','-I','-B','-S',p['entry'],p['action'],'--request',p['request'],'--request-sha256',p['request_sha256']])
'@
$code=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bootstrap))
$remote="/usr/bin/python3 -I -B -S -c 'import base64;exec(base64.b64decode(""$code""))' '$payload'"
if($remote.Length -gt 28000){throw 'ARGUMENT_BOUND_28000'}
if(-not $Execute){@{status='PREPARE_ONLY_NOT_AUTHORITY';action=$Action;remote_command_characters=$remote.Length;entry=$EntryPath;request=$RequestPath;human_actions='Owner, Root and release require independent actual controlling TTY input; Codex never supplies acknowledgements'}|ConvertTo-Json;return}
# The actual visible confirmation window is opened by the delegated operator.
# No input writer, fixture switch, password or confirmation argument exists.
& 'C:\Windows\System32\OpenSSH\ssh.exe' -tt -i 'C:\Users\猪猪\.ssh\ky_web_server' -p 22 -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UpdateHostKeys=no -o ConnectTimeout=10 root@101.96.193.9 $remote
if($LASTEXITCODE -ne 0){throw 'STOP_RETAIN_SCENE_NO_AUTOMATIC_RETRY'}
