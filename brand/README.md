# Brand asset generator

The shipped PNGs live in [`custom_components/base_power/brand/`](../custom_components/base_power/brand/). The approved icon source is [`icon-source.png`](icon-source.png); this directory also holds the logo generator.

Home Assistant serves those files itself through its Brands Proxy, so no `home-assistant/brands` submission is needed and the repo does not have to be public for the icon to appear.

Two traps, each of which looks exactly like the feature not existing:

The proxy path is `/api/brands/integration/<domain>/<file>`. That middle `integration/` segment is required; `/api/brands/<domain>/...` returns 404. The proxy serves this repo's file byte for byte.

Do not test against `brands.home-assistant.io` and read the status code. That CDN never 404s for an unknown domain. It answers HTTP 200 with a 3039-byte "icon not available" placeholder, so a status check reports success while the user sees a grey box. Compare returned bytes against the local file instead.

The icon is a sculpted power cell with a charge bolt. The wide logos retain the drawn wall-mounted battery cabinet and wordmark.

The icon is transparent and reads on both Home Assistant themes.

```
python generate.py            # contact sheet to preview/, ships nothing
python generate.py --final    # write the PNGs into custom_components/
```

`generate.py` scales the approved icon and renders the wide logos with fitted text.
