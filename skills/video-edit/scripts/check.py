"""check、inspect、gaps 三个命令：逐层报告，细看边缘，出补拍单。"""
import os, shutil, sys
from common import Ws, grab, grid_note, is_num, jdump, jload, latest_cut, make_sheets, sha1
from rules import check_edl, log_issues

def edl_md(edl, flat, total, fps, path):
    L = ["# EDL", "", f"由 edl.json 生成（edit.py check），只读。预计 {total:.1f} 秒。", ""]
    L += [f"- 未用 {k}：{v}" for k, v in (edl.get("unused") or {}).items()] + [""]
    scenes = {str(sc.get("id", k)): sc for k, sc in enumerate(edl.get("scenes") or [])}
    for sid, sc in scenes.items():
        rows = [it for it in flat if it["sid"] == sid]
        d = sc.get("dissolve")
        L += [f"## {sid}  {sc.get('beat', '')}", ""] + ([f"从上一场叠化 {d}s", ""] if d else []) + \
             ([f"色彩基准 {sc['match']}", ""] if sc.get("match") else [])
        L += ["| # | 成片 | 源 | 入 | 出 | 秒 | 选项 | why |", "|---|---|---|---|---|---|---|---|"]
        for it in rows:
            s = it["sel"] if isinstance(it["sel"], dict) else {}
            opt = " ".join(filter(None, ["flip" if s.get("flip") else "", f"x{s['speed']}" if s.get("speed") else "",
                                         f"声:{s['sound']}" if s.get("sound") else "",
                                         f"J{s['lead']}" if s.get("lead") else "", f"L{s['lag']}" if s.get("lag") else "",
                                         "变速曲线" if s.get("ramp") else "", f"定格{s['hold']}" if s.get("hold") else "",
                                         f"放大{s['zoom']}" if s.get("zoom") else "", "震动" if s.get("shake") else "",
                                         "不匹配" if s.get("match") is False else "",
                                         *[f"waive:{k}" for k in s.get("waive") or {}]]))
            src = f"缺镜头：{s['gap']}" if "gap" in s else f"{s['color']} 色帧" if "color" in s else s.get("clip")
            L.append(f"| {it['j']} | {it['t']:.2f} | {src} | {s.get('in', '')} | {s.get('out', '')} "
                     f"| {it['frames'] / fps:.2f} | {opt} | {s.get('why', '')} |")
        L.append("")
    if edl.get("audio"):
        L += ["## 声音层", "", "| # | 文件 | 从 | 到 | gain | duck | why |", "|---|---|---|---|---|---|---|"]
        L += [f"| {k} | {x.get('file')} | {x.get('from')} | {x.get('to', '片尾')} | {x.get('gain', 0)} | {x.get('duck', '')} "
              f"| {x.get('why', '')} |" for k, x in enumerate(edl["audio"]) if isinstance(x, dict)]
    with open(path, "w") as f:
        f.write("\n".join(L))

def cmd_check(a):
    ws = Ws(a.work)
    facts, log = ws.facts(), ws.log()
    stages = {}
    brief = ws.p("brief.md")
    stages["意图"] = ([] if os.path.exists(brief) and len(open(brief).read().strip()) > 40 else ["brief.md 没写"],
                     f"先问用户，再写 {brief}：意图、剧本、角色、故事")
    stages["事实"] = ([f"{c} 没分析" for c in ws.ids if c not in facts] +
                     [f"{c} 没转写台词" for c in ws.ids if c in facts and facts[c].get("lines") is None],
                     "edit.py analyze")
    issues = [x for c in ws.ids if c in facts for x in log_issues(c, log.get(c), facts[c])]
    stages["日志"] = (issues, "看 1fps 触点图，填 log.json（edit.py schema log）")
    found, notes, total = [], [], 0.0
    if not os.path.exists(ws.p("edl.json")):
        stages["剪辑"] = (["还没有 edl.json"], "写 edl.json（edit.py schema edl）")
    else:
        edl = jload(ws.p("edl.json"))
        found, notes, flat, total = check_edl(edl, facts, log, ws.ids, ws.project["target"], ws.dir)
        if isinstance(edl, dict):
            edl_md(edl, flat, total, ws.fps, ws.p("edl.md"))
        stages["剪辑"] = ([f"[{x['rule']}] {x['at']} {x['msg']}" + (f"  → {x['fix']}" if x.get("fix") else "")
                          for x in found if not x.get("waived")],
                         "edit.py inspect 出 6fps 局部图细看" if any(x.get("look") and not x.get("waived") for x in found)
                         else "改 edl.json，或在选段 waive 里写明原因")
    rep = latest_cut(ws)
    if not rep:
        cut = (["还没渲染"], "edit.py render")
    elif not os.path.exists(ws.p("edl.json")) or jload(rep).get("edl_sha1") != sha1(ws.p("edl.json")):
        cut = ([f"{os.path.basename(rep)} 落后于当前 edl.json"], "edit.py render")
    else:
        cut = ([], f"成片 {jload(rep)['out']}，逐张看它的触点图")
    stages["成片"] = cut
    jdump({"stages": {k: v[0] for k, v in stages.items()}, "findings": found, "notes": notes, "duration": total},
          ws.p("check.json"))

    nxt = None
    for name, (rows, hint) in stages.items():
        waived = sum(1 for x in found if x.get("waived")) if name == "剪辑" else 0
        print(f"{name} " + ("✓" if not rows else f"✗ {len(rows)} 处") + (f"（{waived} 处已豁免）" if waived else ""))
        for r in rows:
            print(f"  - {r}")
        if rows and nxt is None:
            nxt = hint
    for n in notes:
        print(f"  备注 {n}")
    if total:
        print(f"预计时长 {total:.2f}s，EDL 视图 {ws.p('edl.md')}")
    print(f"下一步：{nxt or stages['成片'][1]}")
    sys.exit(1 if any(stages[k][0] for k in ("意图", "事实", "日志", "剪辑")) else 0)

def cmd_inspect(a):
    """给每个只按 1fps 看过、又挨着选段边缘的崩坏，出 6fps 局部图。"""
    ws = Ws(a.work)
    facts, log = ws.facts(), ws.log()
    found, _, _, _ = check_edl(jload(ws.p("edl.json")), facts, log, ws.ids, ws.project["target"], ws.dir)
    todo = {}
    for x in found:
        if x.get("look") and not x.get("waived"):
            todo.setdefault(tuple(x["look"]), []).append(x)
    if not todo:
        print("没有要细看的边缘")
        return
    for (cid, t0, t1), xs in todo.items():
        m = facts[cid]
        files, cols, rows = make_sheets(ws.src(cid), ws.p("sheets", "fine", f"{cid}_{t0:g}+{t1 - t0:g}@6"), 6,
                                        m["width"], m["height"], t0, t1 - t0)
        print("\n".join(f"[{x['at']}] {x['msg']}" for x in xs))
        print("\n".join(f"  {f}" for f in files) + f"\n  {grid_note(t0, 6, cols, rows)}")
    rules = {x["rule"] for xs in todo.values() for x in xs}
    if "coarse" in rules:
        print("看完把 log.json 里这些崩坏的 t0、t1 改准，seen 改成 6，再跑 check")
    if "slow" in rules:
        print("慢放段看完，有崩坏就记进 log.json 再改选段；没有就在选段 waive.slow 写看到了什么")

def shot_at(log, cid, t):
    for s in (log.get(cid) or {}).get("shots") or []:
        if isinstance(s, dict) and is_num(s.get("t0"), s.get("t1")) and s["t0"] <= t < s["t1"] + 0.01:
            return "，".join(filter(None, [s.get("what"), s.get("framing"), s.get("side")]))
    return ""

def cmd_gaps(a):
    ws = Ws(a.work)
    facts, log, T = ws.facts(), ws.log(), ws.project["target"]
    edl = jload(ws.p("edl.json"))
    _, _, flat, _ = check_edl(edl, facts, log, ws.ids, ws.project["target"], ws.dir)
    gaps = [n for n, it in enumerate(flat) if it["ok"] and "gap" in it["sel"]]
    if not gaps:
        print("edl.json 里没有缺镜头")
        return
    out_dir = ws.p("reshoots")
    shutil.rmtree(out_dir, ignore_errors=True)
    beats = {str(sc.get("id", k)): sc.get("beat", "") for k, sc in enumerate(edl.get("scenes") or [])}
    real = lambda i: flat[i]["ok"] and "clip" in flat[i]["sel"]
    L = ["# 补拍单", "", "由 edl.json 的 gap 选段生成（edit.py gaps），只读。每条可写成一次视频生成 prompt，"
         "角色外观见 brief.md。参考帧是成片里前后镜头的衔接帧，可作首尾帧参考。", ""]
    for n in gaps:
        it, s = flat[n], flat[n]["sel"]
        L += [f"## {it['at']}  成片 {it['t']:.2f}s", "", f"- 拍点：{beats.get(it['sid'], '')}", f"- 要拍：{s['gap']}",
              f"- 时长：{s['dur']}s，生成时两头各多留 1 秒余量", f"- 为什么缺：{s.get('why', '')}",
              f"- 画幅：{T['width']}x{T['height']}，{T['fps']}fps"]
        prev = next((flat[i] for i in range(n - 1, -1, -1) if real(i)), None)
        nxt = next((flat[i] for i in range(n + 1, len(flat)) if real(i)), None)
        for label, nb, side in (("前一镜头，接它的尾帧", prev, "prev"), ("后一镜头，接它的首帧", nxt, "next")):
            if not nb:
                continue
            ns = nb["sel"]
            t = ns["out"] - 1 / facts[ns["clip"]]["fps"] if side == "prev" else ns["in"]
            img = os.path.join(out_dir, f"{it['sid']}_{it['j']}_{side}.jpg")
            grab(ws.src(ns["clip"]), t, img, 1280, flip=ns.get("flip"))
            L.append(f"- {label}：{nb['at']}，素材 {ns['clip']} 的 {t:.2f}s" + ("，成片里水平翻转" if ns.get("flip") else "")
                     + f"。{shot_at(log, ns['clip'], t)}。参考帧 {img}")
        L.append("")
    with open(ws.p("reshoots.md"), "w") as f:
        f.write("\n".join(L))
    print(f"{len(gaps)} 处缺镜头 -> {ws.p('reshoots.md')}")
