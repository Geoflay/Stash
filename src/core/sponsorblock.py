"""SponsorBlock: the parts of a YouTube video its users marked (sponsors, intros…), shown before downloading.

Cutting them out of the file is done by yt-dlp (see download_media)."""

import json
import urllib.error
import urllib.parse

from core.web import http_get


API = "https://sponsor.ajay.app/api/skipSegments"


def fetch_segments(video_id, categories):
    """The parts to skip of this video: [{"start", "end", "category"}] in seconds, by start ([] if none)."""
    query = urllib.parse.urlencode({"videoID": video_id, "categories": json.dumps(list(categories)),
                                    "actionType": "skip"})
    try:
        data = json.loads(http_get(API + "?" + query, timeout=15))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # nothing marked
            return []
        raise
    segments = []
    for item in data if isinstance(data, list) else []:
        times = item.get("segment") if isinstance(item, dict) else None
        if isinstance(times, list) and len(times) == 2 and all(isinstance(t, (int, float)) for t in times) \
                and times[1] > times[0]:
            segments.append({"start": float(times[0]), "end": float(times[1]), "category": item.get("category", "")})
    return sorted(segments, key=lambda s: s["start"])


def merge_segments(segments, categories):
    """The segments of these categories joined where they overlap: [(start, end, [categories])]."""
    merged = []
    for segment in segments:
        if segment["category"] not in categories:
            continue
        if merged and segment["start"] <= merged[-1][1]:
            start, end, names = merged[-1]
            merged[-1] = (start, max(end, segment["end"]),
                          names + ([segment["category"]] if segment["category"] not in names else []))
        else:
            merged.append((segment["start"], segment["end"], [segment["category"]]))
    return merged
