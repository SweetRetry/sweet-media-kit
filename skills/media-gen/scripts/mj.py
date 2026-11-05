#!/usr/bin/env python3
"""Midjourney V8.2 via Tu-zi (midjourney-proxy API). Standard library only.

  mj.py imagine "<prompt>" [--ref PATH_OR_URL ...] [--out DIR] [--dry-run]
  mj.py action <task_id> <U1-U4|V1-V4|reroll|customId|button label> [--out DIR]
  mj.py fetch <task_id> [--out DIR]

Every imagine and action submit is one billed call. --dry-run validates and prints
the payload without network access. Token: $TUZI_API_KEY, else ~/.config/tuzi/api_key.
"""

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("TUZI_BASE_URL", "https://api.tu-zi.com").rstrip("/")
KEY_FILE = Path.home() / ".config" / "tuzi" / "api_key"
VERSION = "8.2"
PRICE_PER_CALL = 0.35
POLL_SECONDS = 3
TIMEOUT_SECONDS = 600

# Flags V8.2 rejects or this API cannot serve, with the replacement to use.
REJECTED = {
    "--q": "V8.x 无 --q；要更高分辨率用 --hd",
    "--quality": "V8.x 无 --quality；要更高分辨率用 --hd",
    "--oref": "V8.x 已移除 Omni Reference；参考图用 --ref 垫图或 --sref",
    "--ow": "V8.x 已移除 --ow",
    "--cref": "--cref 只在 V6；角色参考用 --ref 垫图",
    "--cw": "--cw 只在 V6",
    "--stop": "--stop 只在 V6 及更早",
    "--turbo": "V8.x 不支持 Turbo",
    "--niji": "niji 是另一条模型线，本 skill 固定 V8.2",
    "--style": "V8.x 用 --raw，不写 --style raw",
    "--draft": "Draft 只在 midjourney.com 网页端",
    "--edit": "Edit Model 不走 imagine 接口",
    "--video": "视频不走 imagine 接口",
    "--relax": "速度由接口路径决定，不写在 prompt",
    "--fast": "速度由接口路径决定，不写在 prompt",
    "--r": "--repeat 会把一次提交拆成多个计费任务；需要多组时分开提交",
    "--repeat": "--repeat 会把一次提交拆成多个计费任务；需要多组时分开提交",
}
VERSION_FLAGS = {"--v", "--version"}
OK_CODES = {1, 21, 22}
DONE = {"SUCCESS", "FAILURE", "CANCEL"}


class Fail(Exception):
    pass


def split_prompt(prompt):
    """Return (text, [(flag, value)]). Parameters start at the first ' --'."""
    m = re.search(r"(^|\s)--[a-z]", prompt)
    if not m:
        return prompt.strip(), []
    text, tail = prompt[: m.start()].strip(), prompt[m.start():].strip()
    params, cur = [], None
    for tok in tail.split():
        if tok.startswith("--"):
            cur = [tok.lower(), []]
            params.append(cur)
        else:
            cur[1].append(tok)
    return text, [(f, " ".join(v)) for f, v in params]


def normalize(prompt):
    """Validate against V8.2 and pin --v 8.2. Raises Fail with every problem found."""
    text, params = split_prompt(prompt)
    problems = []
    if not text:
        problems.append("prompt 缺少文字描述")
    if "::" in text:
        problems.append("V8.x 不支持 :: 多段权重；把主次写进句子，排除项用 --no")
    if re.search(r"(?<!\\)\{[^}]*,", prompt):
        problems.append("{a, b} 排列会拆成多个计费任务；每个变体单独提交")
    flags = {f for f, _ in params}
    kept = []
    for flag, value in params:
        if flag in VERSION_FLAGS:
            continue
        if flag in REJECTED:
            problems.append(f"{flag}: {REJECTED[flag]}")
            continue
        if flag in ("--ar", "--aspect"):
            ar = re.fullmatch(r"(\d+):(\d+)", value)
            if not ar:
                problems.append(f"{flag} {value}: 比例只接受整数 W:H，如 16:9")
            elif "--hd" in flags:
                w, h = int(ar[1]), int(ar[2])
                if max(w, h) > 4 * min(w, h):
                    problems.append(f"{flag} {value}: --hd 最大 4:1")
        kept.append(f"{flag} {value}".strip())
    if problems:
        raise Fail("prompt 不合规：\n  " + "\n  ".join(problems))
    return " ".join([text, *kept, f"--v {VERSION}"])


def api_key():
    key = os.environ.get("TUZI_API_KEY", "").strip()
    if not key and KEY_FILE.is_file():
        key = KEY_FILE.read_text().strip()
    if not key:
        raise Fail(
            f"找不到 Tu-zi token。写入 {KEY_FILE}（chmod 600）或设置环境变量 TUZI_API_KEY。"
        )
    return key


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE_URL + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise Fail(f"HTTP {e.code} {path}: {e.read().decode(errors='replace')[:500]}")
    except urllib.error.URLError as e:
        raise Fail(f"网络错误 {path}: {e.reason}")


def submit(path, body):
    res = call("POST", path, body)
    code = res.get("code")
    if code not in OK_CODES or not res.get("result"):
        extra = (res.get("properties") or {}).get("bannedWord")
        hint = f"（敏感词：{extra}）" if extra else ""
        raise Fail(f"提交失败 code={code}: {res.get('description')}{hint}")
    return str(res["result"])


def wait(task_id):
    deadline = time.time() + TIMEOUT_SECONDS
    last = None
    while True:
        task = call("GET", f"/mj/task/{task_id}/fetch")
        status = task.get("status")
        note = f"{status} {task.get('progress') or ''}".strip()
        if note != last:
            print(f"[{task_id}] {note}", file=sys.stderr)
            last = note
        if status in DONE:
            return task
        if time.time() > deadline:
            raise Fail(f"任务 {task_id} 超过 {TIMEOUT_SECONDS}s 未完成，稍后用 fetch 查询")
        time.sleep(POLL_SECONDS)


def download(url, dest_stem):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        ext = mimetypes.guess_extension((r.headers.get_content_type() or "").split(";")[0])
        data = r.read()
    if ext in (None, ".jpe"):
        ext = Path(url.split("?")[0]).suffix or ".png"
    path = dest_stem.with_suffix(ext)
    path.write_bytes(data)
    return path


def report(task, out, calls):
    task_dir = Path(out) / str(task["id"])
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2))
    images = []
    urls = [u.get("url") if isinstance(u, dict) else u for u in task.get("imageUrls") or []]
    if not urls and task.get("imageUrl"):
        urls = [task["imageUrl"]]
    for i, url in enumerate(urls, 1):
        try:
            path = str(download(url, task_dir / f"image_{i}"))
        except Exception as exc:
            print(f"warning: 图片 {i} 下载失败: {exc}", file=sys.stderr)
            path = None
        images.append({"url": url, "path": path})
    summary = {
        "task_id": task["id"],
        "status": task.get("status"),
        "action": task.get("action"),
        "prompt": task.get("promptEn") or task.get("prompt"),
        "fail_reason": task.get("failReason") or None,
        "images": images,
        "buttons": [b.get("label") or b.get("emoji") or b.get("customId") for b in task.get("buttons") or []],
        "calls": calls,
        "cost": round(calls * PRICE_PER_CALL, 2),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if task.get("status") != "SUCCESS":
        raise Fail(f"任务 {task['id']} 状态 {task.get('status')}: {task.get('failReason')}")


def to_data_uri(path):
    p = Path(path)
    if not p.is_file():
        raise Fail(f"参考图不存在: {path}")
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def cmd_imagine(a):
    urls = [r for r in a.ref if re.match(r"https?://", r)]
    files = [r for r in a.ref if r not in urls]
    prompt = normalize(" ".join([*urls, a.prompt]) if urls else a.prompt)
    body = {"botType": "MID_JOURNEY", "prompt": prompt}
    if files:
        body["base64Array"] = [to_data_uri(f) for f in files]
    if a.dry_run:
        shown = {**body, "base64Array": [f"<{len(files)} image(s)>"]} if files else body
        print(json.dumps({"POST": "/mj/submit/imagine", "body": shown, "cost": PRICE_PER_CALL},
                         ensure_ascii=False, indent=2))
        return
    task_id = submit("/mj/submit/imagine", body)
    report(wait(task_id), a.out, calls=1)


BUTTON_PATTERNS = [
    (re.compile(r"[Uu]([1-4])"), "upsample::{}", "UPSCALE"),
    (re.compile(r"[Vv]([1-4])"), "variation::{}", "VARIATION"),
    (re.compile(r"(?i)reroll"), "reroll", "REROLL"),
]


def cmd_action(a):
    task = call("GET", f"/mj/task/{a.task_id}/fetch")
    buttons = task.get("buttons") or []
    want = a.button
    custom_id = want if want.startswith("MJ::") else None
    change = None
    for rx, needle, action in BUTTON_PATTERNS:
        m = rx.fullmatch(want)
        if m:
            idx = m.group(1) if m.groups() else None
            key = needle.format(idx) if idx else needle
            hit = [b["customId"] for b in buttons if key in b.get("customId", "")]
            custom_id = custom_id or (hit[0] if hit else None)
            change = {"taskId": a.task_id, "action": action, **({"index": int(idx)} if idx else {})}
    if not custom_id:
        hit = [b["customId"] for b in buttons if want in (b.get("label"), b.get("emoji"))]
        custom_id = hit[0] if hit else None
    if custom_id:
        new_id = submit("/mj/submit/action", {"customId": custom_id, "taskId": a.task_id})
    elif change:
        new_id = submit("/mj/submit/change", change)
    else:
        labels = [b.get("label") or b.get("emoji") for b in buttons]
        raise Fail(f"任务 {a.task_id} 没有按钮 {want}；可用：{labels}")
    report(wait(new_id), a.out, calls=1)


def cmd_fetch(a):
    report(wait(a.task_id), a.out, calls=0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("imagine", help="生成四宫格（1 次计费）")
    p.add_argument("prompt")
    p.add_argument("--ref", action="append", default=[], help="垫图：本地路径或 URL，可重复")
    p.add_argument("--dry-run", action="store_true", help="只校验并打印请求，不提交")
    p = sub.add_parser("action", help="对已完成任务点按钮（1 次计费）")
    p.add_argument("task_id")
    p.add_argument("button", help="U1-U4、V1-V4、reroll、按钮 label 或完整 customId")
    p = sub.add_parser("fetch", help="查询并下载已有任务（不计费）")
    p.add_argument("task_id")
    for p in sub.choices.values():
        p.add_argument("--out", default="mj-out", help="输出目录，默认 ./mj-out")
    a = ap.parse_args()
    try:
        {"imagine": cmd_imagine, "action": cmd_action, "fetch": cmd_fetch}[a.cmd](a)
    except Fail as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
