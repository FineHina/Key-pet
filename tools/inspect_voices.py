"""Audit unchanged voice files and build guide-backed, local-only scene mappings.

CSV text is authoritative for runtime subtitles. Guide summaries and VO_* aliases
are metadata, never subtitles or physical filenames. No audio is played.
"""
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
VOICE_ROOT = ROOT / "voices"
GUIDE_PATH = VOICE_ROOT / "凯伊桌宠_语音全量分类与功能调用指南.md"
CSV_PATH = VOICE_ROOT / "语音清单.csv"
MANIFEST_PATH = ROOT / "voice_manifest.json"
AUDIO_EXTENSIONS = {".ogg", ".wav", ".mp3", ".m4a", ".flac"}
EXPECTED_GUIDE_GRADES = {"A": 58, "B": 38, "C": 53}
EXTRA_IDS = {f"ch0335_battle_shout_{number}" for number in (1, 2, 3)}

# Human-readable semantic categories; group preserves the exact guide heading,
# including ALT and archive groups, independently of runtime expression cards.
CATEGORY_NAMES = {
    "ANGRY": "生气", "BATTLE_ARCHIVE": "战斗原声收藏", "BLUSH": "害羞",
    "CARE": "感谢与照顾", "CARE_GIVE": "递出奖励", "DARK_ANGRY": "黑脸",
    "DEFAULT": "默认", "DIALOG": "日常对话", "DRAG": "拖拽",
    "EMBARRASSED": "不好意思", "FOCUS": "专注与休息", "GAME": "游戏反馈",
    "GAME_REACT": "游戏反应", "GAME_START": "游戏开始", "GAME_WIN": "游戏胜利",
    "GREETING": "问候", "GROWTH": "成长", "HAPPY": "开心", "IDLE": "待机独白",
    "INTRO": "自我介绍", "PAUSE": "停顿", "RADIO": "无线电原声",
    "SEASON_BIRTHDAY": "生日祝福", "SEASON_BIRTHDAY_PART": "生日对白片段",
    "SEASON_HALLOWEEN": "万圣节", "SEASON_NEWYEAR": "新年",
    "SEASON_SELF_BIRTHDAY": "角色生日", "SEASON_XMAS": "圣诞节",
    "SMILE": "微笑", "STORY": "剧情原声", "TASK_LIST": "每日待办",
    "TITLE": "标题播报", "UNVERIFIED": "待核验", "WELLNESS": "健康与休息提醒",
}

# Explicit original IDs, in guide section 3 priority order. These are not inferred
# by removing underscores. category selects a runtime expression card.
SCENES = {
    "click": ("default", ["ch0335_exskill_2", "ch0335_eventmission_1_1", "ch0335_battle_in_2", "ch0335_exskill_level_1"]),
    "default": ("default", ["ch0335_exskill_2", "ch0335_eventmission_1_1", "ch0335_formation_in_2", "ch0335_battle_in_2"]),
    "smile": ("smile", ["ch0335_exskill_level_1", "ch0335_carrier_monolog_4", "ch0335_exskill_level_2"]),
    "happy": ("happy", ["ch0335_cafe_monolog_5", "ch0335_eventmission_get_1_3", "ch0335_lobby_5_4"]),
    "embarrassed": ("embarrassed", ["ch0335_login_2_2", "ch0335_eventmission_get_1_2"]),
    "blush": ("blush", ["ch0335_eventmission_2_1", "ch0335_eventmission_2_2", "ch0335_lobby_1_1", "ch0335_lobby_1_4"]),
    "angry": ("angry", ["ch0335_lobby_4_1", "ch0335_eventmission_1_2", "ch0335_lobby_5_1", "ch0335_lobby_2_2"]),
    "dark_angry": ("dark_angry", []),
    "cry": ("cry", []),
    "drag": ("embarrassed", ["ch0335_formation_select", "ch0335_eventmission_get_2_2"]),
    "greeting": ("default", ["ch0335_login_1_2", "ch0335_login_2_1", "ch0335_season_birthday_player_1", "ch0335_eventmission_get_2_1"]),
    "focus_start": ("default", ["ch0335_login_2_1", "ch0335_carrier_monolog_1", "ch0335_eventmission_login_1_1"]),
    "focus_pause": ("default", ["ch0335_carrier_monolog_6", "ch0335_lobby_3_4", "ch0335_battle_in_1"]),
    "break_start": ("default", ["ch0335_carrier_monolog_6", "ch0335_lobby_3_4", "ch0335_battle_in_1"]),
    "focus_resume": ("default", ["ch0335_battle_move_2", "ch0335_login_2_1"]),
    "focus_complete": ("smile", ["ch0335_eventmission_get_1_3", "ch0335_carrier_monolog_4", "ch0335_eventmission_day_end", "ch0335_tactic_victory_1"]),
    "break_complete": ("default", ["ch0335_login_2_1"]),
    "reminder_complete": ("smile", ["ch0335_eventmission_get_1_3", "ch0335_carrier_monolog_4", "ch0335_exskill_level_3"]),
    "assistant_open": ("default", ["ch0335_eventmission_1_1", "ch0335_exskill_level_1"]),
    "encouragement": ("smile", ["ch0335_carrier_monolog_1", "ch0335_eventmission_login_1_1", "ch0335_memoriallobby_3_2"]),
    "rest_advice": ("default", ["ch0335_cafe_monolog_3", "ch0335_carrier_monolog_6", "ch0335_battle_in_1"]),
    "idle": ("default", ["ch0335_cafe_monolog_3", "ch0335_carrier_monolog_2"]),
    "coffee": ("default", ["ch0335_cafe_monolog_1", "ch0335_cafe_monolog_2", "ch0335_cafe_monolog_3", "ch0335_cafe_monolog_4", "ch0335_cafe_monolog_5"]),
    "stars": ("default", ["ch0335_exweapon_get"]),
    "tease": ("angry", ["ch0335_lobby_2_4", "ch0335_lobby_5_3", "ch0335_eventshop_buy_3_2"]),
    "intro": ("default", ["ch0335_gachaget", "ch0335_exskill_1"]),
    "support": ("smile", ["ch0335_memoriallobby_1_1", "ch0335_memoriallobby_4_1"]),
    "season_birthday": ("happy", ["ch0335_season_birthday_player_7"]),
    "season_newyear": ("happy", ["ch0335_season_newyear_1"]),
    "season_halloween": ("smile", ["ch0335_season_halloween_1", "ch0335_season_halloween_3"]),
    "season_xmas": ("smile", ["ch0335_season_xmas_1", "ch0335_season_xmas_2"]),
}
MANUAL_SCENES = {"encouragement", "rest_advice", "idle", "coffee", "stars", "tease",
                 "intro", "support", "season_birthday", "season_newyear", "season_halloween", "season_xmas"}


def parse_guide(text=None):
    """Parse only section 2's five-cell tables; return entries and duplicate audit."""
    if text is None:
        text = GUIDE_PATH.read_text(encoding="utf-8-sig")
    entries, id_lines, alias_lines = {}, defaultdict(list), defaultdict(list)
    grade_counts, group_counts, declared_counts = Counter(), Counter(), {}
    in_section, found_section, group = False, False, None
    row_count = 0
    for line_number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if re.match(r"^##\s+2\.", line):
            if found_section:
                raise ValueError("Duplicate guide section 2")
            in_section = found_section = True
            continue
        if in_section and re.match(r"^##\s+", line):
            in_section = False
        if not in_section:
            continue
        heading = re.fullmatch(r"###\s+(VO_[A-Z_]+)\s*[（(](\d+)条[）)]", line)
        if heading:
            group, declared_count = heading.groups()
            if group in declared_counts:
                raise ValueError(f"Duplicate guide category heading: {group}")
            declared_counts[group] = int(declared_count)
            continue
        if line.startswith("### "):
            raise ValueError(f"Malformed category heading at line {line_number}")
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in re.split(r"(?<!\\)\|", line)[1:-1]]
        if len(cells) != 5:
            raise ValueError(f"Expected five guide cells at line {line_number}")
        if cells[0] == "原始编号" or all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        voice_id, alias, site, summary, grade = cells
        if not group or grade not in EXPECTED_GUIDE_GRADES:
            raise ValueError(f"Missing category or invalid grade at line {line_number}")
        if not re.fullmatch(r"(?:ch0335_[a-z0-9_]+|CH0335_Title)", voice_id):
            raise ValueError(f"Invalid original ID at line {line_number}: {voice_id}")
        if not re.fullmatch(re.escape(group) + r"_\d+\.ogg", alias):
            raise ValueError(f"Alias disagrees with category at line {line_number}: {alias}")
        category_key = group.removeprefix("VO_").removesuffix("_ALT")
        if category_key not in CATEGORY_NAMES:
            raise ValueError(f"Unknown semantic category: {group}")
        row_count += 1
        id_lines[voice_id].append(line_number)
        alias_lines[alias].append(line_number)
        grade_counts[grade] += 1
        group_counts[group] += 1
        entries[voice_id] = {
            "grade": grade, "alias": alias, "voice_category": CATEGORY_NAMES[category_key],
            "group": group, "site": site, "summary": summary.replace(r"\|", "|"),
        }
    if not found_section:
        raise ValueError("Guide section 2 not found")
    audit = {
        "guide_row_count": row_count, "guide_unique_id_count": len(id_lines),
        "guide_unique_alias_count": len(alias_lines), "guide_grade_counts": dict(sorted(grade_counts.items())),
        "duplicate_ids": {key: lines for key, lines in id_lines.items() if len(lines) > 1},
        "duplicate_aliases": {key: lines for key, lines in alias_lines.items() if len(lines) > 1},
        "guide_group_counts": dict(sorted(group_counts.items())),
        "group_count_mismatches": {
            key: {"declared": value, "parsed": group_counts[key]}
            for key, value in declared_counts.items() if group_counts[key] != value
        },
    }
    return entries, audit


def validate_guide(entries, audit):
    if audit["duplicate_ids"] or audit["duplicate_aliases"]:
        raise ValueError(f"Duplicate guide IDs/aliases: {audit['duplicate_ids']}, {audit['duplicate_aliases']}")
    if len(entries) != 149 or audit["guide_row_count"] != 149:
        raise ValueError(f"Expected 149 guide rows, got {audit['guide_row_count']}")
    if audit["guide_grade_counts"] != EXPECTED_GUIDE_GRADES or audit["group_count_mismatches"]:
        raise ValueError("Guide grade/category cardinalities disagree with the specification")


def read_subtitles():
    subtitles = {}
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            name = row["文件名"]
            if name in subtitles:
                raise ValueError(f"Duplicate CSV filename: {name}")
            subtitles[name] = row
    return subtitles


def decode_metadata(path):
    """Decode to ffmpeg's null sink, never to an audio device or a new file."""
    import imageio_ffmpeg

    process = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", process.stderr)
    duration = sum(float(n) * factor for n, factor in zip(match.groups(), (3600, 60, 1))) if match else 0
    audio = re.search(r"Audio: ([^\n]+)", process.stderr)
    return {"duration": duration, "decodable": process.returncode == 0 and duration > 0,
            "codec": audio.group(1).strip() if audio else "unknown"}


def cached_decode_matches(previous, path, size, digest):
    return (
        previous.get("id") == path.stem and previous.get("file") == f"voices/{path.name}"
        and previous.get("bytes") == size and previous.get("sha256") == digest
        and isinstance(previous.get("duration"), (int, float)) and previous["duration"] > 0
        and previous.get("decodable") is True and isinstance(previous.get("codec"), str)
        and bool(previous["codec"]) and previous["codec"] != "unknown"
    )


def build_scenes(sources):
    scenes = {}
    for name, (category, ids) in SCENES.items():
        manual = name in MANUAL_SCENES
        enabled = name not in {"dark_angry", "cry"}
        for voice_id in ids:
            source = sources.get(voice_id)
            if source is None or not source["decodable"] or not source["text"]:
                raise ValueError(f"Mapped source is unavailable or has no CSV text: {name}/{voice_id}")
            if source["grade"] not in ({"A", "B"} if manual else {"A"}):
                raise ValueError(f"Disallowed grade in scene: {name}/{voice_id}")
        scenes[name] = {
            "category": category, "sources": list(ids),
            "cooldown": 20 if name == "assistant_open" else 12 if name in {"drag", "embarrassed"} else 3,
            "enabled": enabled, "automatic": enabled and not manual, "manual": manual,
        }
        if manual:
            scenes[name]["user_controlled"] = True
        if name == "idle":
            scenes[name]["default_enabled"] = False
    return scenes


def build_manifest(previous=None):
    guide, audit = parse_guide()
    validate_guide(guide, audit)
    subtitles = read_subtitles()
    previous_sources = (previous or {}).get("sources", {})
    files = sorted((path for path in VOICE_ROOT.iterdir() if path.suffix.lower() in AUDIO_EXTENSIONS),
                   key=lambda path: (path.name.casefold(), path.name))
    sources, reused, decoded, changed = {}, [], [], []
    for path in files:
        if path.stem in sources:
            raise ValueError(f"Duplicate local original ID: {path.stem}")
        size = path.stat().st_size
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        old = previous_sources.get(path.stem, {})
        if cached_decode_matches(old, path, size, digest):
            metadata = {key: old[key] for key in ("duration", "decodable", "codec")}
            reused.append(path.stem)
        else:
            metadata = decode_metadata(path)
            decoded.append(path.stem)
        if old and (old.get("bytes") != size or old.get("sha256") != digest):
            changed.append(path.stem)
        row = subtitles.get(path.name, {})
        classification = guide.get(path.stem, {
            "grade": "C", "alias": path.stem, "voice_category": "未分类",
            "group": "UNCLASSIFIED", "site": row.get("模块", ""),
            "summary": "指南未收录；正文、情绪及用途待核验",
        })
        sources[path.stem] = {
            "id": path.stem, "file": f"voices/{path.name}", **metadata,
            "bytes": size, "sha256": digest,
            "text": row.get("中文翻译", "").strip(), "japanese": row.get("日文原文", "").strip(),
            "subtitle_source": "voices/语音清单.csv（未人工试听核验）",
            **classification, "guide_mapped": path.stem in guide, "verified": False,
        }
    missing_guide = sorted(set(guide) - set(sources))
    extra = sorted(set(sources) - set(guide))
    if missing_guide:
        raise ValueError(f"Missing guide original IDs: {missing_guide}")
    scenes = build_scenes(sources)
    mapped = {voice_id for spec in scenes.values() for voice_id in spec["sources"]}
    automatic = {voice_id for spec in scenes.values() if spec["automatic"] for voice_id in spec["sources"]}
    audit.update({
        "inventory_count": len(sources), "guide_resolved_count": len(set(guide) & set(sources)),
        "missing_guide_ids": missing_guide, "extra_ids": extra,
        "inventory_grade_counts": dict(sorted(Counter(entry["grade"] for entry in sources.values()).items())),
        "mapped_unique_count": len(mapped), "mapped_available_count": sum(sources[key]["decodable"] for key in mapped),
        "automatic_unique_count": len(automatic), "automatic_grade_counts": dict(Counter(sources[key]["grade"] for key in sorted(automatic))),
        "csv_row_count": len(subtitles), "missing_csv_files": sorted(path.name for path in files if path.name not in subtitles),
        "csv_files_without_audio": sorted(set(subtitles) - {path.name for path in files}),
        "empty_chinese_ids": sorted(key for key, entry in sources.items() if not entry["text"]),
        "empty_japanese_ids": sorted(key for key, entry in sources.items() if not entry["japanese"]),
        "decodable_count": sum(entry["decodable"] for entry in sources.values()),
        "undecodable_ids": sorted(key for key, entry in sources.items() if not entry["decodable"]),
        "decode_reused_count": len(reused), "decode_run_count": len(decoded),
        "changed_source_ids": changed, "sfx_candidates": sorted(key for key in sources if key.upper().startswith("SFX_")),
        "listened": False, "aliases_are_logical": True,
    })
    return {
        "version": 1, "sources": sources, "scenes": scenes, "inventory_count": len(sources),
        "missing_mapped_sources": sorted(mapped - set(sources)), "audit": audit,
        "classification_source": f"voices/{GUIDE_PATH.name}#2",
        "note": "Original audio filenames and bytes are unchanged. VO_* aliases are logical metadata only. "
                "CSV Chinese/Japanese text is the runtime subtitle source; guide summaries are not subtitles. "
                "All classifications remain unverified by listening; manual scenes require explicit user action, "
                "idle is opt-in, automatic pools use A only, and C is excluded from every scene.",
    }


def main():
    previous = json.loads(MANIFEST_PATH.read_text(encoding="utf-8")) if MANIFEST_PATH.is_file() else None
    manifest = build_manifest(previous)
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["audit"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
