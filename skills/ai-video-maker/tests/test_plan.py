import json, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import plan as P

TABLE = {"as_of": "test", "models": [
    {"id": "cheap", "name": "Cheap", "kind": "video", "provider": "A", "tier": "standard", "price_per_second": {"720p": 0.05},
     "audio_included": True, "native_audio": True, "min_seconds": 4, "max_seconds": 8, "start_image": True, "max_reference_images": 0, "verified": True},
    {"id": "refs", "name": "Refs", "kind": "video", "provider": "B", "tier": "standard", "price_per_second": {"720p": 0.30, "1080p": 0.6},
     "audio_included": True, "native_audio": True, "min_seconds": 4, "max_seconds": 30, "max_reference_images": 30, "photoreal_faces": "accepted", "verified": False},
    {"id": "silent", "name": "Silent", "kind": "video", "provider": "C", "tier": "standard", "price_per_second": {"720p": 0.01},
     "audio_price_per_second": {"720p": 0.02}, "audio_included": False, "native_audio": False, "min_seconds": 5, "max_seconds": 10, "start_image": True, "max_reference_images": 1, "verified": True},
    {"id": "nofaces", "name": "NoFaces", "kind": "video", "provider": "D", "tier": "standard", "price_per_second": {"720p": 0.001},
     "audio_included": True, "native_audio": True, "min_seconds": 1, "max_seconds": 30, "max_reference_images": 30, "photoreal_faces": "refused", "verified": True},
    {"id": "img", "name": "Img", "kind": "image", "provider": "A", "price_per_image": 0.1, "verified": True},
    {"id": "tts", "name": "TTS", "kind": "voice", "provider": "E", "price_per_1k_characters": 0.08, "verified": True},
]}

class PlanTests(unittest.TestCase):
    def one(self, **shot):
        shot.setdefault("seconds", 8)
        return P.plan({"retake_rate": 0, "shots": [shot]}, TABLE)

    def test_dialogue_needs_native_speech(self):
        r = self.one(dialogue=True, photoreal_faces=True)
        self.assertEqual(r["shots"][0]["model"], "cheap")

    def test_silent_shot_takes_cheapest(self):
        r = self.one(photoreal_faces=True)
        self.assertEqual(r["shots"][0]["model"], "silent")

    def test_reference_count_filters(self):
        r = self.one(dialogue=True, reference_images=4, photoreal_faces=True)
        self.assertEqual(r["shots"][0]["model"], "refs")
        self.assertEqual(r["unverified_prices"], ["refs"])

    def test_refused_faces_excluded_only_when_needed(self):
        self.assertEqual(self.one(dialogue=True)["shots"][0]["model"], "nofaces")
        self.assertNotEqual(self.one(dialogue=True, photoreal_faces=True)["shots"][0]["model"], "nofaces")

    def test_long_shot_is_split_and_billed(self):
        r = self.one(dialogue=True, seconds=20, photoreal_faces=True)
        row = r["shots"][0]
        self.assertEqual(row["model"], "cheap")
        self.assertEqual(len(row["clips"]), 3)
        self.assertAlmostEqual(row["first_pass"], 1.0, places=2)

    def test_minimum_clip_length_is_billed(self):
        row = self.one(dialogue=True, seconds=2, photoreal_faces=True)["shots"][0]
        self.assertEqual(row["billed_seconds"], 4)

    def test_retakes_and_extras(self):
        r = P.plan({"retake_rate": 0.5, "subscription_per_month": 49, "stills": {"count": 5, "revisions_each": 1},
                    "voiceover": {"characters": 1000}, "shots": [{"seconds": 8, "dialogue": True, "photoreal_faces": True}]}, TABLE)
        self.assertAlmostEqual(r["video_first_pass"], 0.40)
        self.assertAlmostEqual(r["video_with_retakes"], 0.60)
        self.assertAlmostEqual(r["total"], 0.60 + 1.0 + 0.08)
        self.assertEqual(r["videos_per_month_to_match_subscription"], round(49 / 1.68, 1))

    def test_resolution_without_price_is_rejected(self):
        with self.assertRaises(P.PlanError):
            self.one(dialogue=True, resolution="1080p", reference_images=0, only=["cheap"])

    def test_hero_tier_only_when_asked(self):
        table = {"as_of": "t", "models": [dict(TABLE["models"][0], id="std"), dict(TABLE["models"][0], id="hero", tier="hero", price_per_second={"720p": 0.2})]}
        shot = {"seconds": 8, "dialogue": True}
        self.assertEqual(P.plan({"retake_rate": 0, "shots": [shot]}, table)["shots"][0]["model"], "std")
        self.assertEqual(P.plan({"retake_rate": 0, "shots": [dict(shot, tier="hero")]}, table)["shots"][0]["model"], "hero")
        self.assertEqual(P.plan({"retake_rate": 0, "shots": [shot]}, table, prefer="quality")["shots"][0]["model"], "hero")

    def test_reference_clips_can_have_a_fixed_length(self):
        table = {"as_of": "t", "models": [dict(TABLE["models"][1], id="fixed", min_seconds=4, max_seconds=8, max_reference_images=3, fixed_seconds_with_references=[8])]}
        row = P.plan({"retake_rate": 0, "shots": [{"seconds": 10, "dialogue": True, "reference_images": 3}]}, table)["shots"][0]
        self.assertEqual(row["clips"], [8.0, 8.0])
        row = P.plan({"retake_rate": 0, "shots": [{"seconds": 10, "dialogue": True}]}, table)["shots"][0]
        self.assertEqual(row["clips"], [5.0, 5.0])

    def test_bad_input(self):
        for bad in ({"shots": []}, {"shots": [{"seconds": 0}]}, {"retake_rate": 9, "shots": [{"seconds": 5}]}, {"shots": [{"seconds": 5, "resolution": "4k"}]}):
            with self.assertRaises(P.PlanError):
                P.plan(bad, TABLE)

    def test_bundled_table_and_example(self):
        table = json.loads((ROOT / "data" / "models.json").read_text())
        ids = [m["id"] for m in table["models"]]
        self.assertEqual(len(ids), len(set(ids)))
        r = P.plan(json.loads((ROOT / "examples" / "wrong-door" / "shots.json").read_text()), table)
        self.assertEqual(len(r["shots"]), 3)
        self.assertGreater(r["total"], 0)
        self.assertIn("| Shot |", P.to_markdown(r))

if __name__ == "__main__":
    unittest.main()
