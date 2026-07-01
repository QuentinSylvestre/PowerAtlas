# PowerAtlas Icon Pack - Round 2 #5 Safe Padding

Status: concept-derived prototype asset pack with conservative safe padding.

This pack keeps the selected #5 direction but scales the optical-centered source to 90% on a square canvas. The goal is to prevent the right edge from reading cropped while preserving the mark.

## Required Assets

- Desktop app: PNG sizes, Windows ICO, macOS iconset, and ICNS.
- Tray: PNG sizes and ICO.
- Favicon: PNG sizes and favicon.ico.
- Web UI top banner.
- PWA/touch icons.

## QC

- `docs/centering-qc.json`: margins before/after safe padding.
- `docs/required-assets-qc.json`: required asset coverage, dimensions, and ICO sizes.
- `concept-raster/previews/poweratlas-r2-05-safe-padding-preview.png`: visual preview sheet.
- `review-safe-padding-r2-05.html`: interactive feedback page.

## Known Compromise

This is still derived from the selected generated crop. It fixes the crop read with layout-safe padding, but a clean native 1024 master remains the right next step before public release.
