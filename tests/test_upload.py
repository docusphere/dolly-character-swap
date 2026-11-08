import av
import numpy as np
import pytest

from character_swap.media.audio import embed_samples
from character_swap.media.upload import UploadTooLarge, fit_for_upload
from character_swap.media.video import VideoWriter, count_frames
from helpers import tone


def noisy_clip(tmp_path):
    """Random pixels compress badly, so the CRF 18 original is big for its size."""
    rng = np.random.default_rng(1)
    silent = tmp_path / "silent.mp4"
    with VideoWriter(silent, 128, 128) as writer:
        for _ in range(48):
            writer.write(rng.integers(0, 255, (128, 128, 3), dtype=np.uint8))
    return embed_samples(silent, tone(2.0), tmp_path / "prepared.mp4")


def audio_packets(path):
    with av.open(str(path)) as container:
        return [bytes(p) for p in container.demux(audio=0) if p.dts is not None]


def test_small_file_is_used_as_it_is(tmp_path):
    clip = noisy_clip(tmp_path)
    assert fit_for_upload(clip, tmp_path / "copy.mp4", clip.stat().st_size) == clip
    assert not (tmp_path / "copy.mp4").exists()


def test_big_file_gets_a_smaller_copy_with_the_same_frames_and_audio(tmp_path):
    clip = noisy_clip(tmp_path)
    limit = int(clip.stat().st_size * 0.8)
    copy = fit_for_upload(clip, tmp_path / "copy.mp4", limit)
    assert copy == tmp_path / "copy.mp4" and copy.stat().st_size <= limit
    assert count_frames(copy) == 48
    assert audio_packets(copy) == audio_packets(clip)  # lip-sync audio untouched


def test_too_big_even_compressed_is_refused_and_leaves_nothing(tmp_path):
    clip = noisy_clip(tmp_path)
    with pytest.raises(UploadTooLarge, match="shorter clip"):
        fit_for_upload(clip, tmp_path / "copy.mp4", 100)
    assert not (tmp_path / "copy.mp4").exists()
