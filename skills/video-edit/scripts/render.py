"""render：逐段出画面（变速、运镜、色彩匹配），叠化拼接，全片调色和质感，混音，出成片报告。"""
import array, glob, json, math, os, re, shutil, statistics
from common import (EPS, GAIN_CAP, HANDLE, REF_LUFS, SEAM_JUMP, SHEET_BUDGET, Ws, decode16k, die, frames_of,
    grid_note, integrated_lufs, jdump, jload, latest_cut, make_sheets, out_at, pieces, probe, resolve, rms_db, run,
    sha1)
from analyze import pixel_stats
from rules import anchor_span, check_edl, grade_of, sound_of, zoom_of

def ramp_pts(sel):
    """setpts 表达式：输入帧时间 T（从入点算起）到成片时间，和 pieces 的积分一致。"""
    S, expr = f"(T+{sel['in']:.4f})", None
    for a, b, va, vb, o in reversed(pieces(sel)):
        e = f"{o:.6f}+(" + (f"({S}-{a:.4f})/{va:.6f}" if abs(vb - va) < EPS else
                           f"{(b - a) / (vb - va):.6f}*log(({va:.6f}+{(vb - va) / (b - a):.6f}*({S}-{a:.4f}))/{va:.6f})") + ")"
        expr = e if expr is None else f"if(lt({S},{b:.4f}),{e},{expr})"
    return f"setpts='({expr})/TB'"

def camera(sel, T):
    """perspective 滤镜做推拉和震动，亚像素插值，慢推不会一像素一像素地跳。
    震动频率低于 24fps 的奈奎斯特频率 12Hz，否则会混叠成逐帧左右跳。"""
    z0, z1 = zoom_of(sel)
    shakes = sel.get("shake") or []
    if z0 == z1 == 1.0 and not shakes:
        return []
    W, H, F = T["width"], T["height"], T["fps"]
    D = max(out_at(sel, sel["out"]), 1 / F)
    u = f"((in-1)/{F})"   # perspective 的 in 从 1 数起
    z = f"({z0}+{z1 - z0:.6f}*clip({u}/{D:.4f},0,1))"
    cx, cy = sel.get("center", [0.5, 0.5])
    dx, dy = ["0"], ["0"]
    for k in shakes:
        t0, d = out_at(sel, k["at"]), k["dur"]
        env = f"between({u},{t0:.4f},{t0 + d:.4f})*(1-({u}-{t0:.4f})/{d})"
        dx.append(f"{k['amp'] * W:.3f}*{env}*sin(2*PI*8*({u}-{t0:.4f}))")
        dy.append(f"{k['amp'] * 0.7 * W:.3f}*{env}*sin(2*PI*6*({u}-{t0:.4f})+1.3)")
    x = f"clip({cx}*{W}-{W}/(2*{z})+{'+'.join(dx)},0,{W}-{W}/{z})"
    y = f"clip({cy}*{H}-{H}/(2*{z})+{'+'.join(dy)},0,{H}-{H}/{z})"
    xr, yb = f"{x}+{W}/{z}", f"{y}+{H}/{z}"
    q = dict(x0=x, y0=y, x1=xr, y1=y, x2=x, y2=yb, x3=xr, y3=yb)
    return ["perspective=" + ":".join(f"{k}='{v}'" for k, v in q.items()) + ":interpolation=cubic:eval=frame"]

def seg_video(ws, it, out, T, look):
    sel = it["sel"]
    n = frames_of(sel, T["fps"])
    W, H, F = T["width"], T["height"], T["fps"]
    enc = ["-an", "-c:v", "libx264", "-crf", "16", "-preset", "fast", "-pix_fmt", "yuv420p", out]
    if "clip" not in sel:   # 色帧，或缺镜头的灰色占位
        run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={sel.get('color', 'gray')}:s={W}x{H}:r={F}",
             "-frames:v", str(n)] + enc)
        return
    g = it.get("grade")
    vf = (["hflip"] if sel.get("flip") else []) + (["deflicker=size=5:mode=pm"] if look.get("deflicker") else []) + \
         [ramp_pts(sel)] + ([g[0]] if g else []) + \
         [f"fps={F}", f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos",
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2", "setsar=1"] + camera(sel, T) + \
         [f"tpad=stop_mode=clone:stop_duration={sel.get('hold', 0.0) + 2 / F:.4f}"]   # 定格，顺带补齐末尾差的一两帧
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{sel['in']:.3f}", "-t", f"{sel['out'] - sel['in']:.3f}",
         "-i", ws.src(sel["clip"]), "-vf", ",".join(vf), "-frames:v", str(n)] + enc)

def build_block(ws, k, its, tmp, T, look):
    """一组硬切相连的选段，画面直接拼接。"""
    vs = []
    for i, it in enumerate(its):
        vs.append(f"{tmp}/b{k}v{i:02d}.mp4")
        seg_video(ws, it, vs[-1], T, look)
        it["file"] = vs[-1]
    with open(f"{tmp}/b{k}.txt", "w") as f:
        f.write("".join(f"file '{v}'\n" for v in vs))
    vid = f"{tmp}/block{k}.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", f"{tmp}/b{k}.txt", "-c", "copy", vid])
    return vid, sum(frames_of(x["sel"], T["fps"]) for x in its) / T["fps"]

def look_filters(look):
    """全片调色和质感，作用在叠化之后、淡入淡出之前。LUT 已复制成工作目录下的 look.cube。"""
    out = ["lut3d=file=look.cube:interp=tetrahedral"] if look.get("lut") else []
    eq = {k: look[k] for k in ("contrast", "saturation", "gamma") if k in look}
    out += ["eq=" + ":".join(f"{k}={v}" for k, v in eq.items())] if eq else []
    out += [f"colortemperature=temperature={look['temperature']}"] if "temperature" in look else []
    if "shadows" in look or "highlights" in look:
        (rs, gs, bs), (rh, gh, bh) = look.get("shadows", [0, 0, 0]), look.get("highlights", [0, 0, 0])
        out.append(f"colorbalance=rs={rs}:gs={gs}:bs={bs}:rh={rh}:gh={gh}:bh={bh}")
    out += [f"vignette=angle={look['vignette'] * math.pi / 2:.4f}"] if look.get("vignette") else []
    out += [f"noise=alls={look['grain']}:allf=t"] if look.get("grain") else []
    return out

def norm_gain(lufs):
    return 0.0 if lufs is None or lufs < -60 else max(-GAIN_CAP, min(GAIN_CAP, REF_LUFS - lufs))

def sync_placements(ws, edl, flat, facts, fps):
    out = []
    for n, it in enumerate(flat):
        s = it["sel"]
        snd = sound_of(s, edl)
        if "clip" not in s or snd == "none" or not facts[s["clip"]]["audio"]:
            continue
        f, sp = facts[s["clip"]], s.get("speed", 1.0)
        nxt = flat[n + 1] if n + 1 < len(flat) else None
        hard_in, hard_out = n > 0 and not it["dissolve"], nxt is not None and not nxt["dissolve"]
        lead, lag = s.get("lead", 0.0), s.get("lag", 0.0)
        ext_in, ext_out = lead + (HANDLE if hard_in else 0.0), lag + (HANDLE if hard_out else 0.0)
        at, s0, s1 = it["t"] - ext_in, s["in"] - ext_in * sp, min(f["duration"], s["out"] + ext_out * sp)
        if s0 < 0 or at < 0:
            cut = max(-s0 / sp, -at)
            at, s0 = at + cut, s0 + cut * sp
        src, lufs = (ws.src(s["clip"]), f["audio"].get("lufs")) if snd == "mix" else \
                    (ws.p(f["stems"][snd]["file"]), f["stems"][snd]["lufs"])
        out.append({"label": it["at"], "src": src, "s0": s0, "s1": s1, "speed": sp, "at": at,
                    "fi": 2 * HANDLE if hard_in or lead else it["dissolve"] or 0.0,
                    "fo": 2 * HANDLE if hard_out or lag else (nxt or {}).get("dissolve") or 0.0,
                    "gain": norm_gain(lufs) + s.get("gain", 0.0)})
    return out

def audio_duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]).stdout)

def layer_placements(ws, edl, flat, fps, total, speech):
    out = []
    for k, L in enumerate(edl.get("audio") or []):
        path = resolve(ws.dir, L["file"])
        start = anchor_span(flat, L["from"], fps)[0] + L.get("offset", 0.0)
        end = anchor_span(flat, L["to"], fps)[1] if "to" in L else total
        s0 = L.get("in", 0.0)
        s1 = min(s0 + end - start, audio_duration(path))
        fade = L.get("fade", 0.5)
        duck = [(a - start, b - start) for a, b in speech if b > start and a < start + s1 - s0] if L.get("duck") else []
        out.append({"label": f"audio#{k}", "src": path, "s0": s0, "s1": s1, "speed": 1.0, "at": start, "fi": fade,
                    "fo": fade, "gain": norm_gain(integrated_lufs(path)) + L.get("gain", 0.0),
                    "duck": (L.get("duck"), duck)})
    return out

def duck_expr(db, spans, ramp=0.15):
    """有台词的区间压低 db，两头各用 ramp 秒斜坡，免得听到台阶。"""
    g = 10 ** (-db / 20)
    terms = [f"clip((t-{a - ramp:.3f})/{ramp},0,1)*clip(({b + ramp:.3f}-t)/{ramp},0,1)" for a, b in spans]
    m = terms[0]
    for x in terms[1:]:
        m = f"max({m},{x})"
    return f"1-{1 - g:.4f}*{m}"

def render_sound(placements, total, fi, fo, tmp):
    files = []
    for i, p in enumerate(placements):
        length = (p["s1"] - p["s0"]) / p["speed"]
        if length <= 0.01:
            continue
        af = [f"atrim=start={p['s0']:.4f}:end={p['s1']:.4f}", "asetpts=PTS-STARTPTS"] + \
             ([f"atempo={p['speed']}"] if p["speed"] != 1.0 else []) + [f"volume={p['gain']:.2f}dB"]
        db, spans = p.get("duck") or (0, [])
        if db and spans:
            af.append(f"volume='{duck_expr(db, spans)}':eval=frame")
        af += ([f"afade=t=in:d={p['fi']:.4f}"] if p["fi"] else []) + \
              ([f"afade=t=out:st={max(0.0, length - p['fo']):.4f}:d={p['fo']:.4f}"] if p["fo"] else [])
        files.append((f"{tmp}/s{i:03d}.wav", p["at"]))
        run(["ffmpeg", "-v", "error", "-y", "-i", p["src"], "-vn", "-af", ",".join(af), "-ar", "48000", "-ac", "2", files[-1][0]])
    out = f"{tmp}/sound.wav"
    tail = f"apad=whole_dur={total:.4f},atrim=duration={total:.4f}" + \
           (f",afade=t=in:d={fi}" if fi else "") + (f",afade=t=out:st={total - fo:.4f}:d={fo}" if fo else "")
    if not files:
        run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{total:.4f}", out])
        return out
    # 在 Python 里按采样叠加。amix 输入一多，会随线程调度时有时无地丢掉后面的某一路，补齐长度也拦不住
    sr, n = 48000, round(total * 48000)
    acc = array.array("f", bytes(8 * n))
    for f, at in files:
        raw = run(["ffmpeg", "-v", "error", "-i", f, "-ac", "2", "-ar", str(sr), "-f", "f32le", "-"], binary=True).stdout
        s = array.array("f")
        s.frombytes(raw[: len(raw) // 8 * 8])
        o = 2 * round(at * sr)
        for j in range(min(len(s), 2 * n - o)):
            acc[o + j] += s[j]
    raw = f"{tmp}/sound.f32"
    with open(raw, "wb") as fh:
        acc.tofile(fh)
    run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", raw,
         "-af", tail, "-c:a", "pcm_s16le", out])
    verify_mix(out, files, fi, fo, total)
    return out

def verify_mix(mix, files, fi, fo, total):
    """每路声音在混音里的能量不该比它自己低 20 dB 以上。叠加只会加能量，掉了就是混音坏了。"""
    m, lost = decode16k(mix), []
    for f, at in files:
        own = decode16k(f)
        dur = len(own) / 16000
        a, b = max(at, fi), min(at + dur, total - fo)   # 片头片尾的淡入淡出本来就压低，跳过
        if b - a < 0.2:
            continue
        mine = rms_db(own, a - at, b - at)
        if mine > -60 and rms_db(m, a, b) < mine - 20:
            lost.append(f"{os.path.basename(f)} 在 {a:.2f}-{b:.2f}s 自身 {mine} dB，混音里 {rms_db(m, a, b)} dB")
    if lost:
        die("混音丢了声音：\n  " + "\n  ".join(lost))

def seam_jumps(out, flat):
    """每个剪点前后 0.3 秒的响度差。听不到，但量得出来。"""
    a = decode16k(out)
    rows = []
    for it in flat[1:]:
        b, c = rms_db(a, it["t"] - 0.3, it["t"]), rms_db(a, it["t"], it["t"] + 0.3)
        rows.append({"t": it["t"], "at": it["at"], "before_db": b, "after_db": c, "jump": round(abs(c - b), 1)})
    return rows

def cmd_render(a):
    ws = Ws(a.work)
    T, facts = ws.project["target"], ws.facts()
    edl = jload(ws.p("edl.json"))
    found, _, flat, total = check_edl(edl, facts, ws.log(), ws.ids, ws.project["target"], ws.dir)
    if any(not it["ok"] for it in flat) or not flat:
        die("edl.json 有选段格式不对或越界，先跑 edit.py check")
    if (todo := [x for x in found if not x.get("waived")]) and not a.force:
        die(f"edl.json 有 {len(todo)} 处未处理的问题，先跑 edit.py check。只要草稿就加 --force")
    blocks, joins = [], []
    for it in flat:
        if not blocks or it["dissolve"]:
            if blocks:
                joins.append(it["dissolve"])
            blocks.append([])
        blocks[-1].append(it)
        it["grade"] = grade_of(it, flat, facts)
    tmp = ws.p("tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    look = edl.get("look") or {}
    if look.get("lut"):
        shutil.copy(resolve(ws.dir, look["lut"]), f"{tmp}/look.cube")
    built = [build_block(ws, k, b, tmp, T, look) for k, b in enumerate(blocks)]
    grades = grade_report(flat, facts, T)
    fi, fo = edl.get("fade_in", 0.3), edl.get("fade_out", 0.8)
    lines = cut_lines(flat, facts, edl)
    placements = sync_placements(ws, edl, flat, facts, ws.fps)
    placements += layer_placements(ws, edl, flat, ws.fps, total, [(l["t"], l["t_end"]) for l in lines])
    sound = render_sound(placements, total, fi, fo, tmp)

    ins = [x for v, _ in built for x in ("-i", v)] + ["-i", sound]
    vf, off, cv = [], built[0][1], "[0:v]"
    for k in range(1, len(built)):
        d = joins[k - 1]
        vf.append(f"{cv}[{k}:v]xfade=transition=fade:duration={d}:offset={off - d:.4f}[v{k}]")
        cv, off = f"[v{k}]", off - d + built[k][1]
    vfade = ([f"fade=t=in:d={fi}"] if fi else []) + ([f"fade=t=out:st={off - fo:.4f}:d={fo}"] if fo else [])
    vf.append(f"{cv}{','.join(look_filters(look) + vfade + ['format=yuv420p'])}[vo]")
    mix = f"{tmp}/mix.mkv"
    run(["ffmpeg", "-v", "error", "-y"] + ins + ["-filter_complex", ";".join(vf), "-map", "[vo]", "-map", f"{len(built)}:a",
         "-c:v", "libx264", "-crf", "16", "-preset", "medium"] + (["-tune", "grain"] if look.get("grain") else []) +
        ["-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", mix], cwd=tmp)

    target = edl.get("lufs", -16)
    ln = f"loudnorm=I={target}:TP=-1.5:LRA=11"
    err = run(["ffmpeg", "-hide_banner", "-i", mix, "-vn", "-af", ln + ":print_format=json", "-f", "null", "-"]).stderr
    m = json.loads(err[err.rindex("{"):err.rindex("}") + 1])
    ln2 = (f"{ln}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
           f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    last = latest_cut(ws)   # 版本号跟报告走，渲染失败留下的半成品会被覆盖
    n = 1 + (int(re.search(r"v(\d+)\.json$", last).group(1)) if last else 0)
    out = ws.p("cuts", f"v{n}.mp4")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    run(["ffmpeg", "-v", "error", "-y", "-i", mix] + (["-af", ln2] if m["input_i"] != "-inf" else []) + ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", out])
    shutil.rmtree(tmp, ignore_errors=True)

    W, H = T["width"], T["height"]
    sheets, cols, rows = make_sheets(out, ws.p("cuts", f"v{n}_sheet"), 1, W, H, tile_w=240 if W >= H else 160)
    seams, seam_rows = seam_sheets(out, ws.p("cuts", f"v{n}_seams"), flat, T)
    lines = [{k: v for k, v in l.items() if k not in ("key", "t_end")} for l in lines]
    jumps = seam_jumps(out, flat)
    loud = [j for j in jumps if j["jump"] >= SEAM_JUMP]
    lines_txt = ws.p("cuts", f"v{n}_lines.txt")
    with open(lines_txt, "w") as f:
        f.write(f"成片台词表。时间是成片里的秒。触点图{grid_note(0, 1, cols, rows)}\n\n")
        f.write("".join(f"{l['t']:7.2f}  {l['text']}\n" for l in lines) or "（没有台词）\n")
    rep = {"out": out, "edl_sha1": sha1(ws.p("edl.json")), "duration": probe(out)["duration"], "expected": total,
           "lufs": integrated_lufs(out), "target_lufs": target, "size": f"{W}x{H}", "fps": T["fps"],
           "sheets": sheets, "sheet_grid": grid_note(0, 1, cols, rows),
           "seams": seams, "seam_rows": seam_rows, "lines": lines_txt, "seam_jumps": jumps,
           "grade": grades,
           "sound": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in p.items() if k not in ("src", "duck")}
                     for p in placements],
           "segments": [{"t": it["t"], "at": it["at"], "clip": it["sel"].get("clip") or it["sel"].get("color") or "gap",
                         "in": it["sel"].get("in"), "out": it["sel"].get("out"), "frames": it["frames"]} for it in flat]}
    jdump(rep, ws.p("cuts", f"v{n}.json"))
    drift = rep["duration"] - total
    print(f"{out}\n  {rep['duration']}s（预计 {total}s，差 {drift:+.2f}s）  {rep['lufs']} LUFS（目标 {target}）")
    print(f"  整片触点图 {len(sheets)} 张，{rep['sheet_grid']}")
    print(f"  剪点图 {len(seams)} 张，每行一个剪点：出点前两帧 | 入点后两帧，行与剪点的对应见 vN.json 的 seam_rows")
    print(f"  成片台词表 {lines_txt}")
    for g in grades:
        print(f"  色彩匹配 {g['at']} 对基准 {g['ref']}：均值差 {g['before']} → {g['after']}" + ("，已到校正上限" if g["capped"] else ""))
    if moving := [it["at"] for it in flat if it["ok"] and "clip" in it["sel"] and
                  (it["sel"].get("ramp") or it["sel"].get("hold") or it["sel"].get("shake") or "zoom" in it["sel"])]:
        print(f"  运动效果 {len(moving)} 段（{' '.join(moving)}）：手感看不到，进看片清单")
    print(f"  剪点响度跳变 ≥ {SEAM_JUMP:g} dB 的 {len(loud)} 处（进听片清单）：" +
          "，".join(f"{j['t']:.2f}s 进 {j['at']} {j['before_db']}→{j['after_db']}" for j in loud))
    print("\n".join(f"  {s}" for s in sheets + seams))

def grade_report(flat, facts, T):
    """色彩匹配前后，段均值和基准均值的 RGB 距离。after 量的是渲染出的段，不是算出来的。"""
    rows = []
    for it in flat:
        g = it.get("grade")
        if not g:
            continue
        info = g[1]
        st = pixel_stats(it["file"], T)
        after = [statistics.mean(r[c] for r in st["color"]) for c in range(3)]
        dist = lambda m: round(math.dist(m, info["ref_mean"]), 1)
        rows.append({"at": it["at"], "ref": info["ref"], "before": dist(info["src_mean"]), "after": dist(after),
                     "capped": info["capped"]})
    return rows

def seam_sheets(out, prefix, flat, T):
    """每个剪点一行：前一段最后两帧，后一段最前两帧。"""
    fps, cuts = T["fps"], flat[1:]
    if not cuts:
        return [], []
    tile_w = 240 if T["width"] >= T["height"] else 160
    per = max(1, SHEET_BUDGET // round(tile_w * T["height"] / T["width"]))
    tmp = prefix + "_frames"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    rows = []
    for k, it in enumerate(cuts):
        first = max(0, round(it["t"] * fps) - 2)
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{(first - 0.25) / fps:.4f}", "-i", out, "-frames:v", "4",
             "-vf", f"scale={tile_w}:-2", "-q:v", "3", f"{tmp}/r{k:03d}_%d.jpg"])
        got = sorted(glob.glob(f"{tmp}/r{k:03d}_*.jpg"))
        rows.append((got + got[-1:] * 4)[:4])
    for old in glob.glob(glob.escape(prefix) + "_[0-9][0-9].jpg"):
        os.remove(old)
    files = []
    for k in range(0, len(rows), per):
        chunk = rows[k:k + per]
        lst = f"{tmp}/list{k}.txt"
        with open(lst, "w") as f:
            f.write("".join(f"file '{x}'\n" for r in chunk for x in r))
        dst = f"{prefix}_{len(files) + 1:02d}.jpg"
        run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
             "-vf", f"tile=4x{len(chunk)}", "-frames:v", "1", "-q:v", "3", dst])
        files.append(dst)
    shutil.rmtree(tmp, ignore_errors=True)
    labels = [f"{(k // per) + 1}-{k % per}: {it['t']:.2f}s 进 {it['at']}" + (f"（叠化 {it['dissolve']}s）" if it["dissolve"] else "")
              for k, it in enumerate(cuts)]
    return files, labels

def cut_lines(flat, facts, edl):
    """同期声里听得到的台词，换算到成片时间。"""
    out = []
    for it in flat:
        s = it["sel"]
        if "clip" not in s or sound_of(s, edl) not in ("mix", "voice"):
            continue
        sp = s.get("speed", 1.0)
        a0, a1 = s["in"] - s.get("lead", 0.0) * sp, s["out"] + s.get("lag", 0.0) * sp
        for l in facts[s["clip"]].get("lines") or []:
            seen = out and out[-1]["key"] == (s["clip"], l["t0"])   # 连续镜头跨两段的同一句只记一次
            if l["t0"] < a1 and l["t1"] > a0 and not seen:
                out.append({"t": round(it["t"] + (max(l["t0"], a0) - s["in"]) / sp, 2),
                            "t_end": round(it["t"] + (min(l["t1"], a1) - s["in"]) / sp, 2), "at": it["at"],
                            "text": l["text"], "key": (s["clip"], l["t0"])})
    return out
