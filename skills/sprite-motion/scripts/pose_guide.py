#!/usr/bin/env python3
"""Draw an 8-frame side-view run-cycle pose guide (2 rows x 4 columns) as a coloured mannequin.

Pass it to an image-edit call as a second reference next to the character image: the character
image carries identity, the guide carries motion. The planted foot moves back by the same step
every frame, so the drawn cycle has an even stride. Near leg dark blue, far leg pale blue.

  pose_guide.py OUT.png [--leg 0.6]

--leg scales the legs against an adult mannequin (1.0, legs about half the height). Measure the
character first: 0.6 gives legs about a third of the height (chibi). The model copies the guide's
proportions, so a guide with longer legs than the character stretches the character's legs.
Needs Pillow.
"""
import argparse
import math
import sys

try:
    from PIL import Image, ImageDraw
except ImportError:  # keep -h usable
    Image = ImageDraw = None

CW, CH = 384, 512     # cell size; the sheet is 1536x1024, a size image models accept
SCALE = 1.9           # guide units -> pixels
GROUND = 470          # ground line inside a cell, pixels
TORSO, HEAD_R = 44, 46  # torso in guide units, head radius in pixels
NEAR, FAR, BOOT = (40, 70, 160), (90, 110, 170), (120, 70, 40)
# adult mannequin, guide units above the ground; phases: contact, down, passing, up
HIP_H = (112, 104, 112, 118)
STANCE_X = (36, 12, -12, -36)  # planted foot moves back 24 every frame
SWING = ((-52, 52), (-34, 70), (0, 58), (30, 40))  # swing foot (x, height), behind -> forward


def ik(hip, foot, bone):
    """Knee position for a two-bone leg of equal bones, knee bending forward (+x)."""
    dx, dy = foot[0] - hip[0], foot[1] - hip[1]
    d = min(math.hypot(dx, dy), 2 * bone - 0.01)
    a = math.atan2(dy, dx)
    b = math.acos(d / (2 * bone))
    return hip[0] + bone * math.cos(a - b), hip[1] + bone * math.sin(a - b)


def draw_cell(d, i, leg):
    """Frame i: phases 0-3 lead with the near leg, 4-7 repeat them with the far leg. y grows down."""
    phase, mirrored = i % 4, i >= 4
    bone = 60 * leg
    hip = (0, -HIP_H[phase] * leg)
    stance = (STANCE_X[phase] * leg, 0 if phase < 3 else -2)
    swing = (SWING[phase][0] * leg, -SWING[phase][1] * leg)
    near_foot, far_foot = (swing, stance) if mirrored else (stance, swing)
    shoulder = (hip[0] + 4, hip[1] - TORSO)

    def P(p):
        return (CW / 2 + p[0] * SCALE, GROUND + p[1] * SCALE)

    def leg_line(foot, color):
        d.line([P(hip), P(ik(hip, foot, bone)), P(foot)], fill=color, width=16, joint="curve")
        d.line([P(foot), P((foot[0] + 16, foot[1]))], fill=BOOT, width=16)

    def arm(forward, color):  # arms swing opposite to the legs
        elbow = (shoulder[0] + (18 if forward else -14), shoulder[1] + 20)
        hand = (shoulder[0] + (30 if forward else -28), shoulder[1] + (22 if forward else 30))
        d.line([P(shoulder), P(elbow), P(hand)], fill=color, width=12, joint="curve")

    leg_line(far_foot, FAR)
    arm(not mirrored, FAR)
    d.line([P(hip), P(shoulder)], fill=(50, 90, 200), width=56)
    d.line([P((hip[0] - 8, hip[1] - 4)), P((hip[0] + 10, hip[1] - 4))], fill=(110, 70, 40), width=12)
    hx, hy = P((shoulder[0] + 4, shoulder[1] - HEAD_R / SCALE))
    R = HEAD_R
    d.ellipse([hx - R, hy - R, hx + R, hy + R], fill=(250, 210, 170))
    d.ellipse([hx + R * 0.35, hy - 10, hx + R * 0.35 + 10, hy + 8], fill=(30, 30, 30))
    d.polygon([(hx - R * 1.4, hy - R * 0.4), (hx + R * 1.4, hy - R * 0.4), (hx - R * 0.4, hy - R * 2.6)],
              fill=(220, 40, 40))
    leg_line(near_foot, NEAR)
    arm(mirrored, NEAR)
    d.line([(10, GROUND + 9), (CW - 10, GROUND + 9)], fill=(200, 200, 200), width=2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--leg", type=float, default=0.6, help="leg length against an adult mannequin")
    a = ap.parse_args()
    if Image is None:
        sys.exit("needs Pillow: python3 -m venv .venv && .venv/bin/pip install pillow, "
                 "then run this script with .venv/bin/python")
    sheet = Image.new("RGB", (CW * 4, CH * 2), (255, 255, 255))
    for i in range(8):
        cell = Image.new("RGB", (CW, CH), (255, 255, 255))
        draw_cell(ImageDraw.Draw(cell), i, a.leg)
        sheet.paste(cell, ((i % 4) * CW, (i // 4) * CH))
    sheet.save(a.out)
    print(f"{a.out}: planted foot moves {24 * a.leg * SCALE:.0f}px per frame in the guide")


if __name__ == "__main__":
    main()
