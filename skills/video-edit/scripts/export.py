"""export：从无字母版出别的画幅版本和封面。文字按新画幅重新排版，不跟着画面一起被裁。"""
import os, re, shutil
from common import Ws, die, grab, grid_note, jload, latest_cut, make_sheets, probe
from text import burn, layout

def even(x):
    return max(2, 2 * round(x / 2))

def steps(segs, key, fallback):
    """按段切换的常数表达式：v0 + Σ (vk - vk-1)·gte(t, Tk)。不嵌套，段再多也不会超出表达式深度。"""
    vals = [(s["t"], key(s)) for s in segs] or [(0.0, fallback)]
    expr, prev = f"{vals[0][1]}", vals[0][1]
    for t, v in vals[1:]:
        if v != prev:
            expr += f"{v - prev:+d}*gte(t,{t:.3f})"
            prev = v
    return expr

def cmd_export(a):
    ws = Ws(a.work)
    rep_path = ws.p("cuts", f"v{a.cut}.json") if a.cut else latest_cut(ws)
    if not rep_path or not os.path.exists(rep_path):
        die("还没有成片，先 edit.py render")
    rep = jload(rep_path)
    stem = re.sub(r"\.json$", "", rep_path)
    clean = rep.get("clean") or rep["out"]
    if a.cover is not None:
        out = f"{stem}_cover.png"
        grab(clean, a.cover, out, probe(clean)["width"])
        print(out)
    if not a.aspect:
        if a.cover is None:
            die("要 --aspect 宽:高，或 --cover 秒")
        return
    m = re.fullmatch(r"(\d+(?:\.\d+)?)[:x](\d+(?:\.\d+)?)", a.aspect)
    if not m or not float(m[2]):
        die("--aspect 写成 宽:高，如 9:16、1:1、4:5")
    ar = float(m[1]) / float(m[2])
    src = probe(clean)
    W, H, segs = src["width"], src["height"], rep["segments"]
    if a.fit == "blur":
        Ho = a.height or (even(min(W, H) / ar) if ar < 1 else min(W, H))
        Wo = even(Ho * ar)
        pre = (f"[0:v]split[a][b];[a]scale={Wo}:{Ho}:force_original_aspect_ratio=increase:flags=lanczos,crop={Wo}:{Ho},"
               f"boxblur=24:2[bg];[b]scale={Wo}:{Ho}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
               f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]")
        note = f"原画完整保留，四周是模糊的放大底"
    else:
        cw, ch = (even(H * ar), H) if ar < W / H else (W, even(W / ar))
        fx = lambda s: round(max(0, min(W - cw, (s.get("focus") or [0.5, 0.5])[0] * W - cw / 2)))
        fy = lambda s: round(max(0, min(H - ch, (s.get("focus") or [0.5, 0.5])[1] * H - ch / 2)))
        Ho = a.height or ch
        Wo = even(Ho * cw / ch)
        pre = f"[0:v]crop={cw}:{ch}:'{steps(segs, fx, (W - cw) // 2)}':'{steps(segs, fy, (H - ch) // 2)}'" + \
              (f",scale={Wo}:{Ho}:flags=lanczos" if (Wo, Ho) != (cw, ch) else "") + "[v0]"
        nofocus = [s["at"] for s in segs if s.get("clip") not in ("gap", "white", "black") and not s.get("focus")]
        note = f"从 {W}x{H} 裁出 {cw}x{ch}" + ("，放大到输出尺寸，画面会发软" if Ho > ch else "") + \
               (f"。{len(nofocus)} 段没写 focus，按画面正中裁：{' '.join(nofocus)}" if nofocus else "")
    name = f"{stem}_{m[1]}x{m[2]}" + ("_blur" if a.fit == "blur" else "") + ("_notext" if a.no_text else "")
    out, tmp = name + ".mp4", ws.p("tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    burn(clean, [] if a.no_text else layout(rep.get("text") or [], Wo, Ho, tmp), out, rep["fps"], pre)
    shutil.rmtree(tmp, ignore_errors=True)
    sheets, cols, rows = make_sheets(out, name + "_sheet", 1, Wo, Ho, tile_w=240 if Wo >= Ho else 160)
    print(f"{out}\n  {Wo}x{Ho}，{note}")
    print(f"  触点图 {len(sheets)} 张，{grid_note(0, 1, cols, rows)}。逐张看主体有没有被裁出画、文字有没有压到脸")
    print("\n".join(f"  {s}" for s in sheets))
