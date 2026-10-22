from core.subtitles import (
    iso_language, cues_to_srt, parse_cues, split_subtitle_key, subtitle_choices, subtitle_key, subtitle_label,
    subtitle_suffix, subtitle_tag,
)

VTT = """WEBVTT

NOTE hello

1
00:00:01.000 --> 00:00:03.500
<c>Hello</c> &amp; world

00:00:03.500 --> 00:00:05.000
Hello &amp; world
second line

01:00:05.5 --> 01:00:06.25
late
"""


def test_transcript(tmp_path):
    from core.subtitles import make_transcript, transcript_paragraphs
    cues = [(0.0, 2.0, ["Hello", "everyone."]), (2.0, 4.0, ["Today:"]), (2.0, 4.0, ["Today:"]),
            (7.0, 9.0, ["Q & A part"]), (70.0, 72.0, ["Much later."])]
    # A pause of 2 s or more starts a paragraph; the repeated line is dropped.
    assert transcript_paragraphs(cues) == [(0.0, "Hello everyone. Today:"), (7.0, "Q & A part"),
                                           (70.0, "Much later.")]
    srt = tmp_path / "subs.srt"
    srt.write_text(cues_to_srt(cues), encoding="utf-8")
    text = make_transcript(srt, "txt", "Course", "https://x.test/v", tmp_path).read_text(encoding="utf-8")
    assert text == "Course\nhttps://x.test/v\n\nHello everyone. Today:\n\nQ & A part\n\nMuch later.\n"
    page = make_transcript(srt, "html", "Q&A", "https://x.test/v?a=1&b=2", tmp_path).read_text(encoding="utf-8")
    assert "<title>Q&amp;A</title>" in page and 'href="https://x.test/v?a=1&amp;b=2"' in page
    assert '<span class="time">1:10</span>Much later.' in page and "Q &amp; A part" in page


def test_parse_cues():
    assert parse_cues(VTT) == [(1.0, 3.5, ["Hello & world"]), (3.5, 5.0, ["Hello & world", "second line"]),
                               (3605.5, 3606.25, ["late"])]


def test_parse_cues_removes_repeated_lines():
    # Auto-generated YouTube subtitles repeat the previous line in each cue.
    assert parse_cues(VTT, dedupe=True)[1] == (3.5, 5.0, ["second line"])


def test_srt():
    assert cues_to_srt(parse_cues(VTT)) == (
        "1\n00:00:01,000 --> 00:00:03,500\nHello & world\n\n"
        "2\n00:00:03,500 --> 00:00:05,000\nHello & world\nsecond line\n\n"
        "3\n01:00:05,500 --> 01:00:06,250\nlate\n")


def test_srt_of_a_clip_starts_at_zero():
    assert cues_to_srt(parse_cues(VTT), 2.0, 4.0) == (
        "1\n00:00:00,000 --> 00:00:01,500\nHello & world\n\n"
        "2\n00:00:01,500 --> 00:00:02,000\nHello & world\nsecond line\n")


def test_languages_offered():
    data = {"subtitles": {"en": [{"name": "English"}], "live_chat": [{}], "fr": [{}]},
            "automatic_captions": {"en-orig": [{"name": "English (Original)"}], "de": [{}]}, "language": "en"}
    choices = subtitle_choices(data)
    assert choices == [{"lang": "en", "name": "English", "auto": False}, {"lang": "fr", "name": "fr", "auto": False},
                       {"lang": "en-orig", "name": "English", "auto": True},
                       {"lang": "de", "name": "de", "auto": True, "translated": True}]
    assert [subtitle_key(c) for c in choices] == ["m:en", "m:fr", "a:en-orig", "a:de"]
    assert [subtitle_label(c) for c in choices] == ["English (en)", "fr", "English (auto-generated)",
                                                    "de (auto-translated)"]


def test_auto_captions_in_the_video_language_then_translated():
    data = {"subtitles": {"es": [{"name": "Spanish"}]},
            "automatic_captions": {"de": [{}], "it": [{"name": "Italian"}], "en": [{}, {"name": "English"}],
                                   "es": [{"name": "Spanish"}]}, "language": "de-DE"}
    choices = subtitle_choices(data)
    assert choices == [{"lang": "es", "name": "Spanish", "auto": False}, {"lang": "de", "name": "de", "auto": True},
                       {"lang": "en", "name": "English", "auto": True, "translated": True},
                       {"lang": "it", "name": "Italian", "auto": True, "translated": True}]
    assert subtitle_key(choices[2]) == "a:en" and subtitle_label(choices[2]) == "English (auto-translated)"
    # "en" is the video's own language: its translation is the same as the auto-generated subtitles.
    data = {"automatic_captions": {"en-orig": [{}], "en": [{}], "fr": [{"name": "French"}]}}
    assert [c["lang"] for c in subtitle_choices(data)] == ["en-orig", "fr"]


def test_keys_and_names():
    assert split_subtitle_key("a:en-orig") == ("en-orig", True)
    assert subtitle_suffix("en-orig") == ".en.srt"
    assert subtitle_tag("en-orig") == ".en"
    assert iso_language("fr-orig") == "fra"
