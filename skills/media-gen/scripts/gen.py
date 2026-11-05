#!/usr/bin/env python3
"""Generate images and video via Tu-zi, driven by the site's own model catalog. Stdlib only.

  gen.py models [--kind 生图|视频|音频] [--grep TEXT]   models this token can call
  gen.py info <model>                                  docs, endpoints, parameters, price
  gen.py run <model> ["<prompt>"] [--endpoint NAME] [--param k=v ...] [--file k=PATH ...]
             [--out DIR] [--dry-run]
  gen.py fetch <model> <task_id> [--endpoint NAME]     resume polling an async task

Endpoints come from /api/pricing. Supported: /v1/images/* (sync, json or multipart) and
/v1/videos (async: submit, then poll GET /v1/videos/<id>). --param values parse as JSON when
possible (n=2, duration=5, svg={"model":"gpt-5.5"}). The prompt goes to `prompt`, or to a
text item in `content` when the endpoint takes `content`.
Token: $TUZI_API_KEY, else ~/.config/tuzi/api_key.
"""

import argparse
import base64
import http.client
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE_URL = os.environ.get("TUZI_BASE_URL", "https://api.tu-zi.com").rstrip("/")
KEY_FILE = Path.home() / ".config" / "tuzi" / "api_key"
KINDS = ("生图", "视频", "音频")
SUPPORTED_PATHS = ("/v1/images/generations", "/v1/images/edits", "/v1/videos")
DONE_OK = {"completed", "succeeded", "success", "finished"}
DONE_BAD = {"failed", "failure", "error", "cancelled", "canceled", "expired"}
POLL_SECONDS = 5
TIMEOUT_SECONDS = 1800


class Fail(Exception):
    pass


def api_key():
    key = os.environ.get("TUZI_API_KEY", "").strip()
    if not key and KEY_FILE.is_file():
        key = KEY_FILE.read_text().strip()
    if not key:
        raise Fail(f"找不到 Tu-zi token。写入 {KEY_FILE}（chmod 600）或设置环境变量 TUZI_API_KEY。")
    return key


def request(method, path, data=None, content_type=None, auth=True, timeout=60, raw=False):
    headers = {"User-Agent": "sweet-media-kit"}
    if auth:
        headers["Authorization"] = f"Bearer {api_key()}"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(BASE_URL + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            if raw and "json" not in (r.headers.get_content_type() or ""):
                return body
            return json.loads(body)
    except urllib.error.HTTPError as e:
        raise Fail(f"HTTP {e.code} {path}: {e.read().decode(errors='replace')[:800]}")
    except urllib.error.URLError as e:
        raise Fail(f"网络错误 {path}: {e.reason}")


def callable_models():
    return {m["id"]: m for m in request("GET", "/v1/models").get("data") or []}


def catalog():
    """Public catalog: docs, endpoints, parameters and price per model."""
    data = request("GET", "/api/pricing", auth=False, timeout=120)
    return data, {r["model_name"]: r for r in data.get("data") or []}


def cmd_models(a):
    kinds = [a.kind] if a.kind else KINDS
    rows = [m for m in callable_models().values() if m.get("category") in kinds]
    if a.grep:
        rows = [m for m in rows if a.grep.lower() in m["id"].lower()]
    mj = [m for m in rows if m["id"].startswith("mj_")]
    for m in sorted(rows, key=lambda m: (m.get("category"), m["id"])):
        if m not in mj:
            print(f"{m.get('category')}  {m['id']:44} {','.join(m.get('supported_endpoint_types') or [])}")
    if mj:
        print(f"\n另有 {len(mj)} 个 mj_* 计费项，走 Midjourney 任务接口（mj.py）。")


def model_row(model):
    if model not in callable_models():
        raise Fail(f"当前 token 的 /v1/models 里没有 {model}；用 models --grep 查可用 id")
    cat, rows = catalog()
    if model not in rows:
        raise Fail(f"/api/pricing 没有 {model} 的说明，无法确定端点")
    return cat, rows[model]


def fmt_param(p):
    bits = [p.get("type") or ""]
    if p.get("options"):
        bits.append("/".join(str(o.get("value")) for o in p["options"]))
    if p.get("min") is not None or p.get("max") is not None:
        bits.append(f"{p.get('min')}–{p.get('max')}")
    if p.get("default_value") is not None:
        bits.append(f"默认 {p['default_value']}")
    req = "必填" if p.get("required") else "可选"
    notes = "；".join([p.get("description") or "", *(p.get("notes") or [])]).strip("；")
    return f"  - {p['name']}（{req}，{'，'.join(b for b in bits if b)}）{notes}"


def cmd_info(a):
    cat, row = model_row(a.model)
    print(row.get("description") or "（站点未写说明）")
    print("\n## 端点")
    for name, ep in (row.get("endpoints") or {}).items():
        mark = "" if ep.get("path") in SUPPORTED_PATHS else "（gen.py 不支持）"
        print(f"- {name}{mark}: {ep.get('method')} {ep.get('path')}"
              + (" 异步" if ep.get("async") else "") + (f" {ep['transport']}" if ep.get("transport") else "")
              + (f"\n  文档 {ep['docs']}" if ep.get("docs") else ""))
        for p in ep.get("parameters") or []:
            if p.get("show_in_form") is not False:
                print(fmt_param(p))
        if ep.get("request_template"):
            print("  示例 " + json.dumps(ep["request_template"], ensure_ascii=False))
    ratios = cat.get("group_ratio") or {}
    groups = "，".join(f"{g}×{ratios.get(g, '?')}" for g in row.get("enable_groups") or [])
    print("\n## 计费")
    if row.get("quota_type") == 1:
        print(f"按 token：model_ratio={row.get('model_ratio')} completion_ratio={row.get('completion_ratio')}"
              f" image_ratio={row.get('image_ratio')} image_output_ratio={row.get('image_output_ratio')}；"
              "提交前算不出单次价格，以返回的 usage 和后台账单为准")
    else:
        print(f"固定单价 model_price={row.get('model_price')}（quota_type={row.get('quota_type')}，"
              "计价单位见上方说明，再乘分组倍率）")
    print(f"可用分组与倍率：{groups}")


def pick_endpoint(row, name, has_files):
    eps = {n: e for n, e in (row.get("endpoints") or {}).items() if e.get("path") in SUPPORTED_PATHS}
    if name:
        if name not in eps:
            raise Fail(f"{row['model_name']} 没有可用端点 {name}；可选 {list(eps)}")
        return name, eps[name]
    if not eps:
        listed = {n: e.get("path") for n, e in (row.get("endpoints") or {}).items()}
        raise Fail(f"{row['model_name']} 的端点 gen.py 都不支持：{listed}")
    multipart_eps = [n for n, e in eps.items() if e.get("transport") == "multipart"]
    if has_files and multipart_eps:
        return multipart_eps[0], eps[multipart_eps[0]]
    sync = [n for n, e in eps.items() if e.get("transport") != "multipart" and e["path"] != "/v1/videos"]
    n = (sync or [n for n in eps if n not in multipart_eps] or list(eps))[0]
    return n, eps[n]


def parse_pairs(pairs, flag):
    out = []
    for pair in pairs:
        k, sep, v = pair.partition("=")
        if not sep or not k:
            raise Fail(f"{flag} 需要 key=value：{pair}")
        out.append((k, v))
    return out


def build_fields(model, prompt, ep, params):
    fields = {"model": model}
    for k, v in params:
        try:
            fields[k] = json.loads(v)
        except json.JSONDecodeError:
            fields[k] = v
    if prompt:
        names = {p["name"] for p in ep.get("parameters") or []}
        if "content" in names and "prompt" not in names:
            fields.setdefault("content", [])
            fields["content"] = [{"type": "text", "text": prompt}] + [
                c for c in fields["content"] if c.get("type") != "text"]
        else:
            fields["prompt"] = prompt
    return fields


def multipart(fields, files):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        v = v if isinstance(v, str) else json.dumps(v)
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for name, path in files:
        p = Path(path)
        if not p.is_file():
            raise Fail(f"文件不存在: {path}")
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                     f'filename="{p.name}"\r\nContent-Type: {mime}\r\n\r\n'.encode() + p.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def data_uri(path):
    p = Path(path)
    if not p.is_file():
        raise Fail(f"文件不存在: {path}")
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def fetch_url(url, attempts=5):
    """Media CDNs cut large transfers short; keep what arrived and resume with Range."""
    got = b""
    for i in range(attempts):
        headers = {"User-Agent": "Mozilla/5.0", **({"Range": f"bytes={len(got)}-"} if got else {})}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=300) as r:
                if got and r.status != 206:
                    got = b""
                return got + r.read()
        except http.client.IncompleteRead as e:
            got += e.partial
        except Exception:
            if i == attempts - 1:
                raise
        time.sleep(1)
    raise Fail(f"下载 {attempts} 次仍不完整（已收 {len(got)} 字节）")


def sniff(data):
    for magic, ext in ((b"\x89PNG", "png"), (b"\xff\xd8", "jpg"), (b"GIF8", "gif"),
                       (b"RIFF", "webp"), (b"\x1aE\xdf\xa3", "webm"), (b"ID3", "mp3")):
        if data.startswith(magic):
            return ext
    if data[4:8] == b"ftyp":
        return "mp4"
    return "svg" if b"<svg" in data[:500] else "bin"


def media_urls(obj):
    """Every http(s) string under a key mentioning url, in document order."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and "url" in k.lower() and v.startswith("http"):
                found.append(v)
            else:
                found += media_urls(v)
    elif isinstance(obj, list):
        for v in obj:
            found += [v] if isinstance(v, str) and v.startswith("http") else media_urls(v)
    return list(dict.fromkeys(found))


def save_all(res, out, fmt):
    items = []
    data_list = res.get("data") if isinstance(res.get("data"), list) else []
    for d in data_list:
        if isinstance(d, dict) and d.get("b64_json"):
            data = base64.b64decode(d["b64_json"])
            p = out / f"output_{len(items) + 1}.{fmt or sniff(data)}"
            p.write_bytes(data)
            items.append({"path": str(p)})
    for url in media_urls(res):
        try:
            data = fetch_url(url)
            p = out / f"output_{len(items) + 1}.{fmt if fmt and fmt != 'url' else sniff(data)}"
            p.write_bytes(data)
            items.append({"path": str(p), "url": url})
        except Exception as exc:
            print(f"warning: {url} 下载失败: {exc}", file=sys.stderr)
            items.append({"path": None, "url": url})
    return items


def redact(obj):
    if isinstance(obj, dict):
        return {k: (f"<{len(v)} chars>" if k == "b64_json" and isinstance(v, str) else redact(v))
                for k, v in obj.items()}
    return [redact(v) for v in obj] if isinstance(obj, list) else obj


def task_status(task):
    for src in (task, task.get("data") if isinstance(task.get("data"), dict) else {}):
        for key in ("status", "state", "task_status"):
            if isinstance(src.get(key), str):
                return src[key].lower()
    return ""


def poll(path, task_id):
    deadline, last = time.time() + TIMEOUT_SECONDS, None
    while True:
        task = request("GET", f"{path}/{task_id}")
        status = task_status(task)
        note = f"{status} {task.get('progress') or ''}".strip()
        if note != last:
            print(f"[{task_id}] {note}", file=sys.stderr)
            last = note
        if status in DONE_OK or status in DONE_BAD:
            return task, status in DONE_OK
        if time.time() > deadline:
            raise Fail(f"任务 {task_id} 超过 {TIMEOUT_SECONDS}s 未完成，稍后用 fetch 继续")
        time.sleep(POLL_SECONDS)


def finish(model, path, res, ok, fmt, out_dir, started):
    out = Path(out_dir) / f"{model}-{time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "response.json").write_text(json.dumps(redact(res), ensure_ascii=False, indent=2))
    items = save_all(res, out, fmt) if ok else []
    if ok and not items and path == "/v1/videos":
        task_id = res.get("id") or res.get("task_id")
        body = request("GET", f"{path}/{task_id}/content", raw=True, timeout=300)
        if isinstance(body, bytes):
            p = out / f"output_1.{sniff(body)}"
            p.write_bytes(body)
            items.append({"path": str(p)})
    print(json.dumps({"model": model, "endpoint": path, "ok": ok, "seconds": round(time.time() - started, 1),
                      "outputs": items, "usage": res.get("usage"), "dir": str(out)},
                     ensure_ascii=False, indent=2))
    if not ok:
        raise Fail(f"任务失败，见 {out / 'response.json'}")
    if not items:
        raise Fail(f"接口未返回可下载内容，见 {out / 'response.json'}")


def cmd_run(a):
    _, row = model_row(a.model)
    files = parse_pairs(a.file, "--file")
    name, ep = pick_endpoint(row, a.endpoint, bool(files))
    path, is_multipart = ep["path"], ep.get("transport") == "multipart"
    fields = build_fields(a.model, a.prompt, ep, parse_pairs(a.param, "--param"))
    if files and not is_multipart:
        for k, v in files:
            fields[k] = data_uri(v)
    if a.dry_run:
        shown = redact({k: (f"<data uri {len(v)} chars>" if isinstance(v, str) and v.startswith("data:") else v)
                        for k, v in fields.items()})
        print(json.dumps({"endpoint": name, "POST": path, "async": bool(ep.get("async")),
                          "body": shown, "files": dict(files) if is_multipart else {}},
                         ensure_ascii=False, indent=2))
        return
    if is_multipart:
        body, ctype = multipart(fields, files)
    else:
        body, ctype = json.dumps(fields).encode(), "application/json"
    started = time.time()
    res = request("POST", path, data=body, content_type=ctype, timeout=600)
    task_id = res.get("id") or res.get("task_id")
    if path == "/v1/videos" or (ep.get("async") and task_id):
        if not task_id:
            raise Fail(f"异步接口没返回任务 id：{json.dumps(redact(res), ensure_ascii=False)[:500]}")
        print(f"task_id {task_id}", file=sys.stderr)
        res, ok = poll(path, task_id)
    else:
        ok = True
    finish(a.model, path, res, ok, fields.get("output_format"), a.out, started)


def cmd_fetch(a):
    _, row = model_row(a.model)
    name, ep = pick_endpoint(row, a.endpoint or "openai-video", False)
    started = time.time()
    res, ok = poll(ep["path"], a.task_id)
    finish(a.model, ep["path"], res, ok, None, a.out, started)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("models", help="列出当前 token 可调用的生成模型（不计费）")
    p.add_argument("--kind", choices=KINDS)
    p.add_argument("--grep", help="按子串过滤 id")
    p = sub.add_parser("info", help="说明、端点、参数与计费（不计费）")
    p.add_argument("model")
    p = sub.add_parser("run", help="提交生成（计费）")
    p.add_argument("model")
    p.add_argument("prompt", nargs="?")
    p.add_argument("--endpoint", help="端点名，取 info 列出的名字；缺省时有 --file 选 multipart，否则选同步端点")
    p.add_argument("--param", action="append", default=[], help="请求字段 key=value，可重复")
    p.add_argument("--file", action="append", default=[],
                   help="文件字段 key=PATH，可重复；multipart 端点上传，JSON 端点转 data URI")
    p.add_argument("--dry-run", action="store_true", help="只打印请求，不提交")
    p = sub.add_parser("fetch", help="继续轮询异步任务（不计费）")
    p.add_argument("model")
    p.add_argument("task_id")
    p.add_argument("--endpoint")
    for p in (sub.choices["run"], sub.choices["fetch"]):
        p.add_argument("--out", default="gen-out", help="输出目录，默认 ./gen-out")
    a = ap.parse_args()
    try:
        {"models": cmd_models, "info": cmd_info, "run": cmd_run, "fetch": cmd_fetch}[a.cmd](a)
    except Fail as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
