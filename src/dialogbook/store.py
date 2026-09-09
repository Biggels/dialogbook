"""Dialogs on disk: one flat directory of `.ipynb` files.

The file *is* the dialog. Every save writes valid nbformat, so any dialog stays openable
in JupyterLab or VS Code — the fallback that makes this safe to build on.
"""
import asyncio
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from aidialog.dialog import Dialog, snote
from aidialog.ipynb import read_ipynb, write_ipynb

log = logging.getLogger('dialogbook.store')

MAX_SLUG = 60


def slugify(title):
    "A filesystem-safe stem derived from `title`; never empty, never a Windows reserved name."
    s = unicodedata.normalize('NFKD', title or '').encode('ascii', 'ignore').decode()
    s = re.sub(r'[^\w\s-]', '', s).strip().lower()
    s = re.sub(r'[\s_-]+', '-', s).strip('-')[:MAX_SLUG].rstrip('-')
    # CON, PRN, AUX, NUL, COM1-9, LPT1-9 are unusable as filenames on Windows.
    if not s or re.fullmatch(r'(con|prn|aux|nul|com\d|lpt\d)', s): s = f'dialog-{s}'.rstrip('-')
    return s


@dataclass(frozen=True)
class DialogInfo:
    "What the index page needs, without parsing the whole notebook."
    id: str      # the file stem, and the id in `/d/{id}`
    title: str
    path: Path
    modified: datetime
    size: int

    @classmethod
    def from_path(cls, p):
        st = p.stat()
        return cls(id=p.stem, title=p.stem.replace('-', ' '), path=p,
                   modified=datetime.fromtimestamp(st.st_mtime), size=st.st_size)


class Store:
    "Load, save, list and create dialogs in one flat workspace directory."

    def __init__(self, workspace):
        self.workspace = Path(workspace).expanduser()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def path_for(self, id): return self.workspace/f'{id}.ipynb'

    def list(self):
        "Every dialog in the workspace, newest first."
        return sorted((DialogInfo.from_path(p) for p in self.workspace.glob('*.ipynb')),
                      key=lambda d: d.modified, reverse=True)

    def exists(self, id): return self.path_for(id).exists()

    def _free_id(self, title):
        "`slugify(title)`, suffixed `-2`, `-3`, … until it names no existing file."
        base = slugify(title)
        if not self.exists(base): return base
        return next(f'{base}-{n}' for n in range(2, 10_000) if not self.exists(f'{base}-{n}'))

    def create(self, title='Untitled'):
        "A new dialog on disk, seeded with a title note so it is never a blank file."
        id = self._free_id(title)
        dlg = Dialog(name=id)
        dlg.mk_message(f'# {title}', msg_type=snote)
        self.save(dlg)
        return dlg

    def load(self, id):
        "The dialog named `id`, or None if there is no such file."
        p = self.path_for(id)
        if not p.exists(): return None
        dlg = read_ipynb(p)
        if dlg is not None: dlg.name = id
        return dlg

    def save(self, dlg):
        """Atomic write: temp file in the same directory, then `os.replace`.

        Deliberately *not* `write_ipynb(dlg, path)`. That routes through fastcore's
        `atomic_save`, which finishes with `Path.rename` — and on Windows `rename` raises
        `FileExistsError` when the destination exists, so it can create a dialog but
        never save one again. `os.replace` is atomic and overwrites on every platform.
        See docs/notes-upstream.md.
        """
        p = self.path_for(dlg.name)
        txt = write_ipynb(dlg)  # fname=None returns the notebook JSON rather than writing
        tmp = p.with_name(f'.{p.name}.{os.getpid()}.tmp')
        try:
            tmp.write_text(txt, encoding='utf-8')
            os.replace(tmp, p)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        dlg.path_ = p
        dlg.mtime_ = p.stat().st_mtime
        return p

    def delete(self, id):
        self.path_for(id).unlink(missing_ok=True)


class OpenDialogs:
    """Dialogs currently open in the app, kept in memory and saved on a debounce.

    A dialog is edited far more often than it needs to hit the disk: typing a cell,
    running it, streaming a reply. Each mutation calls `touch`, which coalesces into one
    atomic write shortly after the edits stop. `flush` forces the write out — call it
    before anything that reads the file, and on shutdown.
    """

    def __init__(self, store, debounce=0.75):
        self.store, self.debounce = store, debounce
        self._open = {}      # id -> Dialog
        self._tasks = {}     # id -> pending save task

    def __contains__(self, id): return id in self._open

    @property
    def ids(self): return set(self._open)

    def get(self, id):
        "The open dialog for `id`, loading it from disk on first access. None if no such file."
        if (dlg := self._open.get(id)) is not None: return dlg
        dlg = self.store.load(id)
        if dlg is not None: self._open[id] = dlg
        return dlg

    def touch(self, id):
        "Mark `id` dirty and schedule a save. Later touches push the write back."
        if (t := self._tasks.pop(id, None)) is not None: t.cancel()
        if self.debounce <= 0: return self._write(id)
        try:
            self._tasks[id] = asyncio.create_task(self._save_soon(id))
        except RuntimeError:      # no running loop (sync tests); write through instead
            self._write(id)

    async def _save_soon(self, id):
        try:
            await asyncio.sleep(self.debounce)
            self._write(id)
        except asyncio.CancelledError:
            raise
        finally:
            self._tasks.pop(id, None)

    def _write(self, id):
        if (dlg := self._open.get(id)) is None: return
        try: self.store.save(dlg)
        except OSError as e: log.error('saving dialog %s: %s', id, e)

    async def flush(self, id=None):
        "Write out pending saves now, for one dialog or all of them."
        for i in ([id] if id is not None else list(self._tasks)):
            if (t := self._tasks.pop(i, None)) is not None: t.cancel()
            self._write(i)

    async def close(self, id):
        "Flush and drop from memory. The kernel is closed separately, by the registry."
        await self.flush(id)
        self._open.pop(id, None)
