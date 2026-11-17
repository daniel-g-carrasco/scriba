"""Extract official wheel DLLs and verify the pinned optional Vulkan runtime.

No installation or compilation. The loader is an explicit, hash-checked input
from an official runtime installation; sources and licences remain in the receipt.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile


def main():
    root=Path(__file__).resolve().parents[1]
    receipt=json.loads((root/'build-aux/community-runtime-windows.json').read_text())
    p=argparse.ArgumentParser()
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--loader',type=Path,required=True)
    p.add_argument('--site-packages',type=Path)
    args=p.parse_args()
    if args.out.exists(): raise FileExistsError(args.out)
    loader=receipt['files']['vulkan-1.dll']
    if hashlib.sha256(args.loader.read_bytes()).hexdigest()!=loader['sha256']:
        raise ValueError('Vulkan loader differs from the reviewed official binary')
    pending={}
    if args.site_packages:
        for name in receipt['files']:
            if name=='vulkan-1.dll':continue
            folder='onnxruntime/capi' if name.startswith('onnxruntime') and 'webgpu' not in name else 'onnxruntime_ep_webgpu'
            pending[name]=(args.site_packages/folder/name).read_bytes()
    else:
        for row in receipt['source_wheels'].values():
            request=urllib.request.Request(row['url'],headers={'User-Agent':'Scriba build tools'})
            with urllib.request.urlopen(request,timeout=60) as response:blob=response.read()
            if hashlib.sha256(blob).hexdigest()!=row['sha256']:raise ValueError('Official wheel hash differs')
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                for item in archive.infolist():
                    name=Path(item.filename).name
                    if name in receipt['files'] and name!='vulkan-1.dll':pending[name]=archive.read(item)
    pending['vulkan-1.dll']=args.loader.read_bytes()
    for name,record in receipt['files'].items():
        data=pending[name]
        if len(data)!=record['bytes'] or hashlib.sha256(data).hexdigest()!=record['sha256']:
            raise ValueError('Official DLL differs: '+name)
    args.out.mkdir(parents=True)
    for name,data in pending.items():(args.out/name).write_bytes(data)
    shutil.copy2(root/'build-aux/community-runtime-windows.json',args.out/'community-runtime-windows.json')
    print('Verified official community runtime:',sum(len(data) for data in pending.values()),'bytes')

if __name__=='__main__':main()
