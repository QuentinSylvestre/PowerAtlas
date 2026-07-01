# PowerAtlas Icon Pack - Round 2 #5 Optical Centered

Status: optically centered concept-derived prototype asset pack.

This replaces the previous centered prototype, which centered mostly against the bright foreground strokes. The screenshot review showed the visible rounded icon panel still read down-right. This pack crops/reframes against the visible rounded panel and re-exports all required assets.

## Required Assets

- Desktop app: `concept-raster/app/png/`, `concept-raster/app/windows/poweratlas.ico`, `concept-raster/app/macos/PowerAtlas.iconset/`, and `concept-raster/app/macos/PowerAtlas.icns`.
- Tray: `concept-raster/tray/png/` and `concept-raster/tray/ico/poweratlas-tray.ico`.
- Favicon: `concept-raster/web/favicon/favicon.ico`, `favicon-16x16.png`, `favicon-32x32.png`, and `favicon-48x48.png`.
- Web UI top banner: `concept-raster/web/banner/poweratlas-webui-banner-dark.png`.
- PWA/touch icons: `concept-raster/web/pwa/`.

## QC

- `docs/centering-qc.json`: before/after optical centering metrics.
- `docs/required-assets-qc.json`: required asset coverage, dimensions, and ICO sizes.
- `concept-raster/previews/poweratlas-r2-05-optical-centered-preview.png`: visual preview sheet.
- `review-optical-centered-r2-05.html`: interactive feedback page.

## Known Compromise

This is still derived from the selected generated crop. It is suitable for prototype wiring and visual approval. Before a public release, generate or redraw a clean native 1024 master in this exact direction.
