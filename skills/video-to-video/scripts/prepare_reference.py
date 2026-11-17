"""Prepare local reference evidence. Does not analyze, generate or upload video."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

from shared_media import prepare_local_media


def prepare(source: Path, output: Path, interval: float = 1.0, timestamps=None) -> dict:
    source, output = source.resolve(), output.absolute()
    if not source.is_file():
        raise ValueError('Source video does not exist')
    if output.exists() or output.is_symlink():
        raise ValueError('Choose a new evidence output directory')
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError('Sampling interval must be a positive finite number')
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise ValueError('Install FFmpeg and ffprobe on PATH before preparing media')
    probe = subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-show_format',
                            '-of', 'json', str(source)], check=True, capture_output=True,
                           text=True, timeout=60)
    metadata = json.loads(probe.stdout)
    if not any(s.get('codec_type') == 'video' for s in metadata.get('streams', [])):
        raise ValueError('Source has no video stream')
    digest = hashlib.sha256()
    with source.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    output.mkdir(parents=True)
    (output / 'probe.json').write_text(json.dumps(metadata, indent=2) + '\n')
    receipt = {'status': 'incomplete', 'source_sha256': digest.hexdigest(),
               'source_path': str(source), 'inspection_status': 'prepared_not_reviewed'}
    try:
        media = prepare_local_media(source, ad_id='reference', media_type='VIDEO',
                                    output_directory=output / 'frames', timestamps=timestamps,
                                    every_seconds=None if timestamps is not None else interval)
        receipt.update(media)
        audio = [s for s in metadata.get('streams', []) if s.get('codec_type') == 'audio']
        receipt['audio_stream_count'] = len(audio)
        receipt['extracted_audio_stream'] = None
        if audio:
            audio_path = output / 'audio.wav'
            subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-n', '-i', str(source),
                            '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000',
                            str(audio_path)], check=True, capture_output=True, timeout=120)
            receipt['extracted_audio_stream'] = {'stream_index': audio[0]['index'],
                                                'path': str(audio_path.resolve())}
        receipt['status'] = 'prepared'
        return receipt
    finally:
        (output / 'media.json').write_text(json.dumps(receipt, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    sampling = parser.add_mutually_exclusive_group()
    sampling.add_argument('--every-seconds', type=float, default=1.0)
    sampling.add_argument('--at', type=float, nargs='+')
    args = parser.parse_args()
    try:
        result = prepare(args.input, args.out, args.every_seconds, args.at)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Preparation incomplete ({type(error).__name__}); check local inputs and any saved media.json.\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
