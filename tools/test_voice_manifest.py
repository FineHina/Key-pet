"""Guide/manifest regression tests using only stdlib, no GUI or audio playback.

Run: python -B tools/test_voice_manifest.py
"""
import ast
from collections import Counter, deque
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("inspect_voices", ROOT / "tools" / "inspect_voices.py")
inspector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspector)

# Load only the real, pure-Python VoiceLibrary class: importing pet.audio would
# also import Qt multimedia. This exercises its existing routing without a GUI.
audio_tree = ast.parse((ROOT / "pet" / "audio.py").read_text(encoding="utf-8"))
library_node = next(node for node in audio_tree.body if isinstance(node, ast.ClassDef) and node.name == "VoiceLibrary")
library_globals = {"ROOT": ROOT, "Path": Path, "json": json, "random": random, "deque": deque}
exec(compile(ast.Module(body=[library_node], type_ignores=[]), str(ROOT / "pet" / "audio.py"), "exec"), library_globals)
VoiceLibrary = library_globals["VoiceLibrary"]

EXPECTED_SCENES = {
    "click": ("default", "exskill_2 eventmission_1_1 battle_in_2 exskill_level_1"),
    "default": ("default", "exskill_2 eventmission_1_1 formation_in_2 battle_in_2"),
    "smile": ("smile", "exskill_level_1 carrier_monolog_4 exskill_level_2"),
    "happy": ("happy", "cafe_monolog_5 eventmission_get_1_3 lobby_5_4"),
    "embarrassed": ("embarrassed", "login_2_2 eventmission_get_1_2"),
    "blush": ("blush", "eventmission_2_1 eventmission_2_2 lobby_1_1 lobby_1_4"),
    "angry": ("angry", "lobby_4_1 eventmission_1_2 lobby_5_1 lobby_2_2"),
    "dark_angry": ("dark_angry", ""), "cry": ("cry", ""),
    "drag": ("embarrassed", "formation_select eventmission_get_2_2"),
    "greeting": ("default", "login_1_2 login_2_1 season_birthday_player_1 eventmission_get_2_1"),
    "focus_start": ("default", "login_2_1 carrier_monolog_1 eventmission_login_1_1"),
    "focus_pause": ("default", "carrier_monolog_6 lobby_3_4 battle_in_1"),
    "break_start": ("default", "carrier_monolog_6 lobby_3_4 battle_in_1"),
    "focus_resume": ("default", "battle_move_2 login_2_1"),
    "focus_complete": ("smile", "eventmission_get_1_3 carrier_monolog_4 eventmission_day_end tactic_victory_1"),
    "break_complete": ("default", "login_2_1"),
    "reminder_complete": ("smile", "eventmission_get_1_3 carrier_monolog_4 exskill_level_3"),
    "assistant_open": ("default", "eventmission_1_1 exskill_level_1"),
    "encouragement": ("smile", "carrier_monolog_1 eventmission_login_1_1 memoriallobby_3_2"),
    "rest_advice": ("default", "cafe_monolog_3 carrier_monolog_6 battle_in_1"),
    "idle": ("default", "cafe_monolog_3 carrier_monolog_2"),
    "coffee": ("default", "cafe_monolog_1 cafe_monolog_2 cafe_monolog_3 cafe_monolog_4 cafe_monolog_5"),
    "stars": ("default", "exweapon_get"),
    "tease": ("angry", "lobby_2_4 lobby_5_3 eventshop_buy_3_2"),
    "intro": ("default", "gachaget exskill_1"),
    "support": ("smile", "memoriallobby_1_1 memoriallobby_4_1"),
    "season_birthday": ("happy", "season_birthday_player_7"),
    "season_newyear": ("happy", "season_newyear_1"),
    "season_halloween": ("smile", "season_halloween_1 season_halloween_3"),
    "season_xmas": ("smile", "season_xmas_1 season_xmas_2"),
}
EXTRAS = {"ch0335_battle_shout_1", "ch0335_battle_shout_2", "ch0335_battle_shout_3"}


class VoiceManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(inspector.MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.sources = cls.manifest["sources"]
        cls.guide_text = inspector.GUIDE_PATH.read_text(encoding="utf-8-sig")
        cls.guide, cls.guide_audit = inspector.parse_guide(cls.guide_text)
        cls.subtitles = inspector.read_subtitles()

    def test_section_two_cardinality_and_duplicates(self):
        inspector.validate_guide(self.guide, self.guide_audit)
        self.assertEqual(len(self.guide), 149)
        self.assertEqual(self.guide_audit["guide_row_count"], 149)
        self.assertEqual(self.guide_audit["guide_unique_alias_count"], 149)
        self.assertEqual(self.guide_audit["guide_grade_counts"], {"A": 58, "B": 38, "C": 53})
        for key in ("duplicate_ids", "duplicate_aliases", "group_count_mismatches"):
            self.assertEqual(self.guide_audit[key], {})

    def test_parser_ignores_other_sections(self):
        text = self.guide_text + "\n|`not_an_id`|`VO_NOT_REAL_01.ogg`|其他|not a subtitle|Z|\n"
        self.assertEqual(inspector.parse_guide(text), (self.guide, self.guide_audit))

    def test_parser_detects_duplicate_ids_and_aliases(self):
        row = "|`ch0335_lobby_4_1`|`VO_ANGRY_01.ogg`|大厅|拒绝被当作小孩|A|"
        for duplicated in (row, row.replace("ch0335_lobby_4_1", "ch0335_lobby_9_9"),
                           row.replace("VO_ANGRY_01.ogg", "VO_ANGRY_99.ogg")):
            with self.subTest(row=duplicated):
                entries, audit = inspector.parse_guide(self.guide_text.replace(row, row + "\n" + duplicated, 1))
                self.assertTrue(audit["duplicate_ids"] or audit["duplicate_aliases"])
                with self.assertRaisesRegex(ValueError, "Duplicate guide"):
                    inspector.validate_guide(entries, audit)

    def test_parser_rejects_bad_cells_and_preserves_alt_heading(self):
        row = "|`ch0335_lobby_4_1`|`VO_ANGRY_01.ogg`|大厅|拒绝被当作小孩|A|"
        with self.assertRaisesRegex(ValueError, "five guide cells"):
            inspector.parse_guide(self.guide_text.replace(row, row[:-1] + "|extra|", 1))
        self.assertEqual(self.guide["ch0335_lobby_2_2"]["group"], "VO_ANGRY_ALT")
        self.assertEqual(self.guide["ch0335_lobby_2_2"]["voice_category"], "生气")
        changed = self.guide_text.replace("### VO_ANGRY（5条）", "### VO_ANGRY（6条）", 1)
        entries, audit = inspector.parse_guide(changed)
        self.assertTrue(audit["group_count_mismatches"])
        with self.assertRaises(ValueError):
            inspector.validate_guide(entries, audit)

    def test_inventory_grades_and_exact_extras(self):
        self.assertEqual(self.manifest["version"], 1)
        self.assertEqual(len(self.sources), 152)
        self.assertEqual(self.manifest["inventory_count"], 152)
        self.assertEqual(set(self.sources) - set(self.guide), EXTRAS)
        self.assertEqual(set(self.guide) - set(self.sources), set())
        self.assertEqual(Counter(entry["grade"] for entry in self.sources.values()), {"A": 58, "B": 38, "C": 56})
        self.assertEqual(set(self.manifest["audit"]["extra_ids"]), EXTRAS)
        for voice_id in EXTRAS:
            entry = self.sources[voice_id]
            self.assertEqual(entry["grade"], "C")
            self.assertEqual(entry["voice_category"], "未分类")
            self.assertEqual(entry["alias"], voice_id)
            self.assertFalse(entry["guide_mapped"])

    def test_all_guide_metadata_and_unique_logical_aliases(self):
        aliases = [entry["alias"] for entry in self.sources.values()]
        self.assertEqual(len(aliases), len(set(aliases)))
        for voice_id, expected in self.guide.items():
            with self.subTest(voice_id=voice_id):
                for key, value in expected.items():
                    self.assertEqual(self.sources[voice_id][key], value)
                self.assertTrue(self.sources[voice_id]["guide_mapped"])

    def test_original_filenames_bytes_hashes_and_decode(self):
        actual = {path.stem: path for path in inspector.VOICE_ROOT.iterdir() if path.suffix.lower() in inspector.AUDIO_EXTENSIONS}
        self.assertEqual(set(actual), set(self.sources))
        for voice_id, entry in self.sources.items():
            with self.subTest(voice_id=voice_id):
                path = actual[voice_id]
                self.assertEqual(entry["id"], path.stem)
                self.assertEqual(entry["file"], "voices/" + path.name)
                self.assertEqual(entry["bytes"], path.stat().st_size)
                self.assertEqual(entry["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
                self.assertTrue(entry["decodable"])
                self.assertGreater(entry["duration"], 0)
                self.assertNotEqual(entry["codec"], "unknown")
        self.assertEqual(self.manifest["audit"]["changed_source_ids"], [])

    def test_csv_is_only_runtime_text_source(self):
        self.assertEqual(len(self.subtitles), 152)
        for entry in self.sources.values():
            with self.subTest(voice_id=entry["id"]):
                row = self.subtitles[Path(entry["file"]).name]
                self.assertEqual(entry["text"], row["中文翻译"].strip())
                self.assertEqual(entry["japanese"], row["日文原文"].strip())
                self.assertIn("语音清单.csv", entry["subtitle_source"])
        for voice_id in EXTRAS | {"ch0335_battle_damage_1"}:
            self.assertEqual(self.sources[voice_id]["text"], "")
            self.assertEqual(self.sources[voice_id]["japanese"], "")
            self.assertTrue(self.sources[voice_id]["summary"])

    def test_exact_curated_scenes_cards_and_cooldowns(self):
        self.assertEqual(set(self.manifest["scenes"]), set(EXPECTED_SCENES))
        for name, (category, suffixes) in EXPECTED_SCENES.items():
            with self.subTest(scene=name):
                spec = self.manifest["scenes"][name]
                self.assertEqual(spec["sources"], ["ch0335_" + suffix for suffix in suffixes.split()])
                self.assertEqual(spec["category"], category)
                self.assertEqual(spec["cooldown"], 20 if name == "assistant_open" else 12 if name in {"drag", "embarrassed"} else 3)
        self.assertEqual(self.manifest["missing_mapped_sources"], [])

    def test_grade_c_excluded_and_automatic_pools_a_only(self):
        expected_c = {voice_id for voice_id, entry in self.guide.items() if entry["grade"] == "C"} | EXTRAS
        actual_c = {voice_id for voice_id, entry in self.sources.items() if entry["grade"] == "C"}
        self.assertEqual(actual_c, expected_c)
        self.assertEqual(len(actual_c), 56)
        mapped, automatic = set(), set()
        for name, spec in self.manifest["scenes"].items():
            mapped.update(spec["sources"])
            self.assertEqual(len(spec["sources"]), len(set(spec["sources"])))
            if spec["automatic"]:
                automatic.update(spec["sources"])
                self.assertFalse(spec["manual"])
        self.assertFalse(mapped & expected_c)
        self.assertEqual({self.sources[key]["grade"] for key in automatic}, {"A"})
        self.assertEqual(self.manifest["audit"]["mapped_unique_count"], len(mapped))
        self.assertEqual(self.manifest["audit"]["mapped_available_count"], len(mapped))
        self.assertEqual(self.manifest["audit"]["automatic_unique_count"], len(automatic))
        library = VoiceLibrary()
        self.assertIsNone(library.error)
        for voice_id in mapped:
            self.assertIsNotNone(library.source(voice_id))
            self.assertIsNotNone(library.source(self.sources[voice_id]["alias"]))
            self.assertTrue(self.sources[voice_id]["text"])

    def test_manual_topics_idle_opt_in_and_disabled_emotions(self):
        for name in ("encouragement", "rest_advice", "idle"):
            spec = self.manifest["scenes"][name]
            self.assertTrue(spec["manual"])
            self.assertTrue(spec["user_controlled"])
            self.assertFalse(spec["automatic"])
        self.assertFalse(self.manifest["scenes"]["idle"]["default_enabled"])
        self.assertEqual({self.sources[key]["grade"] for key in self.manifest["scenes"]["idle"]["sources"]}, {"A"})
        library = VoiceLibrary()
        for name in ("dark_angry", "cry"):
            spec = self.manifest["scenes"][name]
            self.assertEqual(spec["sources"], [])
            self.assertFalse(spec["enabled"])
            self.assertFalse(spec["automatic"])
            self.assertIsNone(library.choose(name, {}, manual=False))
            self.assertIsNone(library.choose(name, {}, manual=True))

    def test_existing_library_b_grade_requires_explicit_manual(self):
        library = VoiceLibrary()
        self.assertEqual(library.sources["ch0335_memoriallobby_3_2"]["grade"], "B")
        # Selecting the last candidate deterministically exercises the B entry.
        library.rng = type("LastCandidate", (), {"choice": staticmethod(lambda candidates: candidates[-1])})()
        self.assertEqual(library.choose("encouragement", {}, manual=True)["id"], "ch0335_memoriallobby_3_2")
        self.assertIsNone(library.choose("encouragement", {}, manual=False))

    def test_all_sources_unverified_no_listening_no_sfx(self):
        self.assertTrue(all(entry["verified"] is False for entry in self.sources.values()))
        self.assertFalse(self.manifest["audit"]["listened"])
        self.assertTrue(self.manifest["audit"]["aliases_are_logical"])
        self.assertEqual(self.manifest["audit"]["sfx_candidates"], [])
        self.assertEqual(self.manifest["audit"]["undecodable_ids"], [])
        self.assertFalse(any(key.startswith(("care", "game")) for key in self.manifest["scenes"]))

    def test_unchanged_metadata_reused_without_decoding(self):
        with patch.object(inspector, "decode_metadata", side_effect=AssertionError("No decode for unchanged bytes")):
            regenerated = inspector.build_manifest(self.manifest)
        self.assertEqual(regenerated, self.manifest)
        self.assertEqual(regenerated["audit"]["decode_reused_count"], 152)
        self.assertEqual(regenerated["audit"]["decode_run_count"], 0)

    def test_hash_or_size_change_invalidates_cached_decode(self):
        voice_id = "ch0335_exskill_2"
        for field, value in (("sha256", "0" * 64), ("bytes", -1)):
            with self.subTest(field=field):
                previous = copy.deepcopy(self.manifest)
                previous["sources"][voice_id][field] = value
                original = self.sources[voice_id]
                metadata = {key: original[key] for key in ("duration", "codec", "decodable")}
                with patch.object(inspector, "decode_metadata", return_value=metadata) as decode:
                    regenerated = inspector.build_manifest(previous)
                decode.assert_called_once_with(inspector.VOICE_ROOT / "ch0335_exskill_2.ogg")
                self.assertEqual(regenerated["audit"]["decode_reused_count"], 151)
                self.assertEqual(regenerated["audit"]["decode_run_count"], 1)
                self.assertEqual(regenerated["audit"]["changed_source_ids"], [voice_id])
                self.assertEqual(regenerated["sources"][voice_id], original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
