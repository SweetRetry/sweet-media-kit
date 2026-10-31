"""init 和 analyze：扫描素材、切点候选、黑场、静音、台词、人声分离、色彩和闪烁统计；sheet 出局部触点图。"""
import math, os, re, shutil, statistics
from common import (COLOR_STEP, ENV_STEP, SCENE_MIN_GAP, SEPARATOR, SILENCE_DB, SILENCE_MIN, STAT_W, VIDEO_EXT, Ws,
    decode16k, die, grab, grid_note, integrated_lufs, jdump, jload, make_sheets, probe, run)

def clip_ids(stems):
    pre = os.path.commonprefix(stems)
    cut = max((pre.rfind(c) for c in "-_. "), default=-1) + 1
    ids = [s[cut:] for s in stems] if len(stems) > 1 else list(stems)
    return ids if all(ids) and len(set(ids)) == len(ids) else list(stems)

def pick_target(metas):
    """多数素材的画幅，短边不超过 1080。"""
    ratio = lambda m: round(m["width"] / m["height"], 2)
    same = [m for m in metas if ratio(m) == statistics.mode(ratio(m) for m in metas)]
    r = same[0]["width"] / same[0]["height"]
    short = min(1080, max(min(m["width"], m["height"]) for m in same))
    w, h = (short * r, short) if r >= 1 else (short, short / r)
    even = lambda x: int(round(x / 2) * 2)
    return {"width": even(w), "height": even(h), "fps": statistics.mode(round(m["fps"]) for m in metas)}

def cmd_init(a):
    src = os.path.abspath(a.src)
    files = sorted(f for f in os.listdir(src) if f.lower().endswith(VIDEO_EXT) and not f.startswith("."))
    if not files:
        die(f"{src} 里没有视频")
    work = os.path.abspath(a.work or os.path.join(src, ".video-edit"))
    old = jload(os.path.join(work, "project.json"), {})
    metas = {f: probe(os.path.join(src, f)) for f in files}
    proj = {"src": src, "work": work,
            "lang": a.lang or old.get("lang"),
            "target": old.get("target") or pick_target(list(metas.values())),
            "whisper_model": a.whisper_model or old.get("whisper_model") or os.environ.get("WHISPER_MODEL"),
            "vad_model": a.vad_model or old.get("vad_model") or os.environ.get("VAD_MODEL"),
            "clips": dict(zip(clip_ids([os.path.splitext(f)[0] for f in files]), files))}
    if not proj["lang"]:
        die("要 --lang：台词的语种（ja、zh、en…），没有台词传 none。不用 auto，无语音段会幻觉出台词")
    if proj["lang"] != "none":
        for k in ("whisper_model", "vad_model"):
            if not (proj[k] and os.path.exists(proj[k])):
                die(f"台词转写要 --{k.replace('_', '-')}（或环境变量 {k.upper()}），没有台词传 --lang none")
    jdump(proj, os.path.join(work, "project.json"))
    print(f"工作区 {work}")
    for cid, f in proj["clips"].items():
        m = metas[f]
        print(f"  {cid:>6}  {f}  {m['width']}x{m['height']} {m['fps']}fps {m['duration']}s 音频{'有' if m['audio'] else '无'}")
    T = proj["target"]
    print(f"目标 {T['width']}x{T['height']} {T['fps']}fps，台词语种 {proj['lang']}")

def scene_scores(path):
    out = run(["ffmpeg", "-v", "error", "-i", path, "-vf",
               "scale=320:-2,select='gte(scene,0)',metadata=print:file=-", "-an", "-f", "null", "-"]).stdout
    rows, t = [], None
    for line in out.splitlines():
        m = re.match(r"frame:\d+\s+pts:\d+\s+pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
        elif t is not None and (m := re.search(r"lavfi\.scene_score=([\d.]+)", line)):
            rows.append((t, float(m.group(1))))
    return rows

def pick_cuts(rows, fixed=None):
    """阈值按每条素材自己的分数基线自适应，相邻 0.3 秒内只留最高分。"""
    sc = sorted(s for _, s in rows) or [0.0]
    th = fixed or max(0.20, min(0.60, max(statistics.median(sc) * 25, sc[int(len(sc) * 0.99)] * 0.6)))
    cuts = []
    for t, s in rows:
        if s < th:
            continue
        if cuts and t - cuts[-1][0] < SCENE_MIN_GAP:
            if s > cuts[-1][1]:
                cuts[-1] = (t, s)
            continue
        cuts.append((t, s))
    return th, cuts

def detect_spans(path):
    err = run(["ffmpeg", "-hide_banner", "-i", path, "-vf", "blackdetect=d=0.2:pix_th=0.05,freezedetect=n=0.003:d=0.5",
               "-an", "-f", "null", "-"]).stderr
    black = [[round(float(a), 2), round(float(b), 2)] for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)]
    st = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", err)]
    en = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", err)]
    return black, [[round(a, 2), round(b, 2)] for a, b in zip(st, en)]

def silences(path):
    s = decode16k(path)
    n = int(16000 * ENV_STEP)
    env = [20 * math.log10(math.sqrt(sum(x * x for x in s[i:i + n]) / n) + 1e-9) for i in range(0, len(s) - n + 1, n)]
    spans, start = [], None
    for i, d in enumerate(env + [0.0]):
        if d < SILENCE_DB and start is None:
            start = i
        elif d >= SILENCE_DB and start is not None:
            if (i - start) * ENV_STEP >= SILENCE_MIN:
                spans.append([round(start * ENV_STEP, 2), round(i * ENV_STEP, 2)])
            start = None
    return spans

SQ = [x * x for x in range(256)]

def pixel_stats(path, m):
    """逐帧缩略图的 RGB 统计。color 每 COLOR_STEP 秒一行 [r g b 均值, r g b 平方均值]，
    flicker 每秒亮度逐帧抖动：帧均亮度离前后 5 帧均值的偏差，取中位数，镜头内的切点只占一两帧，不影响中位数。"""
    h = max(2, round(STAT_W * m["height"] / m["width"] / 2) * 2)
    raw = run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"scale={STAT_W}:{h},format=rgb24", "-f", "rawvideo", "-"],
              binary=True).stdout
    n, size = STAT_W * h, STAT_W * h * 3
    frames = []
    for k in range(len(raw) // size):
        fr = raw[k * size:(k + 1) * size]
        ch = [fr[c::3] for c in range(3)]
        mean = [sum(x) / n for x in ch]
        frames.append(mean + [sum(map(SQ.__getitem__, x)) / n for x in ch] + [0.299 * mean[0] + 0.587 * mean[1] + 0.114 * mean[2]])
    fps, color, flicker = m["fps"], [], []
    y = [f[6] for f in frames]
    dev = [abs(y[i] - statistics.mean(y[max(0, i - 2):i + 3])) for i in range(len(y))]
    for k in range(math.ceil(len(frames) / fps / COLOR_STEP)):
        if rows := frames[round(k * COLOR_STEP * fps):round((k + 1) * COLOR_STEP * fps)]:
            color.append([round(statistics.mean(r[c] for r in rows), 1) for c in range(6)])
    for s in range(math.ceil(len(frames) / fps)):
        if d := dev[round(s * fps):round((s + 1) * fps)]:
            flicker.append(round(statistics.median(d), 2))
    return {"color": color, "flicker": flicker}

def transcribe(ws, cid):
    P = ws.project
    wav, base = ws.p("audio", f"{cid}.wav"), ws.p("speech", cid)
    os.makedirs(os.path.dirname(wav), exist_ok=True)
    os.makedirs(os.path.dirname(base), exist_ok=True)
    run(["ffmpeg", "-v", "error", "-y", "-i", ws.src(cid), "-vn", "-ac", "1", "-ar", "16000", wav])
    hint = ["--prompt", "以下是普通话的句子。"] if P["lang"].startswith("zh") else []   # 不加时默认出繁体
    run(["whisper-cli", "-m", P["whisper_model"], "-l", P["lang"], "--vad", "-vm", P["vad_model"],
         "-oj", "-of", base, "-f", wav] + hint)
    raw = jload(base + ".json")
    return [{"t0": s["offsets"]["from"] / 1000, "t1": s["offsets"]["to"] / 1000, "text": s["text"].strip()}
            for s in raw.get("transcription", []) if s["text"].strip()]

def analyze_clip(ws, cid, threshold):
    path = ws.src(cid)
    m = probe(path)
    th, cuts = pick_cuts(scene_scores(path), threshold)
    black, freeze = detect_spans(path)
    f = {"file": ws.project["clips"][cid], **m, "scene_threshold": round(th, 3),
         "cuts": [{"t": round(t, 3), "score": round(s, 3)} for t, s in cuts], "black": black, "freeze": freeze}
    if m["audio"]:
        f["audio"].update(lufs=integrated_lufs(path), silences=silences(path))
    f.update(pixel_stats(path, m))
    files, cols, rows = make_sheets(path, ws.p("sheets", cid), 1, m["width"], m["height"])
    f["sheets"] = [os.path.relpath(x, ws.dir) for x in files]
    f["sheet_grid"] = [cols, rows]
    for name, t in (("first", 0), ("last", m["duration"] - 0.1)):
        grab(path, t, ws.p("frames", f"{cid}_{name}.jpg"), 960)
        f[name] = f"frames/{cid}_{name}.jpg"
    return f

def separate(ws, cid):
    """把素材音轨分成人声 voice 和伴奏 bed 两路，返回 facts 里的 stems。"""
    cmd = ws.project.get("separator") or SEPARATOR
    if not shutil.which(os.path.expanduser(cmd[0])):
        die(f"找不到人声分离工具 {cmd[0]}。装法：uv tool install --python 3.12 'audio-separator[cpu]'，"
            "或在 project.json 的 separator 里换成别的命令")
    wav, out = ws.p("audio", f"{cid}_full.wav"), ws.p("stems", cid)
    os.makedirs(os.path.dirname(wav), exist_ok=True)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    run(["ffmpeg", "-v", "error", "-y", "-i", ws.src(cid), "-vn", "-ac", "2", "-ar", "44100", wav])
    run([os.path.expanduser(x.replace("{in}", wav).replace("{out}", out)) for x in cmd])
    stems = {}
    for name in ("voice", "bed"):
        f = os.path.join(out, f"{name}.wav")
        if not os.path.exists(f):
            die(f"分离命令没写出 {f}，实际产出 {os.listdir(out)}")
        stems[name] = {"file": os.path.relpath(f, ws.dir), "lufs": integrated_lufs(f)}
    return stems

def seed_log(ws, facts):
    """没动过的日志条目按当前切点候选重建骨架，动过的保持原样。"""
    log = ws.log()
    for cid in ws.ids:
        f, e = facts.get(cid), log.get(cid)
        touched = e and (e.get("summary") or e.get("defects") or any(s.get("what") for s in e.get("shots", [])))
        if f and not touched:
            b = [0.0] + [c["t"] for c in f["cuts"]] + [f["duration"]]
            log[cid] = {"summary": "", "shots": [{"t0": b[i], "t1": b[i + 1], "what": ""} for i in range(len(b) - 1)],
                        "defects": []}
    jdump({"clips": log}, ws.p("log.json"))

def cmd_analyze(a):
    ws = Ws(a.work)
    facts = ws.facts()
    ids = a.clip or ws.ids
    if bad := [c for c in ids if c not in ws.project["clips"]]:
        die(f"未知素材 {bad}，可选 {ws.ids}")
    for cid in ids:
        old = facts.get(cid)
        if old is None or a.force or a.threshold is not None:
            new = analyze_clip(ws, cid, a.threshold)
            new["lines"] = None if old is None or a.force else old.get("lines")
            if old and not a.force and old.get("stems"):
                new["stems"] = old["stems"]
            facts[cid] = new
        f = facts[cid]
        if "color" not in f:   # 早期版本分析过的素材补上色彩和闪烁统计
            f.update(pixel_stats(ws.src(cid), f))
        if f["lines"] is None:
            f["lines"] = transcribe(ws, cid) if ws.project["lang"] != "none" and f["audio"] else []
        if a.stems and f["audio"] and (a.force or not f.get("stems")):
            f["stems"] = separate(ws, cid)
        texts = [l["text"] for l in f["lines"]]
        loop = [t for t in set(texts) if texts.count(t) >= 3]
        print(f"  {cid:>6} {f['duration']}s 切点 {[c['t'] for c in f['cuts']]} 台词 {len(f['lines'])} 句"
              + (f"  疑似转写幻觉：{loop}" if loop else "")
              + ("  人声 {voice[lufs]} / 伴奏 {bed[lufs]} LUFS".format(**f["stems"]) if f.get("stems") else ""))
        jdump({"clips": facts}, ws.p("clips.json"))
    seed_log(ws, facts)
    print(f"写入 {ws.p('clips.json')}，日志骨架 {ws.p('log.json')}")
    print("1fps 触点图在 clips.json 的 sheets，" + grid_note(0, 1, *next(iter(facts.values()))["sheet_grid"]))

def cmd_sheet(a):
    ws = Ws(a.work)
    if a.target in ws.project["clips"]:
        path, name = ws.src(a.target), a.target
    elif os.path.isfile(a.target):
        path, name = os.path.abspath(a.target), os.path.splitext(os.path.basename(a.target))[0]
    else:
        die(f"{a.target} 既不是素材 id 也不是文件")
    if a.at is not None:
        out = ws.p("sheets", "fine", f"{name}@{a.at:g}.jpg")
        grab(path, a.at, out, a.width or 1280)
        print(out)
        return
    if not a.dur:
        die("要 --dur（局部图）或 --at（单帧）")
    m = probe(path)
    files, cols, rows = make_sheets(path, ws.p("sheets", "fine", f"{name}_{a.start:g}+{a.dur:g}@{a.fps:g}"),
                                    a.fps, m["width"], m["height"], a.start, a.dur, a.width)
    print("\n".join(files))
    print(grid_note(a.start, a.fps, cols, rows))
