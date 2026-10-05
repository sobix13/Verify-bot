#!/usr/bin/env python3
"""Verify source manifest, SHA-256 sums and every ZIP/TAR file without extraction."""
import argparse
import hashlib
import json
import re
import tarfile
import zipfile
from pathlib import Path,PurePosixPath


def verify(directory):
    directory=Path(directory)
    manifest=json.loads((directory/'SOURCE_MANIFEST.json').read_text())
    if not manifest or not all(isinstance(name,str) and re.fullmatch('[0-9a-f]{64}',value) for name,value in manifest.items()):raise ValueError('Invalid source manifest.')
    checks=list(directory.glob('*SHA256SUMS.txt'))
    if len(checks)!=1:raise ValueError('Expected one SHA-256 checksum file.')
    for line in checks[0].read_text().splitlines():
        digest,name=line.split('  ',1)
        if Path(name).name!=name or not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('Invalid checksum entry.')
        if hashlib.sha256((directory/name).read_bytes()).hexdigest()!=digest:raise ValueError('Checksum mismatch: '+name)
    archives=list(directory.glob('*.zip'))+list(directory.glob('*.tar.gz'))
    if len(archives)!=2:raise ValueError('Expected matching ZIP and TAR archives.')
    for path in archives:
        entries={}
        if path.suffix=='.zip':
            with zipfile.ZipFile(path) as archive:
                for item in archive.infolist():
                    if item.is_dir():continue
                    if (item.external_attr>>16)&0o170000==0o120000:raise ValueError('Symlink in archive.')
                    if item.filename in entries:raise ValueError('Duplicate archive entry.')
                    entries[item.filename]=archive.read(item)
        else:
            with tarfile.open(path) as archive:
                for item in archive:
                    if item.isdir():continue
                    if not item.isfile() or item.name in entries:raise ValueError('Invalid or duplicate TAR entry.')
                    entries[item.name]=archive.extractfile(item).read()
        actual={}
        prefixes=set()
        for name,data in entries.items():
            parts=PurePosixPath(name).parts
            if len(parts)<2 or name.startswith('/') or '..' in parts:raise ValueError('Unsafe archive path.')
            prefixes.add(parts[0]);relative='/'.join(parts[1:])
            if any(part in ('.venv','.git','__pycache__','data','backups','releases') for part in parts) or parts[-1]=='.env' or parts[-1].endswith(('.pyc','.sqlite3','.rcvbackup')):raise ValueError('Runtime/private file in archive.')
            actual[relative]=hashlib.sha256(data).hexdigest()
        if len(prefixes)!=1 or actual!=manifest:raise ValueError('Archive differs from its complete source manifest: '+path.name)
    return {'ok':True,'source_files':len(manifest),'archives_verified':len(archives),'checksums_verified':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory');args=parser.parse_args()
    print(json.dumps(verify(args.directory),indent=2))
