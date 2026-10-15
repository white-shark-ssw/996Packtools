import json
import os
import shutil
import hashlib
import zipfile
from pathlib import Path
from dataclasses import dataclass, asdict, field
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog, colorchooser
import sys
from typing import Dict, List, Optional
import threading
import queue

from .constants import *
from .models import PackGroup, PackRule
from .store import DataStore
from .filters import *
from .files import md5_of_file, resource_path, config_base_dir
from .widgets import *


class ZipBuilder:
    def _pack_worker(self, export_dir: str, import_root_name: str, files: List[Dict]):
        export_path = Path(export_dir)
        export_path.mkdir(parents=True, exist_ok=True)
        temp_dir = export_path / f'_temp_build_{os.getpid()}_{int(datetime.now().timestamp())}'
        total_steps = max(1, len(files) * 2 + 4)
        step = 0

        try:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            temp_dir.mkdir(parents=True, exist_ok=True)
            step += 1
            self.pack_queue.put(('progress', step, total_steps, f'已创建临时目录：{temp_dir.name}'))

            copied_items = []
            for meta in files:
                dest = temp_dir / Path(meta['rel_path'])
                if meta.get('type') == 'dir':
                    dest.mkdir(parents=True, exist_ok=True)
                    copied_items.append((dest, 'dir'))
                    step += 1
                    self.pack_queue.put(('progress', step, total_steps, f'创建目录：{meta["rel_path"]}'))
                    continue

                dest.parent.mkdir(parents=True, exist_ok=True)
                if meta.get('virtual_content') is not None:
                    content = meta.get('virtual_content')
                    if isinstance(content, bytes):
                        dest.write_bytes(content)
                    else:
                        dest.write_text(str(content), encoding='utf-8')
                else:
                    src = Path(meta['abs_path'])
                    shutil.copy2(src, dest)
                copied_items.append((dest, 'file'))
                step += 1
                self.pack_queue.put(('progress', step, total_steps, f'复制文件：{meta["name"]}'))

            zip_path = export_path / f'{import_root_name}.zip'
            if zip_path.exists():
                zip_path.unlink()

            def write_zip_file():
                nonlocal step
                if zip_path.exists():
                    zip_path.unlink()

                def zip_dt(path_obj: Optional[Path] = None):
                    try:
                        ts = path_obj.stat().st_mtime if path_obj is not None and path_obj.exists() else datetime.now().timestamp()
                        dt = datetime.fromtimestamp(ts)
                    except Exception:
                        dt = datetime.now()
                    year = max(1980, dt.year)
                    return (year, dt.month, dt.day, dt.hour, dt.minute, dt.second)

                def make_dir_info(arc_dir: str, src_dir: Optional[Path] = None):
                    if not arc_dir.endswith('/'):
                        arc_dir += '/'
                    info = zipfile.ZipInfo(arc_dir, date_time=zip_dt(src_dir))
                    info.create_system = 0
                    info.external_attr = 0x10
                    info.compress_type = zipfile.ZIP_STORED
                    return info

                def make_file_info(arc_file: str, src_file: Path):
                    info = zipfile.ZipInfo(arc_file, date_time=zip_dt(src_file))
                    info.create_system = 0
                    info.external_attr = 0x80
                    info.compress_type = zipfile.ZIP_DEFLATED
                    return info

                def collect_parent_dirs(arc_file: str):
                    parts = arc_file.split('/')[:-1]
                    dirs = []
                    cur = ''
                    for part in parts:
                        if not part:
                            continue
                        cur = f'{cur}/{part}' if cur else part
                        dirs.append(cur + '/')
                    return dirs

                written_dirs = set()

                def write_dir(zf, arc_dir: str, src_dir: Optional[Path] = None, progress_name: str = ''):
                    if not arc_dir.endswith('/'):
                        arc_dir += '/'
                    if arc_dir in written_dirs:
                        return
                    zf.writestr(make_dir_info(arc_dir, src_dir), b'')
                    written_dirs.add(arc_dir)
                    if progress_name:
                        self.pack_queue.put(('progress', step, total_steps, f'压缩目录：{progress_name}'))

                with zipfile.ZipFile(zip_path, 'w') as zf:
                    sorted_items = sorted(copied_items, key=lambda item: (0 if item[1] == 'dir' else 1, item[0].as_posix()))
                    for p, kind in sorted_items:
                        arcname = (Path(import_root_name) / p.relative_to(temp_dir)).as_posix()
                        if kind == 'dir':
                            if not arcname.endswith('/'):
                                arcname += '/'
                            # v108: 使用 Windows/WinRAR 风格目录项，并补齐父级目录，提升上传平台兼容性。
                            for d in collect_parent_dirs(arcname.rstrip('/')):
                                write_dir(zf, d)
                            write_dir(zf, arcname, p)
                            step += 1
                            self.pack_queue.put(('progress', step, total_steps, f'压缩目录：{p.name}'))
                        else:
                            # v108: 文件写入前显式写入父级目录 entry，避免平台无法解析深层路径。
                            for d in collect_parent_dirs(arcname):
                                write_dir(zf, d)
                            zf.writestr(make_file_info(arcname, p), p.read_bytes())
                            step += 1
                            self.pack_queue.put(('progress', step, total_steps, f'压缩文件：{p.name}'))

            write_zip_file()

            shutil.rmtree(temp_dir, ignore_errors=True)
            step += 1
            self.pack_queue.put(('progress', step, total_steps, '已清理临时目录'))
            self.pack_queue.put(('done', str(zip_path)))
        except Exception as e:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
            self.pack_queue.put(('error', str(e)))

