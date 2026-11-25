"""Backend choice and a cancellable community child, without GUI or downloads."""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path
from . import models
from .task import Failure, check
from scriba.i18n import _


def select(requested, where=None, cancelled=None):
    if requested not in ('auto','community','legacy'):
        raise ValueError('Unknown diarization backend')
    if requested == 'legacy':
        return 'legacy', ''
    if models.community_set() is None:
        if requested == 'community':
            raise models.ModelError(_("Community voice models are not available yet."), 'unpublished catalogue')
        return 'legacy', ''
    try:
        models.community_files(where, cancelled)
        return 'community', ''
    except models.ModelError:
        if requested == 'community':
            raise
        return 'legacy', _("Community voice models are unavailable; using the previous voice method.")


def run(samples, where, speakers=None, requested='auto', progress=None, cancelled=None, force_cpu=False, *, named=()):
    from scriba.app import process
    from scriba.app.engines import bundle
    from scriba.core.segments import Turn
    import numpy as np
    check(cancelled)
    with tempfile.TemporaryDirectory(prefix='scriba-community-') as temp:
        root = Path(temp)
        np.save(root/'samples.npy',samples,allow_pickle=False)
        (root/'request.json').write_text(json.dumps(dict(models=str(where or models.folder()),
            speakers=speakers,requested=requested,force_cpu=force_cpu,named=named)),encoding='utf-8')
        args = ([sys.executable,'--community-worker',temp] if bundle.FROZEN else
                [sys.executable,'-m','scriba.app.engines.community_worker',temp])
        env = dict(os.environ, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
                   PYTHONDONTWRITEBYTECODE='1')
        if not bundle.FROZEN:
            from scriba import PACKAGE_DIR
            env['PYTHONPATH'] = str(PACKAGE_DIR.parent)
        # GPU driver and ORT cache paths are owned by this work, not by a recording.
        cache = Path(env.get('SCRIBA_COMMUNITY_CACHE',str(root/'cache')))
        for key, name in [('XDG_CACHE_HOME','xdg'),('MESA_SHADER_CACHE_DIR','mesa'),
                          ('__GL_SHADER_DISK_CACHE_PATH','nvidia'),('LOCALAPPDATA','local'),
                          ('APPDATA','roaming'),('TEMP','tmp'),('TMP','tmp')]:
            path = cache/name
            path.mkdir(parents=True,exist_ok=True)
            env[key] = str(path)
        def advanced(line):
            try:
                done,total = json.loads(line)
            except (ValueError,TypeError):
                return
            if progress:
                progress(done,total)
        result = process.run(args,env=env,on_stdout=advanced,cancelled=cancelled)
        check(cancelled)
        if result.returncode:
            error = json.loads((root/'error.json').read_text()) if (root/'error.json').exists() else {}
            if error.get('kind') == 'ModelError':
                raise models.ModelError(_("The voice model set is incompatible or incomplete."),error.get('detail',''))
            raise Failure(_("The community voice engine failed; the previous method was not substituted."),
                          error.get('detail',result.detail))
        data = json.loads((root/'result.json').read_text(encoding='utf-8'))
        data['turns'] = [Turn(**t) for t in data['turns']]
        data['exclusive'] = [Turn(**t) for t in data['exclusive']]
        return data


def proposals(lines, named, centroids, microphone='', *, fingerprints=None, teams_peer=""):
    from scriba.core import naming, segments
    found, reasons = naming.propose_with_voices(lines,named,centroids,microphone,fingerprints=fingerprints)
    # Keep core independent of gettext; localise the editable display proposals here.
    from dataclasses import replace
    for key, proposal in list(found.items()):
        if ', voice ' in proposal.name:
            name, number = proposal.name.rsplit(', voice ',1)
            found[key] = replace(proposal,name=_("{name}, voice {number}").format(name=name,number=number))
    if teams_peer and not found:
        remote = [voice for voice in segments.speakers_of(lines) if voice and voice != microphone]
        if len(remote) == 1:
            found[remote[0]] = naming.Proposal(teams_peer, 0, 0, "two-person")
    messages = {
        'multiple_voices': _("Several different voices are heard from the same Teams participant."),
        'merge_voices': _("Very similar voices share a Teams participant; you can give them the same name."),
        'multiple_tiles': _("One voice appears under several Teams participants; no name was proposed."),
    }
    return found,[messages[r] for r in reasons]
