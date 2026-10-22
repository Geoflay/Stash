"""The Home tab and the Edit tab (a search stays on Home, a link opens Edit > Download and keeps what it holds),
the devices of the Wi-Fi finding each other, and the sizes of Settings (no network, no window)."""

import asyncio
import json
import urllib.request

import pytest

import test_download_options
from core.link_server import Beacon, LinkServer, find_devices, offer_link
from ui.style import APP_SCALES, TEXT_SCALES, density_of, scale_of

tab = test_download_options.tab  # the fixture: the Download tab with a video analyzed


async def test_search_stays_on_home_and_a_link_opens_edit(tab, monkeypatch):
    opened, searched = [], []
    tab.app.open_tool = opened.append

    async def search(text, *args, **kwargs):
        searched.append(text)
    monkeypatch.setattr(tab, "search", search)
    tab.url_input.value = "lofi music"
    await tab.analyze()
    assert searched == ["lofi music"] and opened == []
    assert tab.result_card.visible  # the video analyzed before is kept in Edit > Download
    tab.url_input.value = test_download_options.URL
    await tab.analyze()
    assert opened == ["download"] and tab.result_card.visible


def test_sizes():
    assert scale_of({}, "text_scale", TEXT_SCALES) == 1.0
    assert scale_of({"app_size": "large"}, "app_scale", APP_SCALES) == 1.1  # the menu the sliders replaced
    assert scale_of({"text_scale": 9}, "text_scale", TEXT_SCALES) == TEXT_SCALES[1]
    assert scale_of({"text_scale": True}, "text_scale", TEXT_SCALES) == 1.0
    assert [density_of(x) for x in (0.9, 1.0, 1.1, 1.3)] == ["compact", "compact", "standard", "comfortable"]


async def test_devices_find_each_other():
    offers = []
    server = LinkServer("secret", lambda link: None, on_offer=lambda *offer: offers.append(offer))
    server.start(port=8795)
    beacon = Beacon("Living room PC", "pc-id", server.server.server_port)
    beacon.start(port=8796)
    try:
        assert await asyncio.to_thread(find_devices, "pc-id", port=8796, wait=0.5) == []  # not itself
        found = await asyncio.to_thread(find_devices, "phone-id", port=8796, wait=0.5)
        assert [(d["name"], d["id"]) for d in found] == [("Living room PC", "pc-id")]
        device = dict(found[0], address=found[0]["address"].replace(found[0]["address"].split("/")[2].split(":")[0],
                                                                     "127.0.0.1"))
        url = test_download_options.URL
        assert await asyncio.to_thread(offer_link, device, "look: %s" % url, "My phone", "phone-id") == 1
        assert offers == [(url, "My phone", "phone-id")]
        # Without the key, only the "offer" address takes links (the app then asks its user).
        request = urllib.request.Request(device["address"] + "/other", data=json.dumps({"text": url}).encode(),
                                         method="POST")
        with pytest.raises(OSError):
            await asyncio.to_thread(urllib.request.urlopen, request, timeout=5)
    finally:
        beacon.stop()
        server.stop()
