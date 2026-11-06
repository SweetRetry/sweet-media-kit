#!/usr/bin/env python3
"""Suno music via Tu-zi. The gateway forwards Suno's own fields unchanged.

  suno.py submit --mv chirp-hawk --tags "<style>" [--prompt "<lyrics or [Section] tags>"]
                 [--title T] [--instrumental] [--description "<idea>"] [--out DIR] [--dry-run]
  suno.py fetch <task_id> [--out DIR]

One submit is one billed call (suno_music) and returns two clips. --prompt takes lyrics or
[Section: ...] structure tags; --description lets Suno write them instead (use one of the two).
mv values measured on the gateway: chirp-hawk = v6, chirp-crow = v5, chirp-bluejay = v4.5+,
chirp-auk = v4.5. The gateway does not check mv: an unknown value is billed and silently
falls back to its default model. Token: $TUZI_API_KEY, else ~/.config/tuzi/api_key.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gen  # noqa: E402

KNOWN_MV = {"chirp-hawk", "chirp-crow", "chirp-bluejay", "chirp-auk", "chirp-v4", "chirp-v3-5"}


def wait(task):
    while True:
        d = gen.request("GET", f"/suno/fetch/{task}")["data"]
        print(task, d.get("status"), d.get("progress") or "", file=sys.stderr)
        if d.get("status") in ("SUCCESS", "FAILURE"):
            return d
        time.sleep(15)


def download(task, d, out):
    out = Path(out) / task
    out.mkdir(parents=True, exist_ok=True)
    (out / "response.json").write_text(json.dumps(d, ensure_ascii=False, indent=1))
    for i, c in enumerate(d.get("data") or [], 1):
        if c.get("audio_url"):
            path = out / f"clip_{i}.mp3"
            path.write_bytes(gen.fetch_url(c["audio_url"]))
            print(path, c.get("model_name"), c.get("major_model_version"),
                  f"{(c.get('metadata') or {}).get('duration', 0):.1f}s")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("submit", help="生成 2 首（1 次计费）")
    p.add_argument("--mv", required=True)
    p.add_argument("--tags", required=True, help="风格、配器、BPM")
    p.add_argument("--prompt", default="")
    p.add_argument("--title", default="")
    p.add_argument("--instrumental", action="store_true")
    p.add_argument("--description", default="", help="gpt_description_prompt")
    p.add_argument("--out", default="suno")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("fetch", help="查询并下载已有任务（不计费）")
    p.add_argument("task")
    p.add_argument("--out", default="suno")
    a = ap.parse_args()
    try:
        if a.cmd == "submit":
            if a.mv not in KNOWN_MV:
                sys.exit(f"未知 mv {a.mv}：网关不校验，会按默认模型计费出歌。已知值 {sorted(KNOWN_MV)}")
            body = {"mv": a.mv, "tags": a.tags, "prompt": a.prompt, "title": a.title,
                    "make_instrumental": a.instrumental, "gpt_description_prompt": a.description}
            if a.dry_run:
                print("POST /suno/submit/music\n" + json.dumps(body, ensure_ascii=False, indent=1))
                return
            task = gen.request("POST", "/suno/submit/music", json.dumps(body).encode(), "application/json")["data"]
            print("task", task, file=sys.stderr)
        else:
            task = a.task
        d = wait(task)
        if d.get("status") != "SUCCESS":
            sys.exit(f"{task} 失败：{d.get('fail_reason')}")
        download(task, d, a.out)
    except gen.Fail as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
