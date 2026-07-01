# PowerAtlas Icon Pack - Round 2 #5

Status: concept-derived prototype asset pack.

Selected direction: Round 2 concept #5, a contained app-icon-first mark in the PowerAtlas dark blue/cyan visual system.

## What is included

- `input/app-icon-source.png`: normalized 1024x1024 source derived from the selected concept crop.
- `concept-raster/app/png/`: desktop app PNG sizes from 16 to 1024 px.
- `concept-raster/app/windows/poweratlas.ico`: multi-size Windows app icon.
- `concept-raster/tray/`: tray PNGs and ICO.
- `concept-raster/web/favicon/`: favicon PNGs and ICO.
- `concept-raster/web/pwa/`: common PWA/touch icon sizes.
- `concept-raster/web/banner/poweratlas-webui-banner-dark.png`: top-banner concept for the web UI.
- `concept-raster/previews/poweratlas-r2-05-asset-preview.png`: visual QA sheet.
- `review-selected-r2-05.html`: feedback page with sticky visual panel and copy-all output.

## Known compromise

The selected source is a crop from the Round 2 generated concept sheet. It has been padded and upscaled to 1024x1024 so the asset sizes can be reviewed, but it is not a clean native master. Before wiring this into a public GitHub product release, generate or redraw a clean individual 1024x1024 master in this exact direction.

## Suggested next step

Review `review-selected-r2-05.html`. If the direction passes small-size checks, create a clean master and then wire the final assets into `src/power_atlas/tray.py` and `src/power_atlas/templates/base.html`.
