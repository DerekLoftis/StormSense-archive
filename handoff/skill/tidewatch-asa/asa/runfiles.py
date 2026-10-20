"""Copy of tidewatch-archive/archiver/runfiles.py (kept here so the ASA has no import path into the archive).

Read archived run folders whether their files are loose or packed in run.tar (pack_runs.py).

    from runfiles import glob, read, open_text
    for name in glob(run_dir, "*_f36_MLLW.csv"):     # file names, loose or packed
        df = pd.read_csv(open_text(run_dir, name))   # .gz files are decompressed
    raw = read(run_dir, "SWPT_2018_f36.pypickle.gz") # bytes as archived

    run_dirs_with(root, "*_f36_MLLW.csv")            # run folders holding a match, oldest first

A loose file wins over a packed one of the same name.
"""
import fnmatch
import gzip
import io
import os
import re
import tarfile

TAR = "run.tar"
_index = {}  # run.tar path -> (mtime, {name: TarInfo})


def _members(run):
    path = os.path.join(run, TAR)
    if not os.path.exists(path):
        return {}
    mt = os.path.getmtime(path)
    if path not in _index or _index[path][0] != mt:
        with tarfile.open(path) as t:
            _index[path] = (mt, {m.name: m for m in t.getmembers() if m.isfile()})
    return _index[path][1]


def iter_files(run):
    """Every file name in the run (relative), loose or packed."""
    names = set(_members(run))
    for dirpath, _, filenames in os.walk(run):
        for f in filenames:
            rel = os.path.relpath(os.path.join(dirpath, f), run)
            if rel != TAR:
                names.add(rel)
    return sorted(names)


def glob(run, pattern):
    return [n for n in iter_files(run) if fnmatch.fnmatch(os.path.basename(n), pattern)]


def read(run, name):
    loose = os.path.join(run, name)
    if os.path.exists(loose):
        return open(loose, "rb").read()
    with tarfile.open(os.path.join(run, TAR)) as t:
        return t.extractfile(name).read()


def open_text(run, name):
    raw = read(run, name)
    if name.endswith(".gz"):
        raw = gzip.decompress(raw)
    return io.StringIO(raw.decode("utf-8", "replace"))


_RUN = re.compile(r"^\d{4}-\d{2}-\d{2}[T_]\d{2}(\d{2})?Z")


def run_dirs_with(root, pattern):
    """Run folders under root (any depth) holding a file matching pattern, oldest first."""
    out = []
    for dirpath, dirnames, _ in os.walk(root):
        if _RUN.match(os.path.basename(dirpath)):
            dirnames[:] = []
            if glob(dirpath, pattern):
                out.append(dirpath)
    return sorted(out, key=lambda d: (os.path.basename(d).replace("_", "T")[:15], d))
