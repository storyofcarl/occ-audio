"""Final assembly — concatenate segment audio files with ffmpeg.

Tries a fast stream-copy concat first, falls back to a re-encode when the
segments are not bit-compatible. Audio-only: no frame extraction, no video
mux — this pipeline has neither (METHODOLOGY.md §1: no continuation
mechanism, no video at all).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def resolve_ffmpeg() -> str:
    path = os.getenv("FFMPEG_PATH")
    if path:
        return path
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        raise FileNotFoundError(
            "ffmpeg not found. Install ffmpeg, set FFMPEG_PATH, or "
            "`pip install imageio-ffmpeg`."
        ) from exc


def extract_tail_audio(src: str, seconds: float, dst: str) -> str:
    """Save the trailing ``seconds`` of ``src`` to ``dst`` — the tail clip
    carried forward as the next segment's continuation reference
    (METHODOLOGY.md §2a). Uses ``-sseof`` (seek relative to end of file), the
    audio analogue of occ's fast-path last-frame extraction."""
    ffmpeg = resolve_ffmpeg()
    out = Path(dst)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-sseof", f"-{seconds:.3f}", "-i", str(src), "-y", str(out)]
    result = None
    for attempt in range(5):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and out.is_file():
            return str(out)
        # On Windows, a file another ffmpeg process just finished writing can
        # briefly report a sharing violation even after that process exited.
        if "sharing violation" in (result.stderr or "").lower() or \
           "being used by another process" in (result.stderr or "").lower():
            time.sleep(0.2 * (attempt + 1))
            continue
        break
    raise RuntimeError(f"tail-clip extraction failed: {result.stderr[-400:]}")


def concat_audios(audio_paths: list[str], output_path: str) -> str:
    """Concatenate ``audio_paths`` (in order) into ``output_path``."""
    if not audio_paths:
        raise ValueError("No input audio files to stitch.")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    if len(audio_paths) == 1:
        output.write_bytes(Path(audio_paths[0]).read_bytes())
        return str(output)

    ffmpeg = resolve_ffmpeg()
    handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    try:
        for path in audio_paths:
            handle.write(f"file '{Path(path).resolve().as_posix()}'\n")
        handle.close()

        fast = [ffmpeg, "-f", "concat", "-safe", "0", "-i", handle.name,
                "-c", "copy", "-y", str(output)]
        result = subprocess.run(fast, capture_output=True, text=True)
        if result.returncode != 0:
            reencode = [ffmpeg, "-f", "concat", "-safe", "0", "-i", handle.name,
                        "-y", str(output)]
            fallback = subprocess.run(reencode, capture_output=True, text=True)
            if fallback.returncode != 0:
                raise RuntimeError(f"ffmpeg concat failed: {fallback.stderr[-500:]}")
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass

    return str(output)
