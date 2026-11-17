"""Windows-only Vulkan experiment, with explicit physical Dawn selection."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from unittest.mock import patch


def configure_cache(root):
    """Set process-local paths before importing ORT or loading GPU drivers.

    Windows driver Known Folder lookups may ignore environment overrides.
    These settings are not proof of confinement: audit real caches separately.
    """
    cache = root.resolve()
    for key, folder in {
        'XDG_CACHE_HOME': 'xdg', 'MESA_SHADER_CACHE_DIR': 'mesa',
        '__GL_SHADER_DISK_CACHE_PATH': 'nvidia', 'LOCALAPPDATA': 'local',
        'APPDATA': 'roaming', 'TEMP': 'tmp', 'TMP': 'tmp',
    }.items():
        path = cache / folder
        path.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(path)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    return {key: os.environ[key] for key in (
        'XDG_CACHE_HOME', 'MESA_SHADER_CACHE_DIR', '__GL_SHADER_DISK_CACHE_PATH',
        'LOCALAPPDATA', 'APPDATA', 'TEMP', 'TMP')}


def vulkan_options(index):
    if index < 0:
        raise ValueError('Physical adapter index must be nonnegative')
    return {'dawnBackendType': 'Vulkan', 'adapterIndex': str(index),
            'enableGraphCapture': '0'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--adapter-index', required=True, type=int)
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--cache-root', required=True, type=Path)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--plugin-library', type=Path)
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--driver-select', choices=['nvidia', 'intel'])
    own, rest = parser.parse_known_args()
    cache = configure_cache(own.cache_root)
    if own.driver_select:
        os.environ['VK_LOADER_DRIVERS_SELECT'] = {
            'nvidia': 'nv-vk64.json', 'intel': 'igvk64.json'}[own.driver_select]
        os.environ['VK_LOADER_DEBUG'] = 'warn,driver'
    import community_experiments as experiments
    from community_accelerators import Session, compare
    from pyannote_reference import write_json
    import onnxruntime as ort
    import onnxruntime_ep_webgpu as webgpu

    library = str(own.plugin_library.resolve()) if own.plugin_library else webgpu.get_library_path()
    ort.register_execution_provider_library('vulkan_windows_lab', library)
    devices = [x for x in ort.get_ep_devices() if x.ep_name == webgpu.get_ep_name()]
    if not devices:
        raise RuntimeError('No WebGPU EP device found')
    args = experiments.parser().parse_args(['run', *rest, '--provider', 'c'])
    provider_options = vulkan_options(own.adapter_index)
    print(json.dumps({'provider_options': provider_options, 'cache': cache}), flush=True)

    class VulkanSession:
        def __init__(self, path, threads):
            options = ort.SessionOptions()
            options.intra_op_num_threads = threads
            options.inter_op_num_threads = 1
            options.log_severity_level = 0 if own.verbose else 2
            options.log_verbosity_level = 1 if own.verbose else 0
            options.enable_profiling = args.profile
            options.profile_file_prefix = str(args.out / Path(path).stem)
            options.add_provider_for_devices([devices[0]], provider_options)
            self.native = ort.InferenceSession(str(path), sess_options=options)
            self.native.disable_fallback()
            if webgpu.get_ep_name() not in self.native.get_providers():
                raise RuntimeError('WebGPU provider was not activated')

        def __enter__(self):
            return self

        def run(self, *values):
            return self.native.run(*values)

        def __exit__(self, *exc):
            if args.profile:
                print('WebGPU profile:', self.native.end_profiling(), flush=True)
            self.native = None

    def factory(path, threads, *unused):
        if Path(path).stem == 'segmentation' and not own.full:
            return Session(path, 'ort', 'CPU', threads)
        return VulkanSession(path, threads)

    with patch.object(experiments, 'c_session', factory):
        experiments.run(args)
    write_json(args.out / 'vulkan-options.json', {
        'provider_options': provider_options, 'cache': cache, 'full': own.full})
    if own.reference:
        gate = compare(own.reference, args.out)
        write_json(args.out / 'gate.json', gate)
        print(json.dumps(gate), flush=True)


if __name__ == '__main__':
    main()
