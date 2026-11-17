"""Prepare an attributed HF upload directory. Never upload or overwrite."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


def main():
    from scriba.app.community_recipe import RECIPE
    from scriba.app.engines.community import validated_files
    p=argparse.ArgumentParser()
    p.add_argument('--models',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.out.exists(): raise FileExistsError(args.out)
    validated_files({'recipe':RECIPE,**{name:args.models/name for name in RECIPE['files']}})
    args.out.mkdir(parents=True)
    for name in RECIPE['files']: shutil.copy2(args.models/name,args.out/name)
    (args.out/'manifest.json').write_text(json.dumps(RECIPE,indent=2)+'\n',encoding='utf-8')
    root=Path(__file__).resolve().parents[1]
    for name in ('community-CC-BY-4.0.txt','community-models-NOTICE.txt','pyannote-MIT.txt','VBx-Apache-2.0.txt'):
        shutil.copy2(root/'LICENSES'/name,args.out/name)
    readme="""---
license: cc-by-4.0
language:
- multilingual
library_name: onnxruntime
---
# Scriba community-1 conversions

Prepared for daniel-g-c/scriba-models. Not published by this tool.
Upstream: https://huggingface.co/pyannote/speaker-diarization-community-1
Revision: 3533c8cf8e369892e6b79ff1bf80f7b0286a54ee

Attribution: pyannote / CNRS, WeSpeaker (VoxCeleb-trained embedding),
BUT Speech@FIT (PLDA and xvector transform). Weights: CC BY 4.0.
Ported code: MIT (pyannote.audio 4.0.0), Apache-2.0 (VBx).
All source and converted hashes, versions and numerical checks: manifest.json.

Changes: ONNX FP32, opset 17, fixed 10 s audio, dynamic batch,
checkpoint Sinc filters materialised, shared ResNet backbone with three weighted masks.
PLDA NPZ files unchanged. No PyTorch checkpoint is included.
Segmentation uses CPU; embedding can use WebGPU with Vulkan, with CPU fallback.
Normal overlapping turns are preserved; exclusive turns assign transcript text.

Please cite upstream work: Plaquet and Bredin (powerset segmentation),
Wang et al. (WeSpeaker), Landini et al. (VBx). See upstream model card for citations.
No endorsement by upstream authors is implied.

Published conversion: https://huggingface.co/daniel-g-c/scriba-models
Pinned production revision: defe86adf9df365e3ebd70943e5608147d40fe8b
Scriba verifies all four downloads before activating the set.

| File | Bytes | SHA-256 |
| --- | ---: | --- |
"""
    for name,record in RECIPE['files'].items(): readme+=f"| {name} | {record['bytes']} | {record['sha256']} |\n"
    (args.out/'README.md').write_text(readme,encoding='utf-8')
    inventory={p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
               for p in sorted(args.out.iterdir()) if p.is_file()}
    (args.out/'SHA256.json').write_text(json.dumps(inventory,indent=2)+'\n',encoding='utf-8')
    print(sum(r['bytes'] for r in RECIPE['files'].values()),'model bytes; no upload performed')

if __name__=='__main__': main()
