# Icon Pack Integration

> **Date**: 2026-06-29
> **Status**: Draft  <!-- Status lifecycle: Exploring → Draft → In Progress → Complete -->
> **Scope**: Integrate r3-balanced-master-clean-banner icon pack into PowerAtlas — tray, favicon, banner, app icon

---

## Intent

### Problem statement & desired outcomes

PowerAtlas currently uses a synthetic Pillow-generated 16x16 blue "P" as its tray icon, has no favicon (empty `data:,` suppressor), no branding in the topbar (just text), and no app icon for shortcuts. The r3-balanced-master-clean-banner icon pack provides production-quality assets for all these surfaces. Integrating them gives the app a polished, branded appearance across all visible touchpoints.

### Success criteria

1. System tray icon displays the real `poweratlas-tray.ico` (multi-size 16-64px) instead of the synthetic "P"
2. Web UI shows real favicon (`favicon.ico` + `favicon-32x32.png`) in browser tabs
3. Topbar displays the clean banner image (80px height, flush left, no text title, seamless background blend at `#01070e`)
4. Autostart shortcut (`.lnk`) uses `poweratlas.ico` as its icon
5. `assets-source/` directory contains the source zip (tracked in git) for provenance
6. `pyproject.toml` includes `package_data` for `static/**` and `templates/**`

### Scope boundaries & non-goals

**In scope**: Tray icon, web favicon, topbar banner, autostart shortcut icon, asset provenance storage, pyproject.toml packaging fix.

**Non-goals**: PWA manifest/icons, logo mark usage in UI, macOS icns integration, banner height/layout experimentation beyond the settled 80px design, `_proto/` directory removal (separate task).


## Context

The r3-balanced-master-clean-banner icon pack (zip at `C:\Users\QSylvestre.POLESTAR\OneDrive - Pole Star\Downloads\poweratlas-icon-pack-r3-balanced-master-clean-banner.zip`) provides pre-sized raster assets. The app currently generates a synthetic tray icon at runtime (`tray.py:17-24`), uses `<link rel="icon" href="data:,">` as favicon suppressor (`base.html:6`), displays plain text "PowerAtlas" in the topbar (`index.html:3`), and the autostart shortcut has no custom icon (`autostart.py:28-32`).

Key technical constraints from exploration:
- pystray requires a `PIL.Image.Image` — use `Image.open()` on the .ico file
- FastAPI StaticFiles serves any file in `static/` with correct MIME types — no config needed
- setuptools with `include_package_data=True` (default for pyproject.toml builds) includes git-tracked files, but explicit `package_data` is safer
- Topbar background must be `#01070e` to match banner right-edge color; no left padding on banner

## Files to modify

| File | Change |
|---|---|
| `src/power_atlas/static/poweratlas-tray.ico` | Add (copy from pack `concept-raster/tray/ico/`) |
| `src/power_atlas/static/favicon.ico` | Add (copy from pack `concept-raster/web/favicon/`) |
| `src/power_atlas/static/favicon-32x32.png` | Add (copy from pack `concept-raster/web/favicon/`) |
| `src/power_atlas/static/poweratlas-banner.png` | Add (copy from pack `concept-raster/web/banner/poweratlas-webui-banner-dark.png`) |
| `src/power_atlas/static/poweratlas.ico` | Add (copy from pack `concept-raster/app/windows/`) |
| `src/power_atlas/tray.py` | Replace `_create_icon()` body — load .ico via `Image.open()` |
| `src/power_atlas/templates/base.html` | Replace `data:,` favicon with real favicon links |
| `src/power_atlas/templates/index.html` | Replace topbar title text with banner `<img>` |
| `src/power_atlas/static/style.css` | Update `.topbar` — bg color, height, padding |
| `src/power_atlas/autostart.py` | Set `shortcut.IconLocation` to the .ico path |
| `pyproject.toml` | Add `[tool.setuptools.package-data]` |
| `assets-source/poweratlas-icon-pack-r3-balanced-master-clean-banner.zip` | Add (copy zip from Downloads) |

## External Dependencies

None — code-only change, no infra or third-party services.

## Rollout / Migration / Cleanup

None — no persisted data affected. Existing autostart shortcuts will pick up the new icon on next `enable()` call (or manual toggle in UI).

## Step-by-step

### 1. Copy runtime assets into `static/` [QA]

Copy from the icon pack into `src/power_atlas/static/`:
- `concept-raster/tray/ico/poweratlas-tray.ico` → `static/poweratlas-tray.ico`
- `concept-raster/web/favicon/favicon.ico` → `static/favicon.ico`
- `concept-raster/web/favicon/favicon-32x32.png` → `static/favicon-32x32.png`
- `concept-raster/web/banner/poweratlas-webui-banner-dark.png` → `static/poweratlas-banner.png`
- `concept-raster/app/windows/poweratlas.ico` → `static/poweratlas.ico`

Copy the source zip:
- `poweratlas-icon-pack-r3-balanced-master-clean-banner.zip` → `assets-source/`

### 2. Replace tray icon [QA]

In `tray.py`, replace `_create_icon()` with fallback on missing file:

```python
def _create_icon() -> Image.Image:
    icon_path = Path(__file__).parent / "static" / "poweratlas-tray.ico"
    try:
        with Image.open(icon_path) as img:
            img.load()
            return img.copy()
    except OSError:
        log.warning("Tray icon not found at %s, using fallback", icon_path)
        img = Image.new("RGBA", (16, 16), (60, 120, 220, 255))
        ImageDraw.Draw(img).text((3, 1), "P", fill="white")
        return img
```

Update imports: add `from pathlib import Path` and `import logging`; keep `ImageDraw` for fallback (remove `ImageFont`).
Add `log = logging.getLogger("power_atlas.tray")` at module level.

### 3. Add favicon to base template [QA]

In `templates/base.html`, replace `<link rel="icon" href="data:,">` with:

```html
<link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">
<link rel="icon" type="image/x-icon" href="/static/favicon.ico">
```

### 4. Replace topbar title with banner [QA]

In `templates/index.html`, replace:
```html
<div class="topbar-title">PowerAtlas</div>
```
with:
```html
<img src="/static/poweratlas-banner.png" class="topbar-banner" alt="PowerAtlas">
```

Note: `settings.html` still uses `.topbar-title` for its heading — do NOT remove the `.topbar-title` CSS rule.

In `static/style.css`, update `.topbar`:
```css
.topbar { display: flex; align-items: center; padding: 0 24px 0 0; height: 80px; border-bottom: 1px solid var(--border); gap: 16px; flex-shrink: 0; background: var(--topbar-bg); }
```

Add to `:root` variables:
```css
--topbar-bg: #01070e;
```

Add the banner img rule:
```css
.topbar-banner { height: 100%; object-fit: contain; object-position: left center; max-width: 50vw; }
```

Update `.cards-area` max-height to account for taller topbar (80px + search ~66px = ~146px):
```css
.cards-area { max-height: calc(100vh - 146px); ... }
```

### 5. Set autostart shortcut icon

In `autostart.py` `enable()`, add before `shortcut.save()`:

```python
icon_path = str(Path(__file__).parent / "static" / "poweratlas.ico")
shortcut.IconLocation = f"{icon_path},0"
```

Note: the icon path is absolute to the install location. If the venv is moved/rebuilt, the user must re-toggle autostart to update the shortcut. This is acceptable for a local dev tool.

### 6. Add package_data to pyproject.toml

Add section:
```toml
[tool.setuptools.package-data]
power_atlas = ["static/**", "templates/**"]
```

## Verification

- Run `power-atlas --foreground`: tray icon should show the real branded icon (not blue "P")
- Open the web UI in browser: favicon should appear in tab, topbar shows banner image flush-left at 80px with no visible seam
- Toggle autostart off then on: the shortcut at `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PowerAtlas.lnk` should have the custom icon
- Run `pytest` — existing tests should pass (no test coverage of tray icon creation)
- Verify `assets-source/` contains the zip

## Documentation updates

- Update `README.md` to mention the icon pack provenance in a brief "Branding" or "Assets" note.

## Review Log

### 2026-06-29 -- Plan Review (via /qplan, high effort)

4 personas (Senior engineer, Architect, End-user advocate, Reliability engineer). 9 findings (2 High, 3 Medium, 4 Low). All auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `Image.open()` has no fallback — missing .ico crashes app at startup | Resolved — added try/except with synthetic fallback + warning log |
| 2 | High | Plan removes `.topbar-title` CSS but `settings.html` uses it | Resolved — kept the rule; only index.html changes |
| 3 | Medium | `.cards-area` calc tuned for old 44px topbar; 80px clips content | Resolved — updated calc to `100vh - 146px` |
| 4 | Medium | `tray.py` needs `from pathlib import Path` import | Resolved — added to import list in plan |
| 5 | Medium | `shortcut.IconLocation` path breaks if venv moves | Resolved — documented limitation |
| 6 | Low | `IconLocation` COM property expects `"path,0"` format | Resolved — plan uses `f"{icon_path},0"` |
| 7 | Low | `Image.open()` holds file handle open | Resolved — plan uses `with` + `.copy()` pattern |
| 8 | Low | Hardcoded `#01070e` breaks CSS variable pattern | Resolved — uses `--topbar-bg` variable |
| 9 | Low | Favicon link order — `.png` first for modern browsers | Resolved — swapped order |
