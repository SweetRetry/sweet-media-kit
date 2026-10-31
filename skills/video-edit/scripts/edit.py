#!/usr/bin/env python3
"""video-edit：一批素材剪成一条成片。只依赖标准库和 ffmpeg、ffprobe，台词另需 whisper-cli。

  edit.py init <素材目录> --lang ja|zh|en|…|none [--whisper-model P] [--vad-model P]
  edit.py analyze [--clip ID …] [--threshold T] [--stems] [--force]
                                                           事实层 -> clips.json，补 log.json 骨架。--stems 分离人声和伴奏
  edit.py sheet <clip|视频> --start S --dur D [--fps 4]      局部逐帧触点图
  edit.py sheet <clip|视频> --at T                           单帧大图
  edit.py schema [log|edl]                                   日志和 EDL 的字段
  edit.py check                                              逐层检查 -> check.json、edl.md
  edit.py inspect                                            给挨着选段边缘、只粗看过的崩坏出 6fps 局部图
  edit.py gaps                                               edl.json 的缺镜头 -> reshoots.md 补拍单和参考帧
  edit.py render [--force]                                   edl.json -> cuts/vN.mp4、vN.json、成片触点图

工作区默认 <素材目录>/.video-edit/。不传 --work 时从当前目录向上找 project.json。
init 可以重跑：重新扫描素材目录，没传的设置保持原样。
"""
import argparse
from schema import SCHEMA
from analyze import cmd_analyze, cmd_init, cmd_sheet
from check import cmd_check, cmd_gaps, cmd_inspect
from render import cmd_render

def cmd_schema(a):
    print("\n\n".join(SCHEMA[k] for k in ([a.which] if a.which else SCHEMA)))

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    def add(name, fn):
        p = sp.add_parser(name)
        p.add_argument("--work")
        p.set_defaults(fn=fn)
        return p
    p = add("init", cmd_init)
    p.add_argument("src")
    p.add_argument("--lang")
    p.add_argument("--whisper-model")
    p.add_argument("--vad-model")
    p = add("analyze", cmd_analyze)
    p.add_argument("--clip", action="append")
    p.add_argument("--threshold", type=float)
    p.add_argument("--stems", action="store_true")
    p.add_argument("--force", action="store_true")
    p = add("sheet", cmd_sheet)
    p.add_argument("target")
    p.add_argument("--start", type=float, default=0.0)
    p.add_argument("--dur", type=float)
    p.add_argument("--fps", type=float, default=4)
    p.add_argument("--at", type=float)
    p.add_argument("--width", type=int)
    p = add("schema", cmd_schema)
    p.add_argument("which", nargs="?", choices=list(SCHEMA))
    add("check", cmd_check)
    add("gaps", cmd_gaps)
    add("inspect", cmd_inspect)
    p = add("render", cmd_render)
    p.add_argument("--force", action="store_true")
    a = ap.parse_args()
    a.fn(a)

if __name__ == "__main__":
    main()
