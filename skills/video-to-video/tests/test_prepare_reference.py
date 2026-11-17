import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from prepare_reference import prepare


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe')
                     and importlib.util.find_spec('av'), 'Local media dependencies required')
class PrepareReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def clip(self, name, audio=True):
        path = self.root / name
        command = ['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i',
                   'testsrc2=size=64x48:rate=10:duration=1']
        if audio:
            command += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=1',
                        '-shortest', '-c:a', 'aac']
        command += ['-c:v', 'mpeg4', '-q:v', '2', str(path)]
        subprocess.run(command, check=True, capture_output=True)
        return path

    def test_audio_and_timestamped_frames_are_prepared_without_changing_source(self):
        source = self.clip("reference's clip.mp4")
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        out = self.root / 'evidence'
        result = prepare(source, out, interval=0.5)
        self.assertEqual(result['status'], 'prepared')
        self.assertEqual(result['inspection_status'], 'prepared_not_reviewed')
        self.assertEqual(result['source_sha256'], before)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
        self.assertEqual(result['audio_stream_count'], 1)
        self.assertGreaterEqual(len(result['frames']), 2)
        self.assertTrue(all(Path(frame['path']).is_file() for frame in result['frames']))
        self.assertEqual(result['frames'][0]['seconds'], 0)
        self.assertGreater(result['frames'][-1]['seconds'], 0.5)
        with wave.open(result['extracted_audio_stream']['path']) as audio:
            self.assertEqual(audio.getframerate(), 16000)
            self.assertGreater(audio.getnframes(), 0)
        self.assertEqual(json.loads((out / 'media.json').read_text()), result)

    def test_silent_source_is_explicit_and_has_no_invented_audio_file(self):
        source = self.clip('silent.mp4', audio=False)
        out = self.root / 'silent-evidence'
        result = prepare(source, out)
        self.assertEqual(result['audio_stream_count'], 0)
        self.assertIsNone(result['extracted_audio_stream'])
        self.assertFalse((out / 'audio.wav').exists())

    def test_existing_evidence_is_preserved(self):
        source = self.clip('source.mp4')
        out = self.root / 'evidence'
        out.mkdir()
        sentinel = out / 'notes.md'
        sentinel.write_text('Keep these review notes')
        with self.assertRaisesRegex(ValueError, 'new evidence output'):
            prepare(source, out)
        self.assertEqual(sentinel.read_text(), 'Keep these review notes')
        self.assertEqual(list(out.iterdir()), [sentinel])


if __name__ == '__main__':
    unittest.main()
