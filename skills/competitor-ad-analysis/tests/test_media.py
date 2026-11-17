import importlib.util
from fractions import Fraction
from pathlib import Path

import av
import pytest
from PIL import Image


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "media.py"
SPEC = importlib.util.spec_from_file_location("competitor_ad_media", SCRIPT)
media = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(media)


def vfr_video(path):
    """Write frames at presentation times 0, 1, 6, and 8 seconds."""
    with av.open(str(path), mode="w", format="matroska") as container:
        stream = container.add_stream("ffv1", rate=1)
        stream.width = 16
        stream.height = 16
        stream.pix_fmt = "yuv420p"
        stream.time_base = Fraction(1, 1)
        samples = [(0, (255, 0, 0)), (1, (0, 255, 0)),
                   (6, (0, 0, 255)), (8, (255, 255, 0))]
        for pts, color in samples:
            frame = av.VideoFrame.from_image(Image.new("RGB", (16, 16), color))
            frame.pts = pts
            frame.time_base = Fraction(1, 1)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def test_default_sampling_includes_last_decoded_frame(tmp_path):
    source = tmp_path / "creative.mkv"
    vfr_video(source)
    result = media.prepare_local_media(source, ad_id="a/unsafe", media_type="VIDEO", output_directory=tmp_path / "frames")

    assert result["media_type"] == "VIDEO"
    assert result["duration_seconds"] == 9
    assert [frame["seconds"] for frame in result["frames"]] == [0, 8]
    assert all(Path(frame["path"]).is_absolute() for frame in result["frames"])


def test_timestamp_inputs_use_actual_pts_and_clamp_to_endpoints(tmp_path):
    source = tmp_path / "creative.mkv"
    vfr_video(source)
    result = media.prepare_local_media(source, ad_id="1", media_type="VIDEO", output_directory=tmp_path / "frames", timestamps=[-5, 3, 50])
    assert [item["seconds"] for item in result["frames"]] == [0, 1, 8]
    assert Path(result["frames"][1]["path"]).is_file()

    with pytest.raises(ValueError, match="finite"):
        media.prepare_local_media(source, ad_id="1", media_type="VIDEO", output_directory=tmp_path / "bad", timestamps=[float("nan")])


def test_interval_sampling_retains_actual_final_frame(tmp_path):
    source = tmp_path / "creative.mkv"
    vfr_video(source)
    result = media.prepare_local_media(source, ad_id="1", media_type="VIDEO", output_directory=tmp_path / "frames", every_seconds=2)
    assert result["frames"][-1]["seconds"] == 8


def test_download_rejects_non_meta_url_before_network(monkeypatch, tmp_path):
    monkeypatch.setattr(media.urllib.request, "build_opener", lambda *_: pytest.fail("network must not be opened"))
    with pytest.raises(ValueError, match="No downloadable"):
        media.download_asset({"_image_url": "https://evil.example/image.jpg"}, tmp_path)


def test_thumbnail_does_not_substitute_for_known_video(monkeypatch, tmp_path):
    monkeypatch.setattr(media.urllib.request, "build_opener", lambda *_: pytest.fail("network must not be opened"))
    ad = {"_is_video": True, "_extracted_thumbnail": "https://scontent.fbcdn.net/thumb.jpg"}
    with pytest.raises(ValueError, match="No downloadable"):
        media.download_asset(ad, tmp_path)


def test_known_video_without_video_url_rejects_image_url(monkeypatch, tmp_path):
    monkeypatch.setattr(media.urllib.request, "build_opener", lambda *_: pytest.fail("network must not be opened"))
    ad = {"media_type": "VIDEO", "_image_url": "https://scontent.fbcdn.net/thumbnail.jpg"}
    with pytest.raises(ValueError, match="No downloadable"):
        media.download_asset(ad, tmp_path)


def test_download_returns_asset_metadata_and_snapshot_count(monkeypatch, tmp_path):
    class Response:
        def __init__(self):
            self.chunks = [b"image", b""]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def geturl(self):
            return "https://scontent.fbcdn.net/photo.jpg"

        def read(self, _size):
            return self.chunks.pop(0)

    class Opener:
        def open(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(media.urllib.request, "build_opener", lambda *_: Opener())
    result = media.download_asset({"_image_url": "https://scontent.fbcdn.net/photo.jpg", "_graphql_extra": {"cards": [1, 2]}}, tmp_path)
    assert result["media_type"] == "IMAGE"
    assert result["snapshot_card_count"] == 2
    assert Path(result["media_file"]).read_bytes() == b"image"
    assert not (tmp_path / "creative.jpg.part").exists()
