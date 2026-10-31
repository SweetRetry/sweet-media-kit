"""剪辑规则：逐条检查 edl.json 的选段和素材里的区间（崩坏、黑场、台词、镜头边界）的关系，以及场内色彩匹配。"""
import math, os, statistics
from common import (BLACK_FRAMES, COARSE_FPS, COARSE_NEAR, COLOR_STEP, EPS, FLICKER, LINE_TAIL, MATCH_GAIN,
    MATCH_SHIFT, MATCH_STRENGTH, SLIVER, SLOW, SOUNDS, frames_of, is_num, min_speed, out_at, resolve)
from schema import (DEFECT_KEYS, EDL_KEYS, KINDS, LAYER_KEYS, LOOK_KEYS, SCENE_KEYS, SELECT_KEYS, SEVERITY,
    SHOT_KEYS, WAIVABLE)

def range_stats(f, spans):
    """素材几段区间合起来的每通道 (均值, 标准差)。"""
    rows = [r for t0, t1 in spans for r in f["color"][int(t0 / COLOR_STEP):max(int(t0 / COLOR_STEP) + 1, math.ceil(t1 / COLOR_STEP))]] \
        or f["color"][-1:]
    mean = [statistics.mean(r[c] for r in rows) for c in range(6)]
    return [(mean[c], math.sqrt(max(1.0, mean[c + 3] - mean[c] ** 2))) for c in range(3)]

def grade_of(it, flat, facts):
    """场内按基准素材做的色彩校正：(lutrgb 滤镜, 说明)，不校正返回 None。
    每通道把均值和标准差向基准靠拢 MATCH_STRENGTH，倍数和平移有上限。"""
    s, ref = it["sel"], it.get("match")
    if not ref or "clip" not in s or s["clip"] == ref or s.get("match") is False or "color" not in facts[s["clip"]]:
        return None
    mine = [(x["sel"]["in"], x["sel"]["out"]) for x in flat if x["sid"] == it["sid"] and x["ok"]
            and x["sel"].get("clip") == ref]
    R = range_stats(facts[ref], mine or [(0, facts[ref]["duration"])])
    S = range_stats(facts[s["clip"]], [(s["in"], s["out"])])
    expr, capped = [], False
    for (mr, sr), (ms, ss) in zip(R, S):
        k = 1 + MATCH_STRENGTH * (sr / ss - 1)
        shift = MATCH_STRENGTH * (mr - ms)
        kc, sc = min(max(k, MATCH_GAIN[0]), MATCH_GAIN[1]), max(-MATCH_SHIFT, min(MATCH_SHIFT, shift))
        capped |= (kc, sc) != (k, shift)
        expr.append(f"'clip((val-{ms:.2f})*{kc:.4f}+{ms + sc:.2f},0,255)'")
    lut = "lutrgb=" + ":".join(f"{c}={e}" for c, e in zip("rgb", expr))
    return lut, {"ref": ref, "ref_mean": [round(m, 1) for m, _ in R], "src_mean": [round(m, 1) for m, _ in S],
                 "capped": capped}

def defect_issue(d, dur):
    if not isinstance(d, dict) or not is_num(d.get("t0"), d.get("t1")):
        return "缺 t0 或 t1"
    if extra := set(d) - DEFECT_KEYS:
        return f"未知字段 {sorted(extra)}"
    if not 0 <= d["t0"] < d["t1"] <= dur + 0.1:
        return f"区间 {d['t0']}-{d['t1']} 不在 0-{dur} 内"
    if d.get("kind") not in KINDS:
        return f"kind={d.get('kind')!r}，可选 {' '.join(KINDS)}"
    if d.get("severity") not in SEVERITY:
        return f"severity={d.get('severity')!r}，可选 major minor"
    if "seen" in d and not (is_num(d["seen"]) and d["seen"] > 0):
        return f"seen={d['seen']!r} 要是正数"
    return None

def log_issues(cid, e, f):
    if not e:
        return [f"{cid} 没有日志"]
    out = [f"{cid} 缺 summary"] if not e.get("summary") else []
    t = 0.0
    for i, s in enumerate(e.get("shots") or []):
        if not isinstance(s, dict) or not is_num(s.get("t0"), s.get("t1")):
            out.append(f"{cid} 镜头 {i} 缺 t0 或 t1")
            continue
        if extra := set(s) - SHOT_KEYS:
            out.append(f"{cid} 镜头 {i} 有未知字段 {sorted(extra)}")
        if abs(s["t0"] - t) > 0.05:
            out.append(f"{cid} 镜头 {i} 的 t0={s['t0']} 没接上前一镜头的 t1={t}")
        if not s.get("what"):
            out.append(f"{cid} 镜头 {i} [{s['t0']}-{s['t1']}] 缺 what")
        t = s["t1"]
    if abs(t - f["duration"]) > 0.1:
        out.append(f"{cid} 镜头只盖到 {t}，素材长 {f['duration']}")
    for d in e.get("defects") or []:
        if msg := defect_issue(d, f["duration"]):
            out.append(f"{cid} 崩坏 {d.get('t0')}-{d.get('t1') if isinstance(d, dict) else d}：{msg}")
    return out

def clean_range(t0, t1, avoid, lines, bounds, sp, fixed_head, fixed_tail, dur):
    """[t0, t1] 附近最长的干净区间：不压 avoid，边缘不在台词里，不带残帧。
    边缘落在台词里时，差 0.3 秒以内就外扩包住整句，否则收缩。"""
    clear = lambda x: 0 <= x <= dur and not any(a < x < b for a, b in avoid)
    for l in lines:
        if not fixed_head and l["t0"] + 0.05 < t0 < l["t1"] and t0 - l["t0"] <= 0.3 and clear(l["t0"]):
            t0 = l["t0"]
        if not fixed_tail and l["t0"] + 0.05 < t1 < l["t1"] + LINE_TAIL - EPS and l["t1"] + LINE_TAIL - t1 <= 0.3 \
                and clear(l["t1"] + LINE_TAIL):
            t1 = l["t1"] + LINE_TAIL
    segs = [(t0, t1)]
    for a, b in avoid:
        segs = [p for x, y in segs for p in ((x, min(y, a)), (max(x, b), y)) if p[1] > p[0]]
    best = None
    for x, y in segs:
        keep_x, keep_y = fixed_head and x == t0, fixed_tail and y == t1
        for _ in range(8):
            prev = (x, y)
            for l in lines:
                if not keep_x and l["t0"] + 0.05 < x < l["t1"]:
                    x = l["t1"]
                if not keep_y and l["t0"] + 0.05 < y < l["t1"] + LINE_TAIL - EPS:
                    y = l["t0"]
            for b in bounds:
                if not keep_x and x < b < y and (b - x) / sp < SLIVER:
                    x = b
                if not keep_y and x < b < y and (y - b) / sp < SLIVER:
                    y = b
            if (x, y) == prev:
                break
        if y - x >= 0.3 and (best is None or y - x > best[1] - best[0]):
            best = (x, y)
    return best

def sound_of(sel, edl):
    return "none" if sel.get("ramp") else sel.get("sound", edl.get("sound", "mix"))

def select_fx_issues(s):
    """变速、定格、运镜字段的格式问题。"""
    out = []
    ramp = s.get("ramp")
    if ramp is not None:
        if "speed" in s:
            out.append("ramp 和 speed 不能同用")
        if not (isinstance(ramp, list) and ramp and all(isinstance(k, list) and len(k) == 2 and is_num(*k) for k in ramp)):
            out.append("ramp 要是 [[素材秒, 倍速], …]")
        elif any(v <= 0 for _, v in ramp) or any(a[0] >= b[0] for a, b in zip(ramp, ramp[1:])):
            out.append("ramp 的倍速要是正数，素材秒要递增")
        elif "sound" in s and s["sound"] != "none":
            out.append("ramp 段不带同期声，去掉 sound，冲击声用音效层补")
    if "hold" in s and not (is_num(s["hold"]) and s["hold"] > 0):
        out.append("hold 要是正数秒")
    z = s.get("zoom")
    if z is not None and not ((is_num(z) and z >= 1) or (isinstance(z, list) and len(z) == 2 and is_num(*z) and min(z) >= 1)):
        out.append("zoom 要是不小于 1 的数，或 [起, 止]")
    c = s.get("center")
    if c is not None and not (isinstance(c, list) and len(c) == 2 and is_num(*c) and all(0 <= x <= 1 for x in c)):
        out.append("center 要是 [x, y]，取 0 到 1")
    for k in s.get("shake") or []:
        if not (isinstance(k, dict) and is_num(k.get("at"), k.get("dur"), k.get("amp")) and k["dur"] > 0
                and 0 < k["amp"] <= 0.1 and set(k) <= {"at", "dur", "amp"}):
            out.append("shake 每项要 {at, dur, amp}，dur 为正，amp 在 0 到 0.1")
        elif not s["in"] <= k["at"] < s["out"]:
            out.append(f"shake at={k['at']} 不在选段 {s['in']}-{s['out']} 内")
    if "match" in s and s["match"] is not False:
        out.append("选段的 match 只能写 false，基准写在场上")
    return out

def look_issues(look, base):
    if not isinstance(look, dict):
        return ["look 要是对象"]
    out = [f"未知字段 {k}" for k in set(look) - LOOK_KEYS]
    if "lut" in look and not (isinstance(look["lut"], str) and os.path.exists(resolve(base, look["lut"]))):
        out.append(f"找不到 LUT 文件 {look['lut']!r}（相对工作区 {base}）")
    for k, lo, hi in (("contrast", 0.5, 2), ("saturation", 0, 2), ("gamma", 0.5, 2), ("temperature", 2000, 12000),
                      ("vignette", 0, 1), ("grain", 0, 30)):
        if k in look and not (is_num(look[k]) and lo <= look[k] <= hi):
            out.append(f"{k} 要在 {lo} 到 {hi} 之间")
    for k in ("shadows", "highlights"):
        if k in look and not (isinstance(look[k], list) and len(look[k]) == 3 and is_num(*look[k])
                              and all(-1 <= x <= 1 for x in look[k])):
            out.append(f"{k} 要是 [r, g, b]，各在 -1 到 1")
    if "deflicker" in look and not isinstance(look["deflicker"], bool):
        out.append("deflicker 要是 true 或 false")
    return out

def zoom_of(s):
    """(起, 止) 放大倍数，含震动需要的余量。"""
    z = s.get("zoom", 1.0)
    z0, z1 = (z, z) if is_num(z) else z
    room = 1 + 2 * max([k["amp"] for k in s.get("shake") or []], default=0.0)
    return max(z0, room), max(z1, room)

def anchor_span(flat, name, fps):
    """锚点在成片里的 (起, 止)：场 id 取整场，'A#2' 取一个选段。"""
    its = [it for it in flat if it["at"] == name] or [it for it in flat if it["sid"] == name]
    return (its[0]["t"], its[-1]["t"] + its[-1]["frames"] / fps) if its else None

def check_edl(edl, facts, log, ids, T, base="."):
    """返回 (问题, 备注, 选段表, 预计时长)。问题带 waived 的算已处理。T 是画幅目标。"""
    found, notes, used_waives, fps = [], [], set(), T["fps"]

    def add(rule, at, msg, sel=None, **extra):
        why = (sel.get("waive") or {}).get(rule) if sel and rule in WAIVABLE else None
        if why:
            used_waives.add((id(sel), rule))
        found.append({"rule": rule, "at": at, "msg": msg, **({"waived": why} if why else {}), **extra})

    if not isinstance(edl, dict):
        add("format", "edl", "edl.json 顶层要是对象")
        return found, notes, [], 0.0
    for k in set(edl) - EDL_KEYS:
        add("format", "edl", f"未知字段 {k}")
    if edl.get("sound", "mix") not in SOUNDS:
        add("format", "edl", f"sound={edl['sound']!r}，可选 {' '.join(SOUNDS)}")
    flat = []
    for k, sc in enumerate(edl.get("scenes") or []):
        sid = str(sc.get("id", k))
        for key in set(sc) - SCENE_KEYS:
            add("format", sid, f"场里有未知字段 {key}")
        d = sc.get("dissolve")
        if d is not None and (k == 0 or not is_num(d) or d <= 0):
            add("format", sid, "dissolve 要是正数，且第一场没有可叠化的上一场")
            d = None
        if not sc.get("beat"):
            add("format", sid, "场没写 beat：观众在这一场必须得到的一件事")
        if not sc.get("selects"):
            add("format", sid, "场里没有 selects")
        ref = sc.get("match")
        if ref is not None and (ref not in ids or "color" not in (facts.get(ref) or {})):
            add("format", sid, f"match={ref!r} 要是分析过的素材 id；旧工程先重跑 edit.py analyze 补色彩统计")
            ref = None
        for j, s in enumerate(sc.get("selects") or []):
            flat.append({"sid": sid, "j": j, "at": f"{sid}#{j}", "sel": s, "dissolve": d if j == 0 else None,
                         "match": ref})
    if not flat:
        add("format", "edl", "没有任何选段")
    for msg in look_issues(edl.get("look") or {}, base):
        add("format", "look", msg)

    for it in flat:
        s, at = it["sel"], it["at"]
        it["ok"] = False
        if not isinstance(s, dict):
            add("format", at, "选段要是对象")
            continue
        for key in set(s) - SELECT_KEYS:
            add("format", at, f"未知字段 {key}")
        for key in set(s.get("waive") or {}) - set(WAIVABLE):
            add("format", at, f"waive 里的 {key} 不可豁免，可豁免：{' '.join(WAIVABLE)}")
        if not s.get("why"):
            add("why", at, "没写 why", s)
        if "gap" in s:
            if not s["gap"] or not (is_num(s.get("dur")) and s["dur"] > 0):
                add("format", at, "缺镜头要 gap 写清拍什么，dur 为正数秒")
            else:
                it["ok"] = True
                notes.append(f"{at} 缺镜头 {s['dur']}s：{s['gap']}。edit.py gaps 出补拍单")
            continue
        if "color" in s:
            if s["color"] not in ("white", "black") or not (isinstance(s.get("frames"), int) and s["frames"] > 0):
                add("format", at, "色帧要 color 为 white 或 black，frames 为正整数")
            else:
                it["ok"] = True
            continue
        cid, f = s.get("clip"), facts.get(s.get("clip"))
        t0, t1, sp = s.get("in"), s.get("out"), s.get("speed", 1.0)
        if cid not in ids or f is None:
            add("bounds", at, f"未知或没分析的素材 {cid!r}")
        elif not is_num(t0, t1, sp) or sp <= 0 or not 0 <= t0 < t1 <= f["duration"] + 0.01:
            add("bounds", at, f"{cid} 区间 {t0}-{t1} 不在 0-{f['duration']} 内，或 speed 不是正数")
        elif fx := select_fx_issues(s):
            for msg in fx:
                add("format", at, msg)
        else:
            it["ok"] = True
            snd, lead, lag = sound_of(s, edl), s.get("lead", 0.0), s.get("lag", 0.0)
            if snd not in SOUNDS:
                add("format", at, f"sound={snd!r}，可选 {' '.join(SOUNDS)}")
            elif snd in ("voice", "bed") and f["audio"] and not (f.get("stems") or {}).get(snd):
                add("format", at, f"{cid} 还没有分离出 {snd} 声轨，先跑 edit.py analyze --stems")
            if not is_num(lead, lag, s.get("gain", 0.0)) or lead < 0 or lag < 0:
                add("format", at, "lead、lag 要是非负数，gain 要是数")
                it["ok"] = False
            elif t0 - lead * sp < -0.01 or t1 + lag * sp > f["duration"] + 0.01:
                add("bounds", at, f"{cid} 声音区间 {t0 - lead * sp:.2f}-{t1 + lag * sp:.2f} 超出素材 0-{f['duration']}")
                it["ok"] = False

    def joined(a, b):
        sa, sb = a["sel"], b["sel"]
        return (a["ok"] and b["ok"] and b["dissolve"] is None and "clip" in sa and "clip" in sb
                and sa["clip"] == sb["clip"] and abs(sa["out"] - sb["in"]) < 0.01 and not sa.get("hold")
                and not sa.get("ramp") and not sb.get("ramp")
                and bool(sa.get("flip")) == bool(sb.get("flip")) and sa.get("speed", 1.0) == sb.get("speed", 1.0))

    for n, it in enumerate(flat):
        s, at = it["sel"], it["at"]
        if not it["ok"] or "clip" not in s:
            continue
        cid, t0, t1, sp = s["clip"], s["in"], s["out"], s.get("speed", 1.0)
        f, e = facts[cid], log.get(cid) or {}
        head = None if n > 0 and joined(flat[n - 1], it) else t0
        tail = None if n + 1 < len(flat) and joined(it, flat[n + 1]) else t1
        shots = [x for x in e.get("shots") or [] if isinstance(x, dict) and is_num(x.get("t0"))]
        bounds = [x["t0"] for x in shots[1:]] if shots else [c["t"] for c in f["cuts"]]
        lines = f.get("lines") or []
        defects = [d for d in e.get("defects") or [] if not defect_issue(d, f["duration"])]
        first = len(found)
        avoid = [(b0, b1) for b0, b1 in f.get("black", []) if min(t1, b1) - max(t0, b0) >= (BLACK_FRAMES - 0.5) / fps]
        for b0, b1 in avoid:
            add("black", at, f"{cid} {t0}-{t1} 含黑场 [{b0}-{b1}] {min(t1, b1) - max(t0, b0):.2f}s", s)
        lead, lag = s.get("lead", 0.0), s.get("lag", 0.0)
        a_head = None if head is None else round(t0 - lead * sp, 3)   # 声音的入出点
        a_tail = None if tail is None else round(t1 + lag * sp, 3)
        for l in lines if sound_of(s, edl) in ("mix", "voice") else []:
            span = f"「{l['text']}」[{l['t0']:.2f}-{l['t1']:.2f}]"
            if a_head is not None and l["t0"] + 0.05 < a_head < l["t1"]:
                add("line", at, f"{cid} 声音入点 {a_head} 切进台词{span}。整句包住要 in ≤ {l['t0']:.2f}，"
                    f"或 lead ≥ {(t0 - l['t0']) / sp:.2f}（J-cut）", s)
            if a_tail is not None and l["t0"] + 0.05 < a_tail < l["t1"] + LINE_TAIL - EPS:
                add("line", at, f"{cid} 声音出点 {a_tail} 切断台词{span}。整句包住要 out ≥ {l['t1'] + LINE_TAIL:.2f}，"
                    f"或 lag ≥ {(l['t1'] + LINE_TAIL - t1) / sp:.2f}（L-cut）", s)
        for b in bounds:
            if t0 < b < t1 and head is not None and out_at(s, b) < SLIVER:
                add("sliver", at, f"{cid} 入点 {t0} 只带进镜头边界 {b} 之前 {b - t0:.2f}s 的残帧", s)
            if t0 < b < t1 and tail is not None and out_at(s, t1) - out_at(s, b) < SLIVER:
                add("sliver", at, f"{cid} 出点 {t1} 只带进镜头边界 {b} 之后 {t1 - b:.2f}s 的残帧", s)
        if min_speed(s) < SLOW:
            slow = [k for k, v in s["ramp"] if v < SLOW] if s.get("ramp") else [t0, t1]
            a_, b_ = max(t0, min(slow) - 0.2), min(t1, max(slow) + 0.2)
            add("slow", at, f"{cid} {a_:.2f}-{b_:.2f} 慢到 {min_speed(s):g}x，崩坏会被放大。edit.py inspect 出 6fps 图，"
                "看完在 waive.slow 写看到了什么", s, look=[cid, round(a_, 2), round(b_, 2)])
        z, short = max(zoom_of(s)), min(T["width"], T["height"])
        if (eff := min(f["width"], f["height"]) / z) < 0.8 * short:
            notes.append(f"{at} 放大 {z:.2f} 倍后有效短边约 {eff:.0f}px，低于目标 {short}px 的 80%，画面会发软")
        if not (edl.get("look") or {}).get("deflicker") and f.get("flicker"):
            jit = statistics.median(f["flicker"][int(t0):max(int(t0) + 1, math.ceil(t1))] or [0])
            if jit > FLICKER:
                notes.append(f"{at} {cid} {t0}-{t1} 亮度逐帧抖动 {jit:.1f}（>{FLICKER:g}），考虑 look.deflicker")
        for d in defects:
            span = f"{d['kind']} [{d['t0']}-{d['t1']}]"
            if d["t0"] < t1 and d["t1"] > t0:
                if d["kind"] == "nudity":
                    add("nudity", at, f"{cid} {t0}-{t1} 压到 {span}，不能豁免")
                    avoid.append((d["t0"], d["t1"]))
                elif d["severity"] == "minor":
                    notes.append(f"{at} {cid} {t0}-{t1} 用到 minor 崩坏 {span}：{d.get('note', '')}")
                else:
                    add("defect", at, f"{cid} {t0}-{t1} 压到 {span}：{d.get('note', '')}", s)
                    avoid.append((d["t0"], d["t1"]))
            elif d["severity"] == "major" and d.get("seen", 1) < COARSE_FPS:
                if (head is not None and 0 <= head - d["t1"] < COARSE_NEAR) or \
                   (tail is not None and 0 <= d["t0"] - tail < COARSE_NEAR):
                    edge = head if head is not None and 0 <= head - d["t1"] < COARSE_NEAR else tail
                    look = [cid, round(max(0.0, min(edge, d["t0"]) - 0.5), 2),
                            round(min(f["duration"], max(edge, d["t1"]) + 0.5), 2)]
                    add("coarse", at, f"{cid} 边缘 {edge} 离只按 {d.get('seen', 1)}fps 看过的 {span} 不到 {COARSE_NEAR:g}s。"
                        f"edit.py inspect 出局部图，定准区间后把 seen 改成实际密度", s, look=look)
        mine = [x for x in found[first:] if x["rule"] in ("line", "sliver", "defect", "nudity", "black") and not x.get("waived")]
        if mine:
            r = clean_range(t0, t1, sorted(avoid), lines, bounds, (t1 - t0) / out_at(s, t1), head is None, tail is None, f["duration"])
            fix = f"建议 in={math.ceil(r[0] * 100 - 1e-6) / 100} out={math.ceil(r[1] * 100 - 1e-6) / 100}" if r else "这段里没有干净的区间，换一段"
            for x in mine:
                x["fix"] = fix

    for it in flat:
        if it["ok"] and (g := grade_of(it, flat, facts)) and g[1]["capped"]:
            notes.append(f"{it['at']} 和基准 {g[1]['ref']} 色差太大（均值 {g[1]['src_mean']} 对 {g[1]['ref_mean']}），"
                         "校正到上限为止。多半不是同一场光，换基准，或把这段的 match 设为 false")
    for n, it in enumerate(flat[:-1]):
        s, nx = it["sel"], flat[n + 1]["sel"]
        if it["ok"] and s.get("lag") and "clip" in nx and sound_of(nx, edl) != "none":
            notes.append(f"{it['at']} 的 L-cut 声音会叠在 {flat[n + 1]['at']} 的同期声上 {s['lag']}s，"
                         f"需要时把后者的 sound 改成 bed 或 none，或调低 gain")
        if it["ok"] and nx.get("lead") and "clip" in s and sound_of(s, edl) != "none":
            notes.append(f"{flat[n + 1]['at']} 的 J-cut 声音会叠在 {it['at']} 的同期声上 {nx['lead']}s")
    for n, it in enumerate(flat):
        d = it["dissolve"]
        if d and n > 0 and it["ok"] and flat[n - 1]["ok"]:
            a_, b_ = frames_of(flat[n - 1]["sel"], fps) / fps, frames_of(it["sel"], fps) / fps
            if 2 * d > min(a_, b_):
                add("dissolve", it["at"], f"叠化 {d}s 超过两侧选段（{a_:.2f}s、{b_:.2f}s）较短者的一半")

    used = {it["sel"].get("clip") for it in flat if isinstance(it["sel"], dict)}   # 引用了就算用了，越界另报
    unused = edl.get("unused") or {}
    for cid in ids:
        if cid in used and cid in unused:
            add("format", "unused", f"{cid} 用了又列在 unused")
        elif cid not in used and not unused.get(cid):
            add("unused", "unused", f"素材 {cid} 没用上，也没在 unused 写原因")

    for it in flat:
        s = it["sel"]
        for rule in (s.get("waive") or {}) if isinstance(s, dict) else {}:
            if rule in WAIVABLE and (id(s), rule) not in used_waives:
                notes.append(f"{it['at']} 的 waive.{rule} 没有对应的问题，可以删")

    t = 0.0
    for it in flat:
        t -= it["dissolve"] or 0.0
        it["t"], it["frames"] = round(t, 3), frames_of(it["sel"], fps) if it["ok"] else 0
        t += it["frames"] / fps

    for k, L in enumerate(edl.get("audio") or []):
        at = f"audio#{k}"
        if not isinstance(L, dict):
            add("format", at, "声音层要是对象")
            continue
        for key in set(L) - LAYER_KEYS:
            add("format", at, f"未知字段 {key}")
        if not L.get("why"):
            add("why", at, "没写 why")
        if not L.get("file") or not os.path.exists(resolve(base, L["file"])):
            add("format", at, f"找不到音频文件 {L.get('file')!r}（相对工作区 {base}）")
        spans = {key: anchor_span(flat, L[key], fps) for key in ("from", "to") if key in L}
        if "from" not in L:
            add("format", at, "缺 from 锚点")
        for key, sp_ in spans.items():
            if sp_ is None:
                add("format", at, f"{key}={L[key]!r} 不是场 id，也不是选段（如 C#6）")
        if spans.get("from") and spans.get("to") and spans["from"][0] + L.get("offset", 0.0) >= spans["to"][1]:
            add("format", at, "from 加 offset 晚于 to 的结尾")
        for key in ("offset", "in", "fade", "duck"):
            if key in L and not (is_num(L[key]) and L[key] >= 0):
                add("format", at, f"{key} 要是非负数")
        if "gain" in L and not is_num(L["gain"]):
            add("format", at, "gain 要是数")
    return found, notes, flat, round(t, 3)
