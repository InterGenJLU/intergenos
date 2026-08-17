import importlib.util, gi
gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Gtk, Adw
spec=importlib.util.spec_from_file_location('welcomer_mod','/mnt/intergenos/assets/intergen-welcome/intergen-welcome.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
# PREVIEW-ONLY: neuter the system-mutating handlers so clicking options
# can't change the operator's live theme / extensions / ~/.bashrc.
m.apply_theme=lambda *a,**k: print('[preview] apply_theme stubbed', flush=True)
m.apply_prompt=lambda *a,**k: print('[preview] apply_prompt stubbed', flush=True)
m.set_enabled_extensions=lambda *a,**k: print('[preview] set_enabled_extensions stubbed', flush=True)
# PREVIEW-ONLY: simulate the model-install stream instead of pkexec'ing a real
# 4-5 GB download, so the button's full UX (progress -> ready) is visible safely.
from gi.repository import GLib as _GLib
def _fake_setup(on_line, on_done, tier=None, **_ignored):
    steps = ['Detecting hardware...', '  RAM: 15.0 GB / Tier: 2 (CPU-only -> 2B)',
             'Recommended model: Qwen3 2B  (~4.2 GB, Hugging Face)',
             'License: Tongyi-Qianwen — review at the model card',
             'Downloading model...  12%', 'Downloading model...  58%',
             'Downloading model...  97%', 'Web auth token generated.']
    state = {'i': 0}
    def tick():
        if state['i'] < len(steps):
            on_line(steps[state['i']]); state['i'] += 1; return True
        on_done(True); return False
    _GLib.timeout_add(700, tick)
m._launch_intergen_setup=_fake_setup
print('[preview] _launch_intergen_setup stubbed (simulated stream, no real download)', flush=True)
m.main()
