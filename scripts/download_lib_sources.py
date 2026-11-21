#!/usr/bin/env python3
"""下载 11 个库的源码到 data/<lib>_source（与 data/spacy_source 同构：包目录内容）。

约定：
  * 与 venv 已装版本一致（版本取 importlib.metadata，剥掉 torch 的 +cpu local 段）；
  * 9 个库走 PyPI sdist（该版本的源码包）；paddle/torch 无 sdist，走 GitHub tag；
  * GitHub 走代理（PyPI files.pythonhosted.org 可直连，实测 200）；
  * 解包后只保留顶层一个包目录的内容平铺进 data/<lib>_source（如 dask-2025.12.0/dask/*）。

用法: uv run python scripts/download_lib_sources.py [lib ...]   # 缺省=全部 11 个
"""

import io
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from importlib.metadata import version

import httpx

ROOT = "/home/lisy/Projects/respfuzzer"
DATA = f"{ROOT}/data"
PROXY = "http://192.168.1.11:20172"  # 仅 GitHub codeload 需要

# lib -> (PyPI dist 名, GitHub repo)。repo 仅在无 sdist 时用。
PKGS = {
    "nltk": ("nltk", None),
    "numpy": ("numpy", None),
    "pandas": ("pandas", None),
    "scipy": ("scipy", None),
    "sklearn": ("scikit-learn", None),
    "spacy": ("spacy", None),
}


def fetch(url: str, use_proxy: bool) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "respfuzzer-src-dl/1.0"})
    if use_proxy:
        handler = urllib.request.ProxyHandler({"https": PROXY, "http": PROXY})
        opener = urllib.request.build_opener(handler)
        with opener.open(req, timeout=600) as resp:
            return resp.read()
    with urllib.request.urlopen(req, timeout=600) as resp:
        return resp.read()


def sdist_url(dist: str, ver: str) -> str | None:
    # 必须取已装版本的 sdist：/pypi/{dist}/json 返回 latest，与 venv 不一致
    j = httpx.get(f"https://pypi.org/pypi/{dist}/{ver}/json", timeout=30).json()
    for u in j.get("urls", []):
        if u["packagetype"] == "sdist":
            return u["url"]
    return None


def extract_and_flatten(blob: bytes, dest: str, pkg_dir: str) -> str:
    """解 tar.gz，找到含 __init__.py 的目标包目录，把其内容平铺到 dest。"""
    with tempfile.TemporaryDirectory() as td:
        with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
            tf.extractall(td, filter="data")
        top = os.listdir(td)
        if len(top) != 1:
            raise RuntimeError(f"unexpected top-level entries: {top}")
        root = os.path.join(td, top[0])
        candidates = [
            cur
            for cur, _dirs, files in os.walk(root)
            if os.path.basename(cur) == pkg_dir and "__init__.py" in files
        ]
        if not candidates:
            raise RuntimeError(f"package dir {pkg_dir!r} not found in archive")
        # 取最浅的一层（避免 tests/ 下的同名包目录）
        src = min(candidates, key=lambda p: p.count(os.sep))
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        return top[0]


def main() -> int:
    os.makedirs(DATA, exist_ok=True)
    only = sys.argv[1:] or list(PKGS)
    for lib in only:
        dist, repo = PKGS[lib]
        # torch 装的是 2.9.1+cpu，PyPI/GitHub tag 都只要 base 版本
        ver = version(dist).split("+")[0]
        dest = f"{DATA}/{lib}_source"
        if os.path.isdir(dest) and os.listdir(dest):
            print(f"[skip ] {lib}: {dest} already exists", flush=True)
            continue
        try:
            if repo is None:
                url = sdist_url(dist, ver)
                if not url:
                    print(f"[FAIL ] {lib}: no sdist for {ver} on PyPI", flush=True)
                    continue
                print(f"[get  ] {lib} {ver} <- {url}", flush=True)
                blob = fetch(url, use_proxy=False)
            else:
                tag = ver if ver.startswith("0.") else f"v{ver}"
                url = f"https://codeload.github.com/{repo}/tar.gz/refs/tags/{tag}"
                print(f"[get  ] {lib} {ver} <- {url} (via proxy)", flush=True)
                blob = fetch(url, use_proxy=True)
            top = extract_and_flatten(blob, dest, lib)
            n = sum(len(fs) for _c, _d, fs in os.walk(dest))
            print(f"[ ok  ] {lib}: {top} -> {dest} ({n} files)", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL ] {lib}: {type(exc).__name__}: {exc}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
