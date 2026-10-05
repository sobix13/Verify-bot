#!/usr/bin/env python3
"""Build source-only ZIP/TAR packages and SHA-256 manifests; no secrets or runtime data."""
import argparse
import hashlib
import json
import re
import tarfile
import zipfile
from pathlib import Path

EXCLUDED={'.git','.venv','__pycache__','.pytest_cache','data','backups','dist','releases'}


def files(root):
    for path in sorted(root.rglob('*')):
        rel=path.relative_to(root)
        if any(part in EXCLUDED for part in rel.parts) or path.name=='.env' or path.suffix in ('.pyc','.sqlite3','.rcvbackup') or path.name.startswith('STAGED_'): continue
        if path.is_symlink(): raise ValueError('Source packages must not contain symbolic links.')
        if path.is_file(): yield path,rel


def build(root,output):
    root=Path(root).resolve(); output=Path(output).resolve()
    if output==root or root in output.parents: raise ValueError('Write release packages outside the source tree.')
    output.mkdir(parents=True,exist_ok=True); version=(root/'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+',version): raise ValueError('Invalid version.')
    entries=list(files(root)); name='ripcars-verifier-'+version
    source_manifest={str(rel):hashlib.sha256(path.read_bytes()).hexdigest() for path,rel in entries}
    (output/'SOURCE_MANIFEST.json').write_text(json.dumps(source_manifest,indent=2)+'\n')
    with zipfile.ZipFile(output/(name+'.zip'),'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path,rel in entries: archive.write(path,str(Path(name)/rel))
    with tarfile.open(output/(name+'.tar.gz'),'w:gz') as archive:
        for path,rel in entries: archive.add(path,arcname=str(Path(name)/rel),recursive=False)
    public=[]
    for filename in ('README.md','DEPLOYMENT.md','API_CONTRACT.md','ADMIN_GUIDE.md','TEST_RESULTS.txt','VALIDATION.json'):
        source=root/filename
        if source.exists(): (output/filename).write_bytes(source.read_bytes()); public.append(filename)
    artifacts=[name+'.zip',name+'.tar.gz','SOURCE_MANIFEST.json',*public]
    checks=''.join(hashlib.sha256((output/file).read_bytes()).hexdigest()+'  '+file+'\n' for file in artifacts)
    (output/'RIPCARS_VERIFIER_SHA256SUMS.txt').write_text(checks)
    return artifacts


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--source',default=str(Path(__file__).resolve().parents[1])); parser.add_argument('--output',required=True); args=parser.parse_args()
    for name in build(args.source,args.output): print(name)
