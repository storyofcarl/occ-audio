"""Unit tests for audio concatenation.
Run: python tests/test_stitch.py"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from occ_audio.stitch import concat_audios, resolve_ffmpeg  # noqa: E402

_passed = 0
_failed = 0


def check(label: str, condition: bool) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}")


def test_single_file_passthrough() -> None:
    print("concat_audios: a single input file is copied through untouched")
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "only.wav"
        src.write_bytes(b"RIFFsome-fake-wav-bytes")
        dst = Path(d) / "out.wav"
        result = concat_audios([str(src)], str(dst))
        check("output path returned", result == str(dst))
        check("output file exists", dst.is_file())
        check("content matches the single input", dst.read_bytes() == src.read_bytes())


def test_empty_list_raises() -> None:
    print("concat_audios: an empty input list raises ValueError")
    try:
        concat_audios([], "out.wav")
        check("raises ValueError on empty input", False)
    except ValueError:
        check("raises ValueError on empty input", True)


def test_multi_file_concat_if_ffmpeg_available() -> None:
    print("concat_audios: multi-file concat (skipped if ffmpeg is unavailable)")
    try:
        resolve_ffmpeg()
    except FileNotFoundError:
        print("  SKIP  ffmpeg not available in this environment")
        return
    with tempfile.TemporaryDirectory() as d:
        # Two tiny valid WAV files (44-byte header + silence) so ffmpeg can
        # actually demux/concat them, not just pass bytes through.
        import struct

        def make_wav(path: Path, n_samples: int = 100) -> None:
            data = b"\x00\x00" * n_samples
            with path.open("wb") as fh:
                fh.write(b"RIFF")
                fh.write(struct.pack("<I", 36 + len(data)))
                fh.write(b"WAVEfmt ")
                fh.write(struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16))
                fh.write(b"data")
                fh.write(struct.pack("<I", len(data)))
                fh.write(data)

        a = Path(d) / "a.wav"
        b = Path(d) / "b.wav"
        make_wav(a)
        make_wav(b)
        out = Path(d) / "joined.wav"
        concat_audios([str(a), str(b)], str(out))
        check("joined file exists", out.is_file())
        check("joined file is larger than either input",
              out.stat().st_size > a.stat().st_size)


if __name__ == "__main__":
    test_single_file_passthrough()
    test_empty_list_raises()
    test_multi_file_concat_if_ffmpeg_available()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
