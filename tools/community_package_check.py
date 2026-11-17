"""Measure a real packaged worker on public audio, isolated profiles and cache.

The model set remains a development input until public hosting is approved.
This command never installs a package or opens a user recording. Each run
owns a new output directory and records PCM/model hashes and provenance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parent))


def main():
    import numpy as np
    from scriba.app.engines import audio
    from pyannote_load import MeasurementLoad
    p=argparse.ArgumentParser()
    p.add_argument('--executable',type=Path)
    p.add_argument('--flatpak-dir',type=Path)
    p.add_argument('--models',type=Path,required=True)
    p.add_argument('--wav',type=Path,required=True)
    p.add_argument('--seconds',type=float,default=0)
    p.add_argument('--cpu',action='store_true')
    p.add_argument('--cancel-after',type=float,default=0)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--reference',type=Path)
    args=p.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    samples=audio.read(args.wav)
    if args.seconds: samples=samples[:round(args.seconds*16000)]
    np.save(out/'samples.npy',samples,allow_pickle=False)
    models=args.models.resolve()
    (out/'request.json').write_text(json.dumps(dict(models=str(models),speakers=None,
        requested='community',force_cpu=args.cpu)),encoding='utf-8')
    env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',
             PYTHONDONTWRITEBYTECODE='1',SCRIBA_DEV_COMMUNITY='1',SCRIBA_DEV_COMMUNITY_DIR=str(models))
    for key,name in [('XDG_CONFIG_HOME','config'),('XDG_DATA_HOME','data'),('XDG_STATE_HOME','state'),
                     ('XDG_CACHE_HOME','cache'),('MESA_SHADER_CACHE_DIR','cache/mesa'),
                     ('__GL_SHADER_DISK_CACHE_PATH','cache/nvidia'),('LOCALAPPDATA','local'),
                     ('APPDATA','roaming'),('TEMP','tmp'),('TMP','tmp')]:
        folder=out/name;folder.mkdir(parents=True,exist_ok=True);env[key]=str(folder)
    if args.executable:
        command=[str(args.executable.resolve()),'--community-worker',str(out)]
    elif args.flatpak_dir:
        command=['flatpak','build','--runtime','--device=dri',str(args.flatpak_dir.resolve()),
                 '/app/bin/scriba','--community-worker',str(out)]
        for key in ('SCRIBA_DEV_COMMUNITY','SCRIBA_DEV_COMMUNITY_DIR','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS',
                    'MKL_NUM_THREADS','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_STATE_HOME','XDG_CACHE_HOME',
                    'MESA_SHADER_CACHE_DIR','__GL_SHADER_DISK_CACHE_PATH','LOCALAPPDATA','APPDATA','TEMP','TMP'):
            command.insert(3,'--env='+key+'='+env[key])
    else:
        env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
        command=[sys.executable,'-m','scriba.app.engines.community_worker',str(out)]
    peak_mib = 0.0
    def memory(child):
        if sys.platform != 'win32':
            return 0.0
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD),
                      *[(str(i),ctypes.c_size_t) for i in range(8)]]
        api=ctypes.WinDLL('psapi')
        api.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
        api.GetProcessMemoryInfo.restype=wintypes.BOOL
        counters=Counters();counters.cb=ctypes.sizeof(counters)
        if api.GetProcessMemoryInfo(int(child._handle),ctypes.byref(counters),counters.cb):
            return getattr(counters,'0')/2**20
        return 0.0
    with MeasurementLoad(idle_limit=30) as load:
        start=time.perf_counter()
        with (out/'worker.log').open('xb') as log:
            child=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if args.cancel_after:
                try: child.wait(args.cancel_after)
                except subprocess.TimeoutExpired:
                    child.terminate();child.wait(10)
            else:
                while child.poll() is None:
                    peak_mib=max(peak_mib,memory(child))
                    try:child.wait(.2)
                    except subprocess.TimeoutExpired:pass
        seconds=time.perf_counter()-start
    result=json.loads((out/'result.json').read_text(encoding='utf-8')) if (out/'result.json').exists() else {}
    report=dict(command=command,exit=child.returncode,seconds=seconds,duration=len(samples)/16000,
        pcm_sha256=hashlib.sha256(samples.tobytes()).hexdigest(),provenance=result.get('provenance'),
        speakers=result.get('speakers'),load=load.report,cancel_requested=bool(args.cancel_after),
        passed=bool(result) and child.returncode==0, peak_worker_mib=peak_mib or None)
    if args.cancel_after: report['passed']=child.returncode!=0 and not result
    if args.reference and result:
        from scriba.core.segments import Turn
        from pyannote_benchmark import rttm
        for name,key in [('normal','turns'),('exclusive','exclusive')]:
            target=out/(name+'.rttm')
            rttm(target,'stress-2h',[Turn(**t) for t in result[key]])
            reference=args.reference/(name+'.rttm')
            # Same file ID and LF convention; no relabelling or time tolerance.
            report[name+'_identical']=target.read_text()==reference.read_text()
        report['passed'] &= report['normal_identical'] and report['exclusive_identical']
    with (out/'measurement.json').open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2)
    print(json.dumps(report))
    return 0 if report['passed'] else 1

if __name__=='__main__':sys.exit(main())
