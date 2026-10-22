"""Animated GIFs and still images, made from a downloaded part of the video."""

from pathlib import Path

from core.compress import fit_box
from core.ffmpeg import ffmpeg_with_progress, media_duration, run_ffmpeg
from core.jobs import entering_conversion
from core.units import format_bytes


GIF_MAX_SECONDS = 30  # GIFs are heavy: a longer one would be huge and slow to make
GIF_SIDE, GIF_FPS = 480, 15
GIF_QUALITY = "720"  # downloaded quality: enough for a 480 px GIF


def make_gif(src, out, workdir, notify, max_bytes=None, bounce=False):
    """Turn a video into a looping GIF; with `max_bytes`, made smaller and choppier until it fits. `bounce`: played
    forwards then backwards (a boomerang), so that the loop has no jump."""
    entering_conversion()
    duration = media_duration(src)
    side, fps = GIF_SIDE, GIF_FPS
    for _ in range(5):
        # One palette made for this video keeps the colors right (GIFs have 256 at most). The small frames are
        # reversed, not the video's own (they are all kept in memory).
        back = ",split[fw][bw];[bw]reverse[rv];[fw][rv]concat=n=2:v=1:a=0" if bounce else ""
        filters = ("fps=%d,%s:flags=lanczos%s,split[a][b];[a]palettegen=stats_mode=diff[p];"
                   "[b][p]paletteuse=dither=bayer:bayer_scale=4" % (fps, fit_box(side), back))
        ffmpeg_with_progress(["-i", str(src), "-vf", filters, "-loop", "0", str(out)],
                             workdir, notify, "Making the GIF…", duration * 2 if bounce and duration else duration)
        if not max_bytes or out.stat().st_size <= max_bytes:
            return out
        side, fps = int(side * 0.75), max(fps - 3, 8)
    out.unlink(missing_ok=True)
    raise RuntimeError("The GIF could not be made smaller than %s: choose a shorter passage."
                       % format_bytes(max_bytes))


def grab_frame(src, out, workdir):
    """The first picture of a video, at full size, as a JPG or PNG image."""
    entering_conversion()
    quality = ["-q:v", "2"] if Path(out).suffix.lower() == ".jpg" else []
    run_ffmpeg(["-i", str(src), "-frames:v", "1"] + quality + [str(out)], workdir)
    return out
