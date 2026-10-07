"""Architecture-only lock generation from predownloaded official Linux wheels.

No install, source build or runtime invocation. Outputs are immutable generated
metadata; changed output requires a new descriptor revision, never overwrite.
"""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

from pip._vendor.packaging.markers import default_environment
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.specifiers import SpecifierSet
from pip._vendor.packaging.tags import compatible_tags, cpython_tags
from pip._vendor.packaging.utils import canonicalize_name, parse_wheel_filename

ROOT=Path(__file__).resolve().parents[1]
PLATFORMS=['manylinux_2_28_x86_64','manylinux_2_27_x86_64',
           'manylinux_2_17_x86_64','manylinux2014_x86_64']
TARGET=dict(os='linux',architecture='x86_64',implementation='cpython',
            python='3.11',abi='cp311',glibc_min='2.28')


def sha(raw):return hashlib.sha256(raw).hexdigest()


def write_new(path, raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('LOCK_REVISION_IMMUTABLE')
    else:
        with path.open('xb') as stream:stream.write(raw)


def build(wheelhouse: Path, output: Path):
    source=ROOT/'skill_sources/wechat-html-draft/1.0.0/scripts/requirements.txt'
    direct=[Requirement(line) for line in source.read_text().splitlines() if line.strip()]
    old=json.loads((ROOT/'integrations/wechat-html-draft.dependencies.v1.json').read_text())
    pins={canonicalize_name(name):version for name,version in old['packages'].items()}
    tags=set(cpython_tags((3,11),['cp311'],PLATFORMS))
    tags.update(compatible_tags((3,11),'cp311',PLATFORMS))
    environment=default_environment()
    environment.update(python_version='3.11',python_full_version='3.11.16',
                       sys_platform='linux',platform_system='Linux',platform_machine='x86_64',
                       implementation_name='cpython',implementation_version='3.11.16',
                       platform_python_implementation='CPython',extra='')
    packages={}
    for path in sorted(wheelhouse.iterdir()):
        if path.is_symlink() or path.suffix!='.whl':raise ValueError('WHEELHOUSE_FILES_BLOCKED')
        name,version,_,wheel_tags=parse_wheel_filename(path.name)
        name=str(name);version=str(version)
        if name in packages or not tags.intersection(wheel_tags):raise ValueError('WHEEL_PLATFORM_BLOCKED')
        if pins.get(name)!=version:raise ValueError('DEPENDENCY_PIN_CHANGED_NEW_REVISION_REQUIRED')
        with zipfile.ZipFile(path) as archive:
            metadata=[n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
            if len(metadata)!=1:raise ValueError('WHEEL_METADATA_BLOCKED')
            meta=BytesParser().parsebytes(archive.read(metadata[0]))
        if canonicalize_name(meta['Name'])!=name or meta['Version']!=version:
            raise ValueError('WHEEL_METADATA_IDENTITY_BLOCKED')
        if '3.11.16' not in SpecifierSet(meta.get('Requires-Python','')):
            raise ValueError('WHEEL_PYTHON_BLOCKED')
        digest=sha(path.read_bytes())
        url=f'https://pypi.org/pypi/{name}/{version}/json'
        with urllib.request.urlopen(url,timeout=30) as response:
            data=json.loads(response.read(4*1024*1024+1))
        artifacts=[item for item in data['urls'] if item['filename']==path.name]
        if (len(artifacts)!=1 or artifacts[0]['digests']['sha256']!=digest
                or artifacts[0]['yanked'] or not artifacts[0]['url'].startswith('https://files.pythonhosted.org/')):
            raise ValueError('OFFICIAL_WHEEL_SHA_BLOCKED')
        raw_dependencies=meta.get_all('Requires-Dist',[])
        active=[str(req) for text in raw_dependencies
                if not (req:=Requirement(text)).marker or req.marker.evaluate(environment)]
        packages[name]=dict(package=name,version=version,filename=path.name,sha256=digest,
            url=artifacts[0]['url'],tags=sorted(str(tag) for tag in wheel_tags),
            requires_python=meta.get('Requires-Python',''),requires_dist=raw_dependencies,
            target_dependencies=active,official_sha_verified=True)
    if set(packages)!=set(pins):raise ValueError('DEPENDENCY_CLOSURE_INCOMPLETE')
    reached=set()
    pending=direct[:]
    while pending:
        req=pending.pop();name=canonicalize_name(req.name)
        item=packages.get(name)
        if not item or item['version'] not in req.specifier:raise ValueError('DEPENDENCY_CLOSURE_CONFLICT')
        if name in reached:continue
        reached.add(name)
        pending.extend(Requirement(text) for text in item['target_dependencies'])
    if reached!=set(packages):raise ValueError('DEPENDENCY_CLOSURE_EXTRANEOUS')
    requirements=''.join(f"{name}=={item['version']} --hash=sha256:{item['sha256']}\n"
                         for name,item in sorted(packages.items())).encode()
    lock=dict(contract='REVISION_BOUND_SKILL_PYTHON_RUNTIME_V1',skill_slug='wechat-html-draft',
        skill_version='1.0.0',target=TARGET,input_requirements_sha256=sha(source.read_bytes()),
        requirements_sha256=sha(requirements),requirements_file='wechat-python311-linux.v1.requirements.txt',
        resolution='official_pypi_download_binary_only_complete_closure',
        direct_requirements=[str(req) for req in direct],packages=list(packages.values()))
    write_new(output/'wechat-python311-linux.v1.requirements.txt',requirements)
    raw=(json.dumps(lock,ensure_ascii=False,indent=2)+'\n').encode()
    write_new(output/'wechat-python311-linux.v1.lock.json',raw)
    print(json.dumps(dict(lock_sha256=sha(raw),requirements_sha256=sha(requirements),
        packages=len(packages),wheel_sha_status='OFFICIAL_MATCH',source_builds=0)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheelhouse',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();build(args.wheelhouse,args.output)
