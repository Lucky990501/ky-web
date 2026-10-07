"""Standard-library-only scratch fixture for Revision identity unit tests.

Same directory/containment rules as the WeChat integration fixture; importing
runtime-only tests must not first import the unrelated Skill business program.
"""
from pathlib import Path
import shutil
import uuid


class Scratch:
    def __init__(self, *args, **kwargs):
        self.base = Path(__file__).resolve().parents[2] / '.codex-wht/test-workspaces'
        self.base.mkdir(parents=True, exist_ok=True)
        self.root = self.base / uuid.uuid4().hex
        self.root.mkdir(mode=0o755)
        self.name = str(self.root)
    def cleanup(self):
        if self.root.exists():
            assert self.root.resolve().parent == self.base.resolve() and not self.root.is_symlink()
            shutil.rmtree(self.root)
    def __enter__(self): return self.name
    def __exit__(self, *args): self.cleanup()
