# Bundled geographic data

No map tiles, external API calls or IP lookups occur when viewing the dashboard.

- World outlines: Natural Earth 1:110m admin-0 data, distributed by `world-atlas` 2.0.2 (`countries-110m.json`). Natural Earth geography is public domain. world-atlas tooling has an ISC license.
  https://www.naturalearthdata.com/about/terms-of-use/
  https://github.com/topojson/world-atlas
- Approximate country coordinates and ISO code mapping: `world-countries` 5.1.0 by Mohammed Le Doze and contributors, from https://github.com/mledoze/countries. This country-position database and its derived entries in `map-data.js` are available under Open Database License 1.0; the included `countries-ODbL.txt` contains the license. Country positions represent approximate locations within countries; they are not IP geolocations or exact geodesic centroids.
- Geographic paths were projected at build time with d3-geo 3.1.1 and decoded with topojson-client 3.1.0. Their licenses are included alongside this file. These packages are not runtime dependencies of the Python application.

The map is a coarse geographic context visualization. Country outlines and labels are not a statement about disputed borders or sovereignty. It uses an equirectangular projection; distances and line shapes are not geographic travel measurements. Exported and GeoIP coordinates describe a sign-in's reported network location, potentially a VPN/proxy exit, rather than a verified physical user location.
