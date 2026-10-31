"""log.json 和 edl.json 的字段表与说明，check 按它校验，edit.py schema 打印它。"""
from common import MATCH_STRENGTH, SLOW

KINDS = {
    "body":     "形体：脸融化或被拉长、五官粘连，多出或拉长的手指肢体，人、动物、道具粘在一起",
    "motion":   "运动：拖影、重影，脸和头发被糊成一团",
    "text":     "文字：屏幕、纸面、招牌上的字镜像、乱码、逐帧跳变",
    "identity": "漂移：同一镜头里衣服、发型、脸、道具变了",
    "exposure": "曝光：一团白或近全黑，看不清主体",
    "physics":  "物理：悬空、穿模、不合重力",
    "nudity":   "裸露或衣着不雅。不能豁免",
}
SEVERITY = ("major", "minor")
WAIVABLE = ("defect", "black", "line", "sliver", "coarse", "slow")
EDL_KEYS = {"scenes", "unused", "sound", "audio", "fade_in", "fade_out", "lufs", "look"}
SCENE_KEYS = {"id", "beat", "dissolve", "selects", "match"}
SELECT_KEYS = {"clip", "in", "out", "why", "flip", "speed", "waive", "color", "frames", "gap", "dur",
               "sound", "lead", "lag", "gain", "ramp", "hold", "zoom", "center", "shake", "match"}
LOOK_KEYS = {"lut", "contrast", "saturation", "gamma", "temperature", "shadows", "highlights",
             "vignette", "grain", "deflicker"}
LAYER_KEYS = {"file", "from", "to", "offset", "in", "gain", "fade", "duck", "why"}
SHOT_KEYS = {"t0", "t1", "what", "framing", "text", "side"}
DEFECT_KEYS = {"t0", "t1", "kind", "severity", "seen", "note"}
SCHEMA = {"log": """log.json：看片日志。analyze 按切点候选生成骨架，看图之后由你填写和修正。

{"clips": {"<clip>": {
  "summary": "这条素材讲了什么，一句话",
  "shots": [{"t0": 0.0, "t1": 2.75, "what": "床头闹钟特写，红色数字",
             "framing": "特写", "text": "08:47", "side": "A 左 B 右"}],
  "defects": [{"t0": 16.0, "t1": 18.0, "kind": "motion", "severity": "major",
               "seen": 4, "note": "起身时睡衣拖影，带出多余肢体"}]}}}

shots    首尾相接，盖满 0 到素材时长。切点候选漏了就拆开，误检就合并。
         what 必填。framing 景别机位，text 画面上的字，side 主体在画面的左右，选填。
         有两个以上主体或有方向运动的镜头填 side，查越轴靠它。
defects  t0 写到看得见问题的最早一帧。seen 是看这段用的抽帧密度（fps），缺省 1。
         只在 1fps 图上看到的，区间前后各放 0.5 秒。
         severity：major 选段必须避开；minor 可以用，check 只记一笔。
         kind 取值：
""" + "\n".join(f"           {k:<9}{v}" for k, v in KINDS.items()),
"edl": """edl.json：剪辑决定，唯一的计划来源。

{"scenes": [
  {"id": "A", "beat": "她又睡过头了", "selects": [
    {"clip": "0", "in": 0.0, "out": 2.0, "why": "开场多停一拍"},
    {"clip": "5", "in": 10.4, "out": 17.6, "flip": true, "why": "水平翻转，和上一镜头同侧"},
    {"clip": "2", "in": 4.0, "out": 10.5, "speed": 1.25, "why": "压缩入画过程"},
    {"clip": "5", "in": 12.0, "out": 14.0, "lag": 0.6, "sound": "voice", "why": "画面先切到听者，话说完"},
    {"color": "white", "frames": 3, "why": "闪白，光爆接下一场"},
    {"gap": "近景，她把胶囊丢进嘴里，原地消失只剩睡衣落下", "dur": 1.5, "why": "素材里没有讲清胶囊作用的镜头"},
    {"clip": "3", "in": 17.0, "out": 17.5, "why": "拿起手机一瞬",
     "waive": {"coarse": "6fps 看过，17.5 前屏幕没亮"}}]},
  {"id": "B", "dissolve": 0.4, "match": "4", "beat": "…", "selects": [
    {"clip": "4", "in": 0.0, "out": 3.0, "zoom": [1.0, 1.08], "center": [0.5, 0.4], "why": "静止机位缓推，压向她的脸"},
    {"clip": "7", "in": 2.0, "out": 5.0, "ramp": [[3.2, 1.0], [3.35, 0.3], [3.9, 0.3], [4.0, 1.2]],
     "shake": [{"at": 3.3, "dur": 0.4, "amp": 0.015}], "hold": 0.5, "why": "拳头落下时骤慢，震一下，收尾定格",
     "waive": {"slow": "6fps 看过 3.3-3.9，手指完整"}}]}],
 "unused": {"6": "3 的备选，鱼眼段脸部融化"},
 "sound": "voice",
 "audio": [{"file": "music/bgm.mp3", "from": "A", "to": "F", "gain": -14, "fade": 1.0, "duck": 8,
            "why": "全片一条 BGM 盖住各条素材自带的音乐"}],
 "look": {"lut": "looks/film.cube", "contrast": 1.06, "saturation": 0.92, "temperature": 6200,
          "shadows": [-0.04, 0, 0.05], "highlights": [0.04, 0.01, -0.03], "vignette": 0.3, "grain": 6,
          "deflicker": true},
 "fade_in": 0.3, "fade_out": 0.8, "lufs": -16}

scenes   一场一个拍点，按叙事顺序。beat 必填，观众在这一场必须得到的一件事，一句话。
         场内选段之间硬切。dissolve 是从上一场叠化进本场的秒数，缺省硬切。
selects  素材选段：clip、in、out（素材内的秒）、why 必填。flip 水平翻转，speed 倍速。
         色帧：color（white 或 black）加 frames，作闪白或黑场停顿。
         缺镜头：gap 写清要拍什么（景别、主体、动作），dur 是秒数。渲染成灰色占位，edit.py gaps 出补拍单。
         同一素材、同样 flip 和 speed，上一段 out 等于下一段 in，视为一个连续镜头。
         时长按整数帧：round((out - in) / speed × fps)。
变速     ramp 变速曲线，[[素材秒, 倍速], …]，点之间倍速线性过渡，第一个点之前和最后一个点之后保持。
         不和 speed 同用。ramp 段不带同期声，冲击声用音效层补。
         倍速低于 """ + f"{SLOW:g}" + """ 的慢放会放大崩坏：check 报 slow，edit.py inspect 出 6fps 图，看完在 waive.slow 写结论。
         hold 在出点定格的秒数。
运镜     zoom 放大倍数，数字是固定放大（数码推近），[起, 止] 是整段从起到止匀速推拉。不小于 1。
         center 放大的中心，[x, y] 取 0 到 1，缺省 [0.5, 0.5]，越界会被推回画内。
         shake 冲击震动，[{"at": 素材秒, "dur": 秒, "amp": 振幅占画宽比例}]，振幅随 dur 衰减到 0。
         震动要余量：有 shake 的段至少放大 1 + 2 × 最大 amp。放大后有效分辨率低于目标 80% 时 check 备注。
调色     场的 match 是基准素材 id：场内别的素材按基准的色彩统计（每通道均值和对比度）校正，靠拢 """ + \
f"{MATCH_STRENGTH:g}" + """。
         基准取它在本场的选段，不在本场就取整条。选段 "match": false 不参与。不同地点、时间的场各选各的基准。
声音     画面是主轨，每个选段自带同期声，其他声音是锚在画面上的声音层。
         sound 取哪路同期声：mix 原声（缺省）、voice 人声、bed 伴奏、none 静音。
         voice 和 bed 要先 edit.py analyze --stems。顶层 sound 是全片缺省，选段可以覆盖。
         lead 声音比画面早进的秒数（J-cut），lag 声音比画面晚出的秒数（L-cut）。gain 额外增益 dB。
         台词规则看声音的入出点（in - lead、out + lag），画面规则看画面的入出点。
audio    声音层，音乐和音效。file 相对工作区或绝对路径。from、to 是锚点：场 id，或选段如 "C#6"。
         从 from 的开头加 offset 秒起，到 to 的结尾止；没有 to 就到片尾或文件结束。
         in 是从文件第几秒开始取。gain dB，fade 两头淡入淡出秒数（缺省 0.5），duck 有台词时压低的 dB。
         所有声音先归到同一响度再叠加，gain 是相对这个基准的增减。音乐垫底常用 -12 到 -18。
waive    {规则: 原因}，明知违反还要用。可豁免：""" + " ".join(WAIVABLE) + """。
unused   没用上的素材和原因。每条素材要么在选段里，要么在这里。
fade_in fade_out lufs  片头片尾淡入淡出秒数、目标响度，缺省 0.3、0.8、-16。
look     全片统一的调色和质感，在剪完、叠化之后、淡入淡出之前一次作用于整片。各项都可省。
         lut .cube 文件，相对工作区或绝对路径。contrast saturation gamma 缺省 1。
         temperature 色温 K，缺省 6500，低于它偏暖，高于它偏冷。
         shadows highlights 暗部、亮部的 [r, g, b] 偏移，-1 到 1，分离色调常用 0.03 到 0.08。
         vignette 暗角 0 到 1，0.2 到 0.4 不易察觉。grain 胶片颗粒 0 到 30，4 到 8 是细颗粒。
         deflicker 逐段压帧间亮度抖动，不跨剪点。
"""}
