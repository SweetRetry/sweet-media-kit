"""字幕和标题：文字写成 HTML，无头浏览器排版并截成透明 PNG，ffmpeg 按时间叠到画面上。成片和各画幅版本共用同一份文字计划。"""
import html, json, os, re, shutil
from common import die, resolve, run
from rules import anchor_span, subs_of

BROWSERS = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "google-chrome", "google-chrome-stable",
            "chromium", "chromium-browser", "microsoft-edge", "chrome",
            "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"]
FAMILY = "'PingFang SC','Hiragino Sans GB','Noto Sans CJK SC','Source Han Sans SC','Microsoft YaHei',sans-serif"   # 进 style 属性，名字用单引号
FONT_FILES = (".ttf", ".otf", ".woff", ".woff2")   # 浏览器能按文件加载的字体。.ttc 字体集不行
SUB_MIN = 0.6             # 一条字幕至少停这么久，除非下一条先到
SUB_ROWS = 2              # 一屏最多几行，超出的拆成前后两条
WRAP = 0.86               # 文字最宽占画宽的比例
SHEET_H = 8000            # 一张截图的高度上限（像素），文字块排满就另起一张

def find_browser():
    env = os.environ.get("VIDEO_EDIT_BROWSER")
    for b in ([env] if env else []) + BROWSERS:
        p = b if os.path.isabs(b) else shutil.which(b)
        if p and os.path.exists(p):
            return p
    die("字幕和标题靠 Chromium 内核的浏览器（Chrome、Chromium、Edge）排版，没找到。"
        "装一个，或把它的可执行文件路径写进环境变量 VIDEO_EDIT_BROWSER")

def font_of(path, base):
    return resolve(base, path) if path else None

def text_plan(edl, flat, lines, fps, total, base):
    """成片时间上的文字计划，和画幅无关。lines 是成片台词（cut_lines 的结果，带 t_end）。"""
    plan, st = [], subs_of(edl)
    if st is not None:
        font = font_of(st.get("font"), base)
        for l, nxt in zip(lines, lines[1:] + [None]):
            end = min(max(l["t_end"], l["t"] + SUB_MIN), nxt["t"] if nxt else total, total)
            if end - l["t"] > 0.05 and l["text"].strip():
                plan.append({"kind": "sub", "t": l["t"], "end": round(end, 2), "text": l["text"].strip(),
                             "size": st.get("size", 0.045), "margin": st.get("margin", 0.08),
                             "color": st.get("color", "#FFFFFF"), "font": font})
    for L in edl.get("titles") or []:
        a, b = anchor_span(flat, L["from"], fps)
        t = a + L.get("offset", 0.0)
        end = min(total, t + L["dur"] if "dur" in L else anchor_span(flat, L["to"], fps)[1] if "to" in L else b)
        plan.append({"kind": "title", "t": round(t, 3), "end": round(end, 3), "text": L["text"],
                     "pos": L.get("pos", [0.5, 0.5]), "size": L.get("size", 0.08), "fade": L.get("fade", 0.3),
                     "color": L.get("color", "#FFFFFF"), "font": font_of(L.get("font"), base)})
    return plan

def write_srt(lines, total, path):
    def tc(t):
        ms = round(t * 1000)
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    with open(path, "w") as f:
        for k, (l, nxt) in enumerate(zip(lines, lines[1:] + [None]), 1):
            end = min(max(l["t_end"], l["t"] + SUB_MIN), nxt["t"] if nxt else total, total)
            f.write(f"{k}\n{tc(l['t'])} --> {tc(end)}\n{l['text'].strip()}\n\n")

MEASURE = """<pre id="out"></pre><script>
const out=[];document.querySelectorAll('.b').forEach(el=>{const t=el.firstChild,r=document.createRange(),rows=[];let y0=null;
for(let i=0;t&&i<t.length;i++){r.setStart(t,i);r.setEnd(t,i+1);const q=r.getClientRects()[0];
if(q&&q.width>0){const y=Math.round(q.top);if(y0===null||Math.abs(y-y0)>2){rows.push({s:'',l:q.left,r:q.right});y0=y}
const o=rows[rows.length-1];o.l=Math.min(o.l,q.left);o.r=Math.max(o.r,q.right)}
if(rows.length&&t.data[i]!=='\\n')rows[rows.length-1].s+=t.data[i]}
out.push(rows.map(o=>({s:o.s.trim(),w:Math.ceil(o.r-o.l)})).filter(o=>o.s))});
document.getElementById('out').textContent=JSON.stringify(out);</script>"""

def browse(browser, tmp, page, w, h, shot=None):
    """打开一页 HTML。给 shot 就截成透明 PNG，否则返回页面脚本写进 #out 的 JSON。"""
    args = [browser, "--headless", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
            f"--user-data-dir={tmp}/browser", "--default-background-color=00000000", f"--window-size={w},{h}"]
    r = run(args + ([f"--screenshot={shot}"] if shot else ["--dump-dom"]) + ["file://" + os.path.abspath(page)])
    if shot:
        if not os.path.exists(shot):
            die(f"浏览器没有截出图：{shot}")
        return None
    m = re.search(r'<pre id="out">(.*?)</pre>', r.stdout, re.S)
    if not m or not m.group(1):
        die("浏览器没有返回排版结果，页面脚本没跑完")
    return json.loads(html.unescape(m.group(1)))

def styled(plan, H):
    """每条文字的字号、描边、行高，和页面开头的样式表。带字体文件的条目各用一个 @font-face。"""
    css = ["html,body{margin:0;background:transparent}",
           ".b{position:absolute;left:0;box-sizing:border-box;font-weight:600;text-align:center;"
           "paint-order:stroke fill;-webkit-text-stroke-color:#000;line-break:strict;overflow-wrap:anywhere}"]
    st = []
    for k, it in enumerate(plan):
        px = max(12, round(it["size"] * H))
        sw, fam = max(2, round(px * 0.07)), FAMILY
        if it.get("font") and it["font"].lower().endswith(FONT_FILES):
            css.append(f"@font-face{{font-family:f{k};src:url('file://{os.path.abspath(it['font'])}')}}")
            fam = f"f{k},{FAMILY}"
        st.append({"sw": sw, "lh": round(px * 1.45), "css": f"font-family:{fam};font-size:{px}px;line-height:{round(px * 1.45)}px;"
                   f"color:{it['color']};-webkit-text-stroke-width:{2 * sw}px"})
    return "<!doctype html><meta charset=\"utf-8\"><style>" + "".join(css) + "</style>", st

def layout(plan, W, H, tmp):
    """文字计划按画幅排版，画成 PNG。返回叠加项：文件、成片起止、左上角、淡入淡出。"""
    if not plan:
        return []
    browser = find_browser()
    os.makedirs(tmp, exist_ok=True)
    head, st = styled(plan, H)
    # 第一遍：浏览器折行，读回每行的字和宽度
    page = f"{tmp}/text_measure.html"
    with open(page, "w") as f:
        f.write(head + "".join(f'<div class="b" style="top:0;width:max-content;max-width:{round(WRAP * W)}px;white-space:pre-line;{s["css"]}">'
                               f'{html.escape(it["text"])}</div>' for it, s in zip(plan, st)) + MEASURE)
    measured = browse(browser, tmp, page, W, H)
    # 第二遍：每条文字（字幕超过 SUB_ROWS 行的拆成前后几条）排成一个定宽定高的块，竖着码在长图上截下来
    blocks = []
    for k, (it, s, rows) in enumerate(zip(plan, st, measured)):
        rows = rows or [{"s": "", "w": 0}]
        for j, c in enumerate([rows] if it["kind"] == "title" else [rows[i:i + SUB_ROWS] for i in range(0, len(rows), SUB_ROWS)]):
            w, h = max(r["w"] for r in c) + 4 * s["sw"], len(c) * s["lh"] + 4 * s["sw"]
            blocks.append({"k": k, "j": j, "rows": [r["s"] for r in c], "w": w + w % 2, "h": h + h % 2, "s": s})
    sheets, y = [[]], 0
    for b in blocks:
        if y and y + b["h"] > SHEET_H:
            sheets.append([])
            y = 0
        b["y"], y = y, y + b["h"]
        sheets[-1].append(b)
    for n, sheet in enumerate(sheets):
        page, shot = f"{tmp}/text_sheet{n}.html", f"{tmp}/text_sheet{n}.png"
        with open(page, "w") as f:
            f.write(head + "".join(f'<div class="b" style="top:{b["y"]}px;width:{b["w"]}px;height:{b["h"]}px;padding-top:{2 * b["s"]["sw"]}px;'
                                   f'white-space:pre;{b["s"]["css"]}">{"<br>".join(html.escape(r) for r in b["rows"])}</div>' for b in sheet))
        browse(browser, tmp, page, max(b["w"] for b in sheet), sheet[-1]["y"] + sheet[-1]["h"], shot)
        for b in sheet:
            b["file"] = f"{tmp}/t{b['k']:03d}_{b['j']}.png"
            run(["ffmpeg", "-v", "error", "-y", "-i", shot, "-vf", f"crop={b['w']}:{b['h']}:0:{b['y']}", "-frames:v", "1", b["file"]])
    out = []
    for k, it in enumerate(plan):
        mine = [b for b in blocks if b["k"] == k]
        if it["kind"] == "title":
            b = mine[0]
            x, y = round(it["pos"][0] * W - b["w"] / 2), round(it["pos"][1] * H - b["h"] / 2)
            out.append({"file": b["file"], "t": it["t"], "end": it["end"], "fade": it["fade"],
                        "x": max(0, min(W - b["w"], x)), "y": max(0, min(H - b["h"], y))})
            continue
        chars, t = sum(len("".join(b["rows"])) for b in mine) or 1, it["t"]   # 一句话拆成几条时，按字数分时长
        for j, b in enumerate(mine):
            end = it["end"] if j == len(mine) - 1 else t + (it["end"] - it["t"]) * len("".join(b["rows"])) / chars
            out.append({"file": b["file"], "t": round(t, 3), "end": round(end, 3), "fade": 0.0,
                        "x": round((W - b["w"]) / 2), "y": max(0, round(H * (1 - it["margin"]) - b["h"]))})
            t = end
    return out

def burn(src, overlays, out, fps, pre="[0:v]null[v0]"):
    """pre 是从 [0:v] 到 [v0] 的滤镜图（裁切、缩放），文字叠在它之后。音频原样复制。"""
    ins, fc, cv = [], [pre], "[v0]"
    for k, o in enumerate(overlays, 1):
        dur, f = o["end"] - o["t"], o["fade"]
        if f and dur > 2 * f:
            ins += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur:.3f}", "-i", o["file"]]
            fc.append(f"[{k}:v]format=rgba,fade=t=in:st=0:d={f}:alpha=1,fade=t=out:st={dur - f:.3f}:d={f}:alpha=1,"
                      f"setpts=PTS+{o['t']:.3f}/TB[o{k}]")
            src_k, eof = f"[o{k}]", ":eof_action=pass"
        else:
            ins += ["-i", o["file"]]
            src_k, eof = f"[{k}:v]", ""
        fc.append(f"{cv}{src_k}overlay={o['x']}:{o['y']}:enable='between(t,{o['t']:.3f},{o['end']:.3f})'{eof}[v{k}]")
        cv = f"[v{k}]"
    fc.append(f"{cv}format=yuv420p[vo]")
    run(["ffmpeg", "-v", "error", "-y", "-i", src] + ins + ["-filter_complex", ";".join(fc), "-map", "[vo]", "-map", "0:a?",
         "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "copy",
         "-movflags", "+faststart", out])
