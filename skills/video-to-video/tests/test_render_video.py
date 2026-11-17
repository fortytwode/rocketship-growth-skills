import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "render_video.py"


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools required")
class RenderVideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_clip(self, path, size="32x32", color="red", audio=True):
        command = [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color=c={color}:s={size}:r=5:d=1.0",
        ]
        if audio:
            command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=8000:duration=1.0", "-shortest"]
        command += ["-c:v", "mpeg4", "-q:v", "2"]
        if audio:
            command += ["-c:a", "aac", "-b:a", "32k"]
        subprocess.run(command + [str(path)], check=True)

    def manifest(self, entries, path=None):
        path = path or self.root / "manifest.json"
        data = {"scenes": [{"id": f"s{i}", "line": "Test line", "visual": "Test visual", "provenance": "client-owned", "source": str(p)}
                            for i, p in enumerate(entries, 1)]}
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def run_cli(self, command, manifest, output=None):
        args = [sys.executable, str(SCRIPT), command, "--manifest", str(manifest)]
        if output is not None:
            args += ["--output", str(output)]
        return subprocess.run(args, text=True, capture_output=True)

    def decoded_frames(self, path):
        result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "framemd5", "-"],
                                check=True, capture_output=True, text=True)
        return [line.rsplit(",", 1)[-1].strip() for line in result.stdout.splitlines() if line and not line.startswith("#")]

    def decoded_audio(self, path):
        return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "s16le", "-ac", "1", "-ar", "8000", "-"],
                              check=True, capture_output=True).stdout

    def test_no_change_assembly_preserves_decoded_frames_and_audio(self):
        clip = self.root / "clip.mp4"
        self.make_clip(clip)
        manifest = self.manifest([clip])
        output = self.root / "joined.mp4"
        result = self.run_cli("render", manifest, output)
        self.assertEqual(result.returncode, 0, result.stderr)
        source_frames = self.decoded_frames(clip)
        output_frames = self.decoded_frames(output)
        self.assertEqual(output_frames, source_frames)
        assembled_audio = self.decoded_audio(output)
        source_audio = self.decoded_audio(clip)
        self.assertEqual(assembled_audio, source_audio)

    def test_incompatible_dimensions_rejected_without_output(self):
        a, b = self.root / "a.mp4", self.root / "b.mp4"
        self.make_clip(a, "32x32")
        self.make_clip(b, "48x32", "blue")
        output = self.root / "out.mp4"
        result = self.run_cli("render", self.manifest([a, b]), output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("incompatible clips", result.stderr)
        self.assertFalse(output.exists())

    def test_existing_output_is_preserved(self):
        clip = self.root / "clip.mp4"
        self.make_clip(clip)
        output = self.root / "out.mp4"
        output.write_bytes(b"keep me")
        result = self.run_cli("render", self.manifest([clip]), output)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), b"keep me")

    def test_path_with_apostrophe_renders(self):
        folder = self.root / "it's fine"
        folder.mkdir()
        clip = folder / "source.mp4"
        self.make_clip(clip)
        output = self.root / "out.mp4"
        result = self.run_cli("render", self.manifest([clip]), output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.is_file())

    def test_missing_source_fails(self):
        missing = self.root / "absent.mp4"
        result = self.run_cli("render", self.manifest([missing]), self.root / "out.mp4")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing scene sources", result.stderr)
        self.assertFalse((self.root / "out.mp4").exists())

    def test_two_video_only_scenes_preserve_scene_order(self):
        red, blue = self.root / "red.mp4", self.root / "blue.mp4"
        self.make_clip(red, color="red", audio=False)
        self.make_clip(blue, color="blue", audio=False)
        output = self.root / "ordered.mp4"
        result = self.run_cli("render", self.manifest([red, blue]), output)
        self.assertEqual(result.returncode, 0, result.stderr)
        frames = self.decoded_frames(output)
        self.assertEqual(frames[:5], self.decoded_frames(red))
        self.assertEqual(frames[5:10], self.decoded_frames(blue))

    def test_compressed_audio_multiscene_rejected(self):
        a, b = self.root / "a.mp4", self.root / "b.mp4"
        self.make_clip(a)
        self.make_clip(b, color="blue")
        output = self.root / "out.mp4"
        result = self.run_cli("render", self.manifest([a, b]), output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only uncompressed PCM audio", result.stderr)
        self.assertFalse(output.exists())

    def test_rotation_metadata_is_not_silently_lost_between_scenes(self):
        flat, rotated = self.root / 'flat.mp4', self.root / 'rotated.mp4'
        self.make_clip(flat, size='48x32', audio=False)
        options = subprocess.run(['ffmpeg', '-hide_banner', '-h', 'full'],
                                 check=True, capture_output=True, text=True).stdout
        if '-display_rotation' in options:
            command = ['ffmpeg', '-v', 'error', '-display_rotation:v:0', '90',
                       '-i', str(flat), '-c', 'copy', str(rotated)]
        else:
            command = ['ffmpeg', '-v', 'error', '-i', str(flat), '-c', 'copy',
                       '-metadata:s:v:0', 'rotate=90', str(rotated)]
        subprocess.run(command, check=True, capture_output=True)
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json',
                                str(rotated)], check=True, capture_output=True, text=True)
        metadata = json.loads(probe.stdout)['streams'][0]
        self.assertTrue(any(abs(side.get('rotation', 0)) == 90
                            for side in metadata.get('side_data_list', [])))
        output = self.root / 'out.mp4'
        result = self.run_cli('render', self.manifest([flat, rotated]), output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('display transforms', result.stderr)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
