# Brand asset generator

The shipped PNGs live in
[`custom_components/base_power/brand/`](../custom_components/base_power/brand/).
This directory holds only the tooling.

Home Assistant serves those files itself through its Brands Proxy, so no
`home-assistant/brands` submission is needed and the repo does not have to be
public for the icon to appear.

Two traps, each of which looks exactly like the feature not existing:

The proxy path is `/api/brands/integration/<domain>/<file>`. That middle
`integration/` segment is required; `/api/brands/<domain>/...` returns 404.
Verified 2026-09-13 against HA 2026.9.2 by comparing byte counts: the proxy
returned 6939 bytes for `base_power/icon.png`, matching this repo's file
exactly, and likewise for `tuxedo_touch` and `glkvm`.

Do not test against `brands.home-assistant.io` and read the status code. That
CDN never 404s for an unknown domain. It answers HTTP 200 with a 3039-byte
"icon not available" placeholder, so a status check reports success while the
user sees a grey box. Compare returned bytes against the local file instead.

The art is a stylized front view of the device, the stacked wall-mounted
battery cabinet, rather than Base's "BASE" wordmark. That mark is their
trademark, a generated approximation reads as wrong next to real vendor logos,
and Devices & Services already captions the integration "Base Power", so the
icon's job is to say which energy integration this is rather than spell the
name. Same convention as `ha-tuxedo-touch`.

The palette has to survive both Home Assistant themes: the cabinet carries its
own dark outline so it does not vanish on a light background, with light
panels inside so it does not vanish on a dark one.

```
python generate.py            # contact sheet to preview/, ships nothing
python generate.py --final    # write the PNGs into custom_components/
```

`generate.py` renders at 4x and downsamples, and measures text to fit rather
than assuming a font size. Which font loads varies by machine, and a
hard-coded size clipped the wordmark to "Base Pow".
