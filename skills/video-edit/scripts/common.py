"""公共部分：阈值常量、工作区、ffprobe、选段的时间映射、触点图、音频小工具。"""
import array, glob, hashlib, json, math, os, re, subprocess, sys

VIDEO_EXT = (".mp4", ".mov", ".mkv", ".m4v", ".webm")
SHEET_BUDGET = 1600       # 触点图长宽上限（像素）。Read 读更大的图会缩小，细节丢失
SCENE_MIN_GAP = 0.30      # 相邻切点候选的最小间隔
ENV_STEP, SILENCE_DB, SILENCE_MIN = 0.1, -50.0, 0.3
SLIVER = 0.25             # 选段边缘带进另一个镜头不足这么长，就是残帧
LINE_TAIL = 0.10          # 出点至少在句尾之后这么多
COARSE_FPS, COARSE_NEAR = 4, 1.0   # 低于 4fps 看过的崩坏，选段边缘离它不到 1 秒就要复看
BLACK_FRAMES = 2           # 选段里含黑场达到这么多帧就算问题；有意的黑场停顿用 black 色帧
HANDLE = 0.06             # 硬切处声音交叉淡化 = 2 × HANDLE，以剪点为中心
REF_LUFS, GAIN_CAP = -23.0, 12.0   # 每个声音放置先归到同一响度，增益上限 ±12 dB，混完再整体归一
SOUNDS = ("mix", "voice", "bed", "none")   # 选段取哪路声音：原声、人声、伴奏、静音
# 人声分离命令，{in} 是输入 wav，{out} 是输出目录，要在里面写出 voice.wav 和 bed.wav。project.json 的 separator 可覆盖
SEPARATOR = ["audio-separator", "{in}", "-m", "model_bs_roformer_ep_317_sdr_12.9755.ckpt", "--output_format", "WAV",
             "--output_dir", "{out}", "--model_file_dir", "~/.cache/audio-separator",
             "--custom_output_names", '{"Vocals": "voice", "Instrumental": "bed"}']
SEAM_JUMP = 10.0          # 剪点前后 0.3 秒响度差超过这么多 dB，进听片清单。实测全片相邻窗自然起伏 p90 约 9 dB
SLOW = 0.75               # 低于这个倍速的慢放会放大崩坏，慢放区间要 6fps 看过
MATCH_STRENGTH = 0.8      # 色彩匹配向基准靠拢的比例，留一点素材本来的差别
MATCH_GAIN, MATCH_SHIFT = (0.75, 1.33), 40   # 每个通道对比度倍数和均值平移（0-255）的上限，超出说明基准选错了
STAT_W = 64               # 色彩和亮度统计用的缩略宽度
COLOR_STEP = 0.25         # 色彩统计每行盖多少秒。AI 快剪一秒内能换两三个镜头，按秒统计会把它们混在一起
FLICKER = 2.0             # 选段各秒亮度逐帧抖动（0-255）的中位数超过这么多，建议开 deflicker。
                          # 实测一条无闪烁的 AI 快剪：各秒 0.2 到 2.9，中位 0.7，高值在换镜头和强光扫过的秒；
                          # 人为加 ±6 的逐帧亮度抖动：各秒 3.5 到 5.7。还没有真实闪烁素材校准过
EPS = 1e-6

def die(msg):
    sys.exit(f"错误：{msg}")

def run(cmd, binary=False, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=not binary, cwd=cwd)
    if r.returncode:
        err = r.stderr.decode(errors="replace") if binary else r.stderr
        die(f"命令失败：{' '.join(map(str, cmd))}\n{err[-800:]}")
    return r

def jload(path, default=None):
    if not os.path.exists(path):
        if default is not None:
            return default
        die(f"找不到 {path}")
    with open(path) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            die(f"{path} 不是合法 JSON：{e}")

def jdump(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)

def sha1(path):
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()[:10]

def is_num(*xs):
    return all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in xs)

def find_work(arg):
    if arg:
        return os.path.abspath(arg)
    d = os.getcwd()
    while True:
        for cand in (d, os.path.join(d, ".video-edit")):
            if os.path.exists(os.path.join(cand, "project.json")):
                return cand
        if os.path.dirname(d) == d:
            die("找不到工作区，先 edit.py init，或传 --work")
        d = os.path.dirname(d)

class Ws:
    """工作区。所有产物路径都从这里派生。"""
    def __init__(self, arg):
        self.dir = find_work(arg)
        self.project = jload(self.p("project.json"))
        self.ids = list(self.project["clips"])
        self.fps = self.project["target"]["fps"]
    def p(self, *parts):
        return os.path.join(self.dir, *parts)
    def src(self, cid):
        return os.path.join(self.project["src"], self.project["clips"][cid])
    def facts(self):
        return jload(self.p("clips.json"), {"clips": {}})["clips"]
    def log(self):
        return jload(self.p("log.json"), {"clips": {}})["clips"]

def probe(path):
    j = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path]).stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    num, den = map(int, v["r_frame_rate"].split("/"))
    dur = float(j["format"]["duration"])
    return {"width": v["width"], "height": v["height"], "fps": round(num / den, 3),
            "frames": int(v.get("nb_frames") or round(dur * num / den)), "duration": round(dur, 3),
            "audio": {"sample_rate": int(a["sample_rate"]), "channels": a["channels"]} if a else None}

def pieces(sel):
    """素材时间到成片时间的映射，分段：(素材起, 素材止, 起倍速, 止倍速, 成片起)。倍速在段内线性变化。"""
    keys = sorted(sel["ramp"]) if sel.get("ramp") else [[sel["in"], sel.get("speed", 1.0)]]
    def v(s):
        if s <= keys[0][0]:
            return keys[0][1]
        for (a, va), (b, vb) in zip(keys, keys[1:]):
            if s <= b:
                return va + (vb - va) * (s - a) / (b - a) if b > a else vb
        return keys[-1][1]
    pts = [sel["in"]] + [k for k, _ in keys if sel["in"] < k < sel["out"]] + [sel["out"]]
    out, o = [], 0.0
    for a, b in zip(pts, pts[1:]):
        out.append((a, b, v(a), v(b), o))
        o += span_out(a, b, v(a), v(b), b)
    return out

def span_out(a, b, va, vb, s):
    """倍速从 va 线性变到 vb 的素材段 [a, b] 里，从 a 播到 s 用的成片秒数。"""
    if abs(vb - va) < EPS:
        return (s - a) / va
    return (b - a) / (vb - va) * math.log((va + (vb - va) * (s - a) / (b - a)) / va)

def out_at(sel, s):
    """素材时间 s 落在成片里离选段开头多少秒（不含定格）。"""
    for a, b, va, vb, o in pieces(sel):
        if s <= b:
            return o + span_out(a, b, va, vb, max(a, s))
    a, b, va, vb, o = pieces(sel)[-1]
    return o + span_out(a, b, va, vb, b)

def min_speed(sel):
    return min(v for _, v in sel["ramp"]) if sel.get("ramp") else sel.get("speed", 1.0)

def frames_of(sel, fps):
    if "gap" in sel:
        return round(sel["dur"] * fps)
    if "color" in sel:
        return int(sel.get("frames") or 0)
    return round(out_at(sel, sel["out"]) * fps) + round(sel.get("hold", 0.0) * fps)

def grab(path, t, out, width, flip=False):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(t, 0):.3f}", "-i", path, "-frames:v", "1",
         "-vf", ("hflip," if flip else "") + f"scale={width}:-2", out])

def make_sheets(path, prefix, fps, w, h, start=0.0, dur=None, tile_w=None):
    """抽帧拼成网格，返回 (文件列表, 列, 行)。第 n 张第 k 格 = start + ((n-1)·列·行 + k) / fps 秒。"""
    tile_w = tile_w or (320 if w >= h else 200)
    cols, rows = SHEET_BUDGET // tile_w, max(1, SHEET_BUDGET // round(tile_w * h / w))
    os.makedirs(os.path.dirname(prefix), exist_ok=True)
    pattern = glob.escape(prefix) + "_[0-9][0-9].jpg"
    for old in glob.glob(pattern):
        os.remove(old)
    run(["ffmpeg", "-v", "error", "-y"] + (["-ss", f"{start:.3f}"] if start else []) +
        (["-t", f"{dur:.3f}"] if dur else []) +
        ["-i", path, "-vf", f"fps={fps},scale={tile_w}:-2,tile={cols}x{rows}", "-q:v", "3", prefix + "_%02d.jpg"])
    return sorted(glob.glob(pattern)), cols, rows

def grid_note(start, fps, cols, rows):
    return (f"每张 {cols} 列 {rows} 行。第 n 张第 k 格（行优先，从 0 数）"
            f"= {start:g} + ((n-1)×{cols * rows} + k) / {fps:g} 秒")

def integrated_lufs(path):
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-vn", "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    m = re.findall(r"^\s+I:\s+(-?[\d.]+) LUFS", err, re.M)
    return float(m[-1]) if m else None

def resolve(base, path):
    return path if os.path.isabs(path) else os.path.join(base, path)

def latest_cut(ws):
    reps = glob.glob(ws.p("cuts", "v*.json"))
    return max(reps, key=lambda p: int(re.search(r"v(\d+)\.json$", p).group(1))) if reps else None

def decode16k(path):
    raw = run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], binary=True).stdout
    s = array.array("f")
    s.frombytes(raw[: len(raw) // 4 * 4])
    return s

def rms_db(samples, t0, t1, sr=16000):
    w = samples[max(0, int(t0 * sr)):max(0, int(t1 * sr))]
    return round(20 * math.log10(math.sqrt(sum(x * x for x in w) / len(w)) + 1e-9), 1) if len(w) else -90.0
