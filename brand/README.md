# Brand asset generator

The shipped PNGs live in
[`custom_components/base_power/brand/`](../custom_components/base_power/brand/).
This directory holds only the tooling.

Home Assistant serves those files itself, through its Brands Proxy, so no
`home-assistant/brands` submission is needed and the repo does not have to be
public for the icon to appear.

**The proxy path is `/api/brands/integration/<domain>/<file>`.** That middle
`integration/` segment matters and is easy to miss — `/api/brands/<domain>/...`
returns 404, which looks exactly like the feature not existing. Verified on
2026-09-13 against HA 2026.9.2 by comparing byte counts: the proxy returned 6939
bytes for `base_power/icon.png`, which is this repo's file exactly, and likewise
for `tuxedo_touch` and `glkvm`.

**Do not test this against `brands.home-assistant.io` and read the status code.**
That CDN never 404s for an unknown domain — it answers **HTTP 200 with a 3039-byte
"icon not available" placeholder**, so a status check reports success while the
user sees a grey box. Compare the returned bytes against the local file instead.

The art is a stylized front view of the actual device — the stacked wall-mounted
battery cabinet — rather than Base's "BASE" wordmark. That mark is their
trademark, a generated approximation of a wordmark reads as wrong next to real
vendor logos, and in Devices & Services the integration is already captioned
"Base Power", so the icon's job is to say *which* energy integration this is
rather than to spell the name. Same convention as `ha-tuxedo-touch`.

The palette has to survive both Home Assistant themes: the cabinet carries its
own dark outline so it does not vanish on a light background, with light panels
inside it so it does not vanish on a dark one.

```
python generate.py            # contact sheet to preview/, ships nothing
python generate.py --final    # write the PNGs into custom_components/
```

`generate.py` renders at 4x and downsamples, and measures text to fit rather
than assuming a font size — the first draft hard-coded one and clipped the
wordmark to "Base Pow", because which font actually loads varies by machine.
