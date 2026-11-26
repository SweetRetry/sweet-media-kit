#!/usr/bin/env python3
"""Validate repository skill structure, metadata, independence, links, and README sync."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"
README_PATH = ROOT / "README.md"
MAX_SKILL_LINES = 500
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def strip_code(text: str) -> str:
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(re.sub(r"`[^`]*`", "", line))
    return "\n".join(lines)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            key, val = line.split(":", 1)
            meta[key.strip()] = val.strip().strip('"').strip("'")
    body = "\n".join(lines[end + 1 :])
    return meta, body


def first_h1(body: str) -> str | None:
    in_fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and line.startswith("# "):
            return line[2:].strip()
    return None


def parse_openai_yaml(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    section = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0 and line.endswith(":"):
            section = line[:-1].strip()
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            full_key = f"{section}.{key.strip()}" if section else key.strip()
            result[full_key] = val.strip().strip('"').strip("'")
    return result


def parse_readme_skills(text: str) -> list[str]:
    in_skills = False
    found: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            in_skills = line.strip() == "## Skills"
            continue
        if in_skills:
            m = re.match(r"^- `([a-z0-9-]+)`", line)
            if m:
                found.append(m.group(1))
    return found


def main() -> int:
    errors: list[str] = []
    skill_dirs = sorted(
        p for p in SKILLS_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")
    )
    skill_names = [p.name for p in skill_dirs]

    for skill_dir in skill_dirs:
        name = skill_dir.name
        skill_md = skill_dir / "SKILL.md"
        openai_yaml = skill_dir / "agents" / "openai.yaml"

        if not KEBAB_RE.match(name):
            errors.append(f"{skill_dir.relative_to(ROOT)}: directory name must be English kebab-case")

        if not skill_md.is_file():
            errors.append(f"{skill_md.relative_to(ROOT)}: missing SKILL.md")
            continue

        skill_text = skill_md.read_text(encoding="utf-8")
        line_count = len(skill_text.splitlines())
        if line_count > MAX_SKILL_LINES:
            errors.append(
                f"{skill_md.relative_to(ROOT)}: {line_count} lines exceeds limit {MAX_SKILL_LINES}"
            )

        parsed = parse_frontmatter(skill_text)
        if parsed is None:
            errors.append(f"{skill_md.relative_to(ROOT)}: missing or malformed YAML frontmatter")
        else:
            meta, body = parsed
            if meta.get("name") != name:
                errors.append(
                    f"{skill_md.relative_to(ROOT)}: frontmatter name {meta.get('name')!r} != {name!r}"
                )
            if not meta.get("description"):
                errors.append(f"{skill_md.relative_to(ROOT)}: empty frontmatter description")
            if meta.get("disable-model-invocation") != "true":
                errors.append(
                    f"{skill_md.relative_to(ROOT)}: must set disable-model-invocation: true"
                )
            h1 = first_h1(body)
            if not h1:
                errors.append(f"{skill_md.relative_to(ROOT)}: missing H1 heading")
            elif CJK_RE.search(h1):
                errors.append(f"{skill_md.relative_to(ROOT)}: H1 {h1!r} must be in English")

        if not openai_yaml.is_file():
            errors.append(f"{openai_yaml.relative_to(ROOT)}: missing agents/openai.yaml")
        else:
            oy = parse_openai_yaml(openai_yaml.read_text(encoding="utf-8"))
            disp = oy.get("interface.display_name", "")
            if not disp:
                errors.append(f"{openai_yaml.relative_to(ROOT)}: missing interface.display_name")
            elif CJK_RE.search(disp):
                errors.append(
                    f"{openai_yaml.relative_to(ROOT)}: display_name {disp!r} must be in English"
                )
            if oy.get("policy.allow_implicit_invocation") != "false":
                errors.append(
                    f"{openai_yaml.relative_to(ROOT)}: must set policy.allow_implicit_invocation: false"
                )

        other_skills = [other for other in skill_names if other != name]
        other_re = (
            re.compile(r"(?<![a-z0-9_-])(" + "|".join(map(re.escape, other_skills)) + r")(?![a-z0-9_-])")
            if other_skills
            else None
        )

        for doc in sorted(skill_dir.rglob("*")):
            if not doc.is_file() or doc.suffix not in {".md", ".yaml"}:
                continue
            text = doc.read_text(encoding="utf-8")
            if other_re:
                for m in other_re.finditer(text):
                    errors.append(
                        f"{doc.relative_to(ROOT)}: references sibling skill {m.group(1)!r}"
                    )
            if doc.suffix == ".md":
                cleaned = strip_code(text)
                for m in LINK_RE.finditer(cleaned):
                    target = m.group(1).strip()
                    if target.startswith(("http://", "https://", "mailto:", "#", "file://")):
                        continue
                    rel_path = target.split("#", 1)[0]
                    if not rel_path:
                        continue
                    resolved = (doc.parent / rel_path).resolve()
                    if not resolved.exists():
                        errors.append(
                            f"{doc.relative_to(ROOT)}: broken relative link {target!r}"
                        )

    if README_PATH.is_file():
        readme_skills = parse_readme_skills(README_PATH.read_text(encoding="utf-8"))
        for missing in sorted(set(skill_names) - set(readme_skills)):
            errors.append(f"README.md: skill {missing!r} missing from ## Skills")
        for extra in sorted(set(readme_skills) - set(skill_names)):
            errors.append(f"README.md: deleted or unknown skill {extra!r} listed in ## Skills")

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print(f"validate_skills: OK ({len(skill_names)} skills checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
