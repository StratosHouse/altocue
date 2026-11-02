# Map shapes

`countries-110m.json` holds country and land outlines for the map formats.

- **Source:** Natural Earth 1:110m cultural vectors, public domain, from [naturalearthdata.com](https://www.naturalearthdata.com/).
- **Packaging:** the [world-atlas](https://github.com/topojson/world-atlas) npm package, version 2, ISC licence, `countries-110m.json`.
- **Changes:** converted to compact SVG path syntax by `scripts/build_map_data.py`, in twentieths of a degree of longitude and latitude. Shapes and names are the Natural Earth defaults. No border is edited and no country is handled differently.
- **Rebuild:** `uv run python scripts/build_map_data.py`.
