# Saved results

Every analysis is saved here in its own folder (`runs/<run id>/`), and opening it again on the dashboard is instant.

One result is included in the repository, so the dashboard shows a real map straight after the intro, before you run anything:

- `fb7507902568/` – Upper Trishuli, Syabrubesi to Rasuwagadhi, flood of 26 August 2026 (the main case study).

All other results stay on the computer that made them (they are not uploaded to Git).

## Data in this folder, and its licences

- **Map layers** (`layers/*.geojson`: buildings, roads, bridges, settlements and hospitals, with what the flood did to them) are made from **OpenStreetMap as it was before the flood**: © OpenStreetMap contributors, available under the [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/). These derived layers are shared under the same licence.
- **Satellite pictures** (`overlays/radar_*`, `overlays/photo_*`, the flood map): contain modified Copernicus Sentinel data 2026.
- **Height data** (`rasters/dem.tif`, `hydro_fdir.npy`, `overlays/hillshade.png`): produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018, provided under COPERNICUS by the European Union and ESA; all rights reserved.

The code of this project is under the MIT licence (see `LICENSE`); the data above keeps its own licences.
