"""Download public Meta ad assets and extract timestamped local review frames.

The CLI's ``--every-seconds`` option samples a full video at a fixed interval.
Callers in analysis pipelines should pass their actual beat timestamps to
``prepare_local_media`` instead.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

MAX_BYTES = 100 * 1024 * 1024
MAX_DURATION_SECONDS = 600
MAX_FRAMES = 300
MAX_DECODED_FRAMES = 200_000
META_HOSTS = ('fbcdn.net', 'facebook.com', 'fbsbx.com')


def allowed(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        return (parsed.scheme == 'https' and not parsed.username and not parsed.password
                and parsed.port in (None, 443)
                and any(host == domain or host.endswith('.' + domain) for domain in META_HOSTS))
    except ValueError:
        return False


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl):
            raise ValueError('Media redirect outside public Meta CDN')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _snapshot_card_count(ad: dict) -> int:
    extra = ad.get('_graphql_extra') or {}
    cards = extra.get('cards')
    if cards is None:
        cards = (ad.get('snapshot') or {}).get('cards')
    return len(cards) if isinstance(cards, list) else 0


def _asset_url(ad: dict) -> tuple[str | None, str]:
    video_url = ad.get('_video_url') or ad.get('_extracted_video_url')
    if video_url:
        return video_url, 'VIDEO'
    if ad.get('_is_video') or ad.get('media_type') == 'VIDEO':
        return None, 'VIDEO'
    image_url = ad.get('_image_url') or ad.get('_extracted_image_url')
    if image_url:
        return image_url, 'IMAGE'
    # A thumbnail is usable for an image-only ad, never as a substitute for a known video.
    if ad.get('_extracted_thumbnail'):
        return None, 'VIDEO'
    return ad.get('_extracted_thumbnail'), 'IMAGE'


def download_asset(ad: dict, output_directory: Path) -> dict:
    """Download a selected asset from the public Meta CDN with bounded atomic writes."""
    url, media_type = _asset_url(ad)
    if not url or not allowed(url):
        raise ValueError('No downloadable public Meta media; retain an explicit media gap')
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    target = output_directory / ('creative.mp4' if media_type == 'VIDEO' else 'creative.jpg')
    partial = target.with_name(target.name + '.part')
    partial.unlink(missing_ok=True)
    try:
        opener = urllib.request.build_opener(SafeRedirect())
        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        total = 0
        with opener.open(request, timeout=30) as response, partial.open('wb') as output:
            if not allowed(response.geturl()):
                raise ValueError('Media response outside public Meta CDN')
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError('Creative exceeds 100 MB local review limit')
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return {
        'media_file': str(target.resolve()),
        'media_type': media_type,
        'snapshot_card_count': _snapshot_card_count(ad),
    }


def _finite_seconds(value) -> float:
    if isinstance(value, bool):
        raise ValueError('Frame timestamps must be finite numbers')
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise ValueError('Frame timestamps must be finite numbers') from None
    if not math.isfinite(seconds):
        raise ValueError('Frame timestamps must be finite numbers')
    return seconds


def prepare_local_media(
    media_file: Path, *, ad_id: str, media_type: str, output_directory: Path,
    timestamps=None, every_seconds: float | None = None,
) -> dict:
    """Prepare local image/video frames, preserving decoded frame timestamps."""
    media_file = Path(media_file)
    output_directory = Path(output_directory)
    if not media_file.is_file():
        raise ValueError('Media file does not exist')
    if not isinstance(ad_id, str) or not ad_id.strip():
        raise ValueError('Ad ID must be non-empty text')
    normalized_type = str(media_type).upper()
    if normalized_type not in {'VIDEO', 'IMAGE'}:
        raise ValueError('Media type must be VIDEO or IMAGE')
    output_directory.mkdir(parents=True, exist_ok=True)
    frames: list[dict] = []
    if normalized_type == 'IMAGE':
        frames.append({'path': str(media_file.resolve()), 'seconds': 0.0})
        duration = 0.0
    else:
        import av

        # First pass stores only timestamp metadata. Presentation timestamps are
        # the source of truth; nominal FPS is inaccurate for variable-rate media.
        with av.open(str(media_file)) as container:
            streams = [stream for stream in container.streams if stream.type == 'video']
            if not streams:
                raise ValueError('Video cannot be decoded; mark media unavailable')
            stream = streams[0]
            origin = (float(container.start_time) / av.time_base
                      if container.start_time is not None else None)
            points: list[float] = []
            frame_durations: list[float | None] = []
            for frame in container.decode(stream):
                if len(points) >= MAX_DECODED_FRAMES:
                    raise ValueError('Video exceeds the 200,000 decoded frame metadata limit')
                if frame.pts is None:
                    raise ValueError('Video frame is missing a presentation timestamp')
                time_base = frame.time_base or stream.time_base
                if time_base is None:
                    raise ValueError('Video frame has no presentation time base')
                absolute = float(frame.pts * time_base)
                if not math.isfinite(absolute):
                    raise ValueError('Video frame has a non-finite presentation timestamp')
                if origin is None:
                    origin = absolute
                relative = absolute - origin
                if not math.isfinite(relative) or (points and relative < points[-1]):
                    raise ValueError('Video presentation timestamps are out of order')
                points.append(relative)
                duration_value = getattr(frame, 'duration', None)
                frame_durations.append(float(duration_value * time_base) if duration_value else None)
            if not points:
                raise ValueError('Video cannot be decoded; mark media unavailable')
            last_delta = points[-1] - points[-2] if len(points) > 1 else 0.0
            last_duration = frame_durations[-1] or last_delta
            last_end = points[-1] + last_duration
            container_duration = (float(container.duration) / av.time_base
                                  if container.duration is not None else 0.0)
            duration = max(container_duration, last_end)
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError('Video duration cannot be determined from presentation timestamps')
            if duration > MAX_DURATION_SECONDS:
                raise ValueError('Video exceeds the 600 second local review limit')

        if every_seconds is not None:
            interval = _finite_seconds(every_seconds)
            if interval <= 0:
                raise ValueError('every_seconds must be greater than zero')
            requested_times = []
            t = 0.0
            while t < duration:
                requested_times.append(t)
                if len(requested_times) >= MAX_FRAMES:
                    raise ValueError('Requested sampling exceeds 300 frames; choose a larger interval')
                t += interval
            requested_times.append(points[-1])
        elif timestamps is None:
            requested_times = [points[0], points[-1]]
        else:
            requested_times = [_finite_seconds(item) for item in timestamps]
            if len(requested_times) > MAX_FRAMES:
                raise ValueError('Requested timestamps exceed 300 frames')
            if not requested_times:
                raise ValueError('At least one frame timestamp is required')

        indices = sorted({max(0, min(len(points) - 1,
                                     bisect.bisect_right(points, min(max(t, points[0]), points[-1])) - 1))
                          for t in requested_times})
        if len(indices) > MAX_FRAMES:
            raise ValueError('Requested sampling exceeds 300 frames')
        selected = set(indices)
        # Second pass streams and exports only selected frames; no video frames
        # are retained in memory between decode passes.
        with av.open(str(media_file)) as container:
            stream = next((item for item in container.streams if item.type == 'video'), None)
            if stream is None:
                raise ValueError('Video cannot be decoded; mark media unavailable')
            for frame_index, frame in enumerate(container.decode(stream)):
                if frame_index in selected:
                    path = output_directory / f'frame-{len(frames):03d}.jpg'
                    frame.to_image().save(path, format='JPEG')
                    frames.append({'path': str(path.resolve()), 'seconds': points[frame_index]})
                if frame_index >= indices[-1]:
                    break
        if len(frames) != len(indices):
            raise ValueError('Selected video frame could not be decoded on export pass')
    return {
        'media_file': str(media_file.resolve()),
        'media_type': normalized_type,
        'frames': frames,
        'duration_seconds': duration,
        'review_scope': 'one captured asset; supplied beat timestamps' if timestamps is not None else 'one captured asset; sampled frames',
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', required=True)
    parser.add_argument('--ad-id', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--every-seconds', type=float, default=2.0,
                        help='CLI fallback: sample the full video at this interval and include its last frame')
    args = parser.parse_args(argv)
    data = json.loads(Path(args.capture).read_text(encoding='utf-8'))
    ad = next((item for item in data['ads'] if str(item.get('ad_id')) == args.ad_id), None)
    if ad is None:
        raise ValueError('Ad ID is absent from capture')
    downloaded = download_asset(ad, Path(args.out))
    result = prepare_local_media(
        Path(downloaded['media_file']), ad_id=args.ad_id,
        media_type=downloaded['media_type'], output_directory=Path(args.out),
        every_seconds=args.every_seconds if downloaded['media_type'] == 'VIDEO' else None,
    )
    result['snapshot_card_count'] = downloaded['snapshot_card_count']
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit(f'Media preparation failed ({type(exc).__name__}). Check capture freshness or mark this media unavailable.') from None
