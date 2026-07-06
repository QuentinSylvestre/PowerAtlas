# Merged Multi-Provider Workspace Cards

> **Date**: 2026-07-06
> **Status**: Complete  <!-- Status lifecycle: Exploring → Draft → In Progress → Complete -->
> **Estimated effort**: 2-3 days
> **Scope**: Merge per-provider workspace cards into unified cards with gradient borders, top-level provider filter, redesigned hover actions, and revised multi-select semantics.

---

## Intent

### Problem statement & desired outcomes

PowerAtlas currently shows one workspace card per provider — if both kiro-cli and claude-code have sessions for the same folder, two separate cards appear. This creates visual clutter, makes it harder to see which workspaces are active across tools, and fragments the user's mental model of "my workspaces." The provider tabs currently filter which provider's cards are visible, but the underlying card-per-provider model remains.

The goal is to unify workspace representation: one card per physical folder, aggregating all providers' data. Provider identity is communicated through visual cues (gradient border, session row colors, provider icons on launch buttons) rather than card duplication. The provider filter becomes a global lens that controls which sessions are visible, not which cards exist.

### Success criteria

1. **SC1 — Merged workspace cards**: Each unique workspace path produces exactly one card, regardless of how many providers have sessions for it. Single-provider cards show a solid left border; multi-provider cards show a vertical gradient split (top = first provider color, bottom = second) via `::before` pseudo-element.

2. **SC2 — Top-level provider filter**: Provider tabs move inline with the search bar as a global filter. Selecting a provider filters all panels (pinned sessions, pinned workspaces, workspace cards). "All" shows everything interleaved. Cards with zero sessions for the filtered provider are hidden.

3. **SC3 — Per-provider hover launch buttons**: On card header hover, one launch button per available provider appears (replacing the single `>_` button). Each button shows the provider's color indicator and launches a new session in that provider. Pin button remains provider-agnostic.

4. **SC4 — Interleaved sessions with provider identification**: When a card is expanded (with "All" filter), sessions from all providers are interleaved by `updated_at` descending. Each session row shows its provider via left border color and the resume button is replaced by the provider icon (small, 16x16).

5. **SC5 — Revised multi-select semantics**: "Launch selected" action bar button is enabled only when session rows are selected (not workspace cards). Selected sessions launch in their native provider. Selected workspaces are acted upon only via launcher tiles in the left panel.

6. **SC6 — Provider-agnostic pinning**: Pinned folders become path-only (`list[str]`), with config migration from the current `list[dict]` format. Pin/unpin APIs drop the `provider` parameter.

7. **SC7 — Server-side session merge**: `/partials/sessions` endpoint supports `provider=all` to query all providers' caches and return interleaved, sorted results. Active provider filter is passed through to session loading.

8. **SC8 — Search grouping**: Search results apply the same grouping logic — one merged card per matching workspace path, regardless of how many providers have data.

### Scope boundaries & non-goals

**In scope**: Card merging with gradient border, provider filter relocation, hover action redesign, session interleaving, multi-select behavior change, pin simplification, search result grouping, config migration, updated tests.

**Non-goals**: New provider support (Kiro IDE session discovery). Session title search. Workspace reordering/sorting controls. Provider-specific session counts in split display (decided: filter-aware single count). Real-time session updates. Mobile/responsive layout changes.

---

## Discovery

### Existing patterns & constraints

- `data.py:137-168` — `discover_workspaces_with_counts()` returns flat `(cwd, count, updated_at, provider_name)` tuples; one row per (workspace, provider). This API is preserved unchanged.
- `data.py:62-100` — `SessionCache` uses compound key `(provider, normalized_cwd)`. Query both keys to get all providers' sessions for a workspace.
- `data.py:28-32` — `_normalize_path()` does casefold + backslash normalization on Windows. This is the grouping key.
- `web.py:234-245` — Provider tabs currently rendered inside right panel with htmx `hx-get="/partials/workspaces?provider=X"`.
- `web.py:19-33` — `PROVIDER_COLORS`, `PROVIDER_DISPLAY_NAMES`, `PROVIDER_BADGES` dicts define provider visual identity.
- `workspace_card.html:1` — Card has `data-cwd` and `data-provider` attributes; `style="border-left: 3px solid {{ provider_color }}"`.
- `session_row.html:1` — Row has `data-sid`, `data-cwd`, `data-provider` attributes; already self-sufficient for launch dispatch.
- `index.html` — `refreshCards()` tracks expanded state by `cwd|provider` key; `launchSelected()` reads `data-provider` from cards; `getSelectedWorkspaceCwds()` deduplicates paths.
- `config.py:26` — `pinned_folders: list[dict]` with `{"folder": path, "provider": provider}` entries.
- `config.py:79-80` — Existing migration pattern: `list[str]` → `list[dict]`. New migration reverses this.
- `style.css:82-95` — `.workspace-card` and `.card-actions` styling; actions absolutely positioned with opacity transition.
- `style.css:265-286` — `.provider-tabs` and `.provider-tab` classes.
- AGENTS.md: update existing tests when implementation changes; no new test files unless requested.
- Project MEMORY.md: cache getters must return copies; custom htmx-mini requires `process()` after innerHTML swaps.

### Risks & mitigations

- **border-image kills border-radius**: Confirmed in prototype. Mitigation: use `::before` pseudo-element approach (validated in `_proto/border-gradient-prototype.html`).
- **Expanded state key change**: `cwd|provider` → `cwd` in JS. Risk: stale localStorage or race conditions during migration. Mitigation: the key is ephemeral (session-lifetime only, rebuilt on page load), no persistence concern.
- **Config migration for pinned_folders**: `list[dict]` → `list[str]` loses the provider association. Mitigation: intentional — pinning becomes workspace-level. Migration deduplicates folder paths. Same pattern as existing `config.py:79-80` migration.
- **Three parallel fetches on tab switch**: Risk of brief inconsistency between panels. Mitigation: acceptable — each panel renders independently, same as current refresh pattern.
- **Session sort performance**: Merging two providers' sessions and sorting. Risk: negligible — both are already cached, sort is O(n log n) on <100 items typically.

### Resolved decisions

- Q1: Session ordering within expanded card — A: ok — Decision: Interleaved by `updated_at` descending, all providers mixed, identified by border color.
- Q2: Hover launch buttons and pin behavior — A: ok — Decision: Per-provider launch buttons (new session in that provider); pin becomes provider-agnostic (folder-only).
- Q3: Provider filter scope — A: ok — Decision: Global filter affects all panels (pinned sessions, pinned workspaces, workspace cards). Cards with zero sessions for filtered provider are hidden.
- Q4: Border visual for multi-provider cards — A: likes gradient split (#1), not dependent on card height — Decision: Vertical gradient split via `::before` pseudo-element (preserves border-radius). Single-provider = solid border. Split signals multi-provider.
- Q5: Session row resume button — A: ok — Decision: Replace `>_` glyph with small (16x16) provider icon. Same onclick behavior, clearer visual identity.
- Q6: Multi-select behavior — A: ok — Decision: "Launch selected" enabled only for session rows (each launches in native provider). Workspace card selection → acted upon via launcher tiles only.
- Q7: Pinned folders config migration — A: ok — Decision: Simplify to `list[str]`. Migration in `load_config()` extracts unique folder paths. Pin/unpin APIs drop `provider` param.
- Q8: Session count on merged cards — A: ok — Decision: Filter-aware count. "All" = total; specific provider = that provider's count only.
- Q9: Hover action overlay — A: ok — Decision: Overlay on top (current approach extended wider). No layout shift. Background covers underlying elements.
- Q10: Grouping logic location — A: ok — Decision: Group in web layer (not data layer). Keep `discover_workspaces_with_counts()` flat API unchanged. `defaultdict` grouping in render endpoints.
- Q11: Session loading for merged cards — A: ok — Decision: Single request, server-side merge. `/partials/sessions?provider=all` queries all providers, sorts, returns interleaved rows.
- Q12: Card data attributes — A: ok — Decision: Remove `data-provider` from cards. Cards identified by `data-cwd` only. Session rows retain `data-provider`. Provider buttons self-encode their target.
- Q13: Search behavior — A: keep path-only matching + pinned session title matching — Decision: Same functional behavior, apply grouping to workspace results.
- Q14: Provider filter tab position — A: inline with search bar — Decision: Tabs inline in search area as flex row: `[search input] [All | Kiro CLI | Claude Code]`.
- Q15: Inline "new session" element — A: ok — Decision: Remove. Per-provider hover buttons replace this affordance.
- Q16: Tab switch rendering — A: ok — Decision: Three parallel htmx requests (one per panel). Matches existing refresh pattern.
- Q17: Expanded state across tab switches — A: ok — Decision: Preserve expanded state, re-fetch sessions with new filter. Key simplifies from `cwd|provider` to `cwd`.
- Q18: Session row border color when filtered — A: ok — Decision: Always show. No conditional logic.

### Open items

None — all design decisions resolved.

### Recommended approach

**Phase 1 — Data & config layer**: Simplify `pinned_folders` to `list[str]` with migration. Add `provider=all` support to `/partials/sessions` endpoint (server-side merge and sort).

**Phase 2 — Card grouping in web layer**: Refactor `partials_workspaces()`, `partials_pinned_workspaces()`, and `search()` to group flat workspace data by normalized cwd before rendering. Update `workspace_card.html` template to accept a `providers` list instead of single provider. Remove `data-provider` from card element.

**Phase 3 — Card UI**: Implement `::before` gradient border for multi-provider cards. Redesign hover actions with per-provider launch buttons. Update session row resume button to provider icon.

**Phase 4 — Provider filter relocation**: Move tabs inline with search bar. Wire tab clicks to trigger three parallel panel refreshes with `?provider=X` param. Pass filter through to all panel endpoints.

**Phase 5 — Multi-select & launch logic**: Update `launchSelected()` to only process session rows. Disable action bar button when only workspace cards selected. Update `refreshCards()` expanded-state key.

**Phase 6 — Tests & cleanup**: Update existing tests for new card grouping, config migration, session merge, and filter behavior. Remove prototype file.


---

## 1) Current State

**Config layer** (`config.py:23-31`):
- `pinned_folders: list[dict]` — entries are `{"folder": path, "provider": "kiro-cli"}` dicts
- Migration at line 64: `list[str]` → `list[dict]` for backward compatibility
- `provider_settings: dict[str, dict]` — keyed by provider name

**Data layer** (`data.py`):
- `discover_workspaces_with_counts(provider=None)` (line 137-168) — returns flat `(cwd, count, updated_at, provider_name)` tuples, one per (workspace, provider). Cached 30s.
- `SessionCache` (line 62-100) — compound key `(provider, normalized_cwd)`. `get(cwd, provider)` returns sessions for one provider.
- `get_sessions(cwd, provider)` (line 170-180) — loads from provider adapter, caches.
- `_normalize_path(p)` (line 28-32) — casefold + backslash on Windows. Grouping key.

**Web layer** (`web.py`):
- `partials_workspaces()` (line 196-264) — discovers workspaces, renders provider tabs + one card per `(cwd, provider)` row. Tab bar rendered inline when `len(providers) > 1`.
- `partials_pinned_workspaces()` (line 175-213) — renders pinned cards matched by `(normalized_cwd, provider)` set.
- `partials_sessions()` (line 353-380) — loads sessions for one `(cwd, provider)` pair.
- `search()` (line 268-316) — matches workspace paths, renders one card per provider match.
- `pin_folder()` / `unpin_folder()` (line 144-166) — accept `provider` param, store/remove `{folder, provider}` dicts.
- `_render_pinned_sessions()` (line 416-498) — renders pinned session rows, iterates providers.
- `PROVIDER_COLORS` / `PROVIDER_BADGES` / `PROVIDER_DISPLAY_NAMES` (line 19-33) — provider visual identity.

**Templates**:
- `workspace_card.html` — `data-cwd="{{ cwd }}" data-provider="{{ provider }}"`, `border-left: 3px solid {{ provider_color }}`. Single provider badge with icon/fallback.
- `session_row.html` — `data-sid="{{ session.session_id }}" data-cwd="{{ cwd }}" data-provider="{{ provider_name }}"`, provider border-left. Resume button `>_`.
- `index.html` — `refreshCards()` expanded key = `cwd|provider`; `launchSelected()` reads `card.dataset.provider`; `getSelectedWorkspaceCwds()` deduplicates by lowercase; `_activeProvider` tracks selected tab; `toggleCard()` fetches `/partials/sessions?provider=<card.dataset.provider>`.

**CSS** (`style.css`):
- `.workspace-card` (line 82) — background, border, border-radius.
- `.card-actions` (line 94) — absolute positioned, opacity 0→1 on hover.
- `.provider-tabs` (line 265) — flex, gap, inside right panel.
- `.session-row` (line 114) — grid layout, hover background.

## 2) Goal

Unify workspace cards to one-per-folder regardless of provider count. Provider identity moves from the card level to visual cues (gradient border, session row colors, per-provider hover buttons). Provider filter becomes a global lens applied to all panels via inline tabs.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Multi-provider border visual | `::before` pseudo-element with `linear-gradient` | `border-image` (kills border-radius), multiple stripes, dots/pips | Prototype-validated; preserves border-radius |
| Grouping location | Web layer (`defaultdict` in render endpoints) | Data layer (new grouped API) | Keeps flat data API clean; grouping is a presentation concern |
| Session merge endpoint | Single request `/partials/sessions?provider=all` | Multiple client requests per provider | Avoids round-trips; consistent with htmx server-rendered architecture |
| Provider filter position | Inline with search bar (flex row) | Between search and panels (new row), inside topbar | Saves vertical space; natural companion to search |
| Pinned folders config | `list[str]` (path-only) | Keep `list[dict]`, ignore provider field | Matches provider-agnostic pin semantics; simpler |
| Card identification (JS) | `data-cwd` only; drop `data-provider` | Comma-separated `data-providers` | Cards are workspace-level; provider info lives in rendered buttons/sessions |
| Multi-select launch | Sessions only for "Launch selected"; workspaces via launcher tiles | Allow workspace launch with provider chooser dialog | Cleaner separation; no ambiguity about which provider |
| Session count | Filter-aware (total on "All", provider-specific when filtered) | Split display "7|5", always total | Consistent with global filter lens; avoids header clutter |
| Resume button | Provider icon (16x16) replacing `>_` | Keep `>_` with colored background | Clearer identity in mixed-provider list |
| Expanded state key | `cwd` only | Keep `cwd|provider` | Cards are workspace-level; no provider dimension |

## 4) External Dependencies & Costs

### Required external changes

None. This is a code-only change with no infrastructure, CI/CD, IAM, cloud, or third-party service dependencies.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Config migration & session merge endpoint [QA] [P:2]

**Goal**: Simplify `pinned_folders` to `list[str]` and add `provider=all` support to the sessions endpoint.

**File scope**: `config.py`, `web.py`, `__main__.py`, `tests/test_config.py`, `tests/test_web.py`

**Changes**:

1. **`config.py`** — Change `pinned_folders` type and add reverse migration:

```python
@dataclass
class Config:
    # ...
    pinned_folders: list[str] = field(default_factory=list)  # paths only
    # ...
```

Migration in `load_config()`:
```python
# Migration: pinned_folders list[dict] → list[str] (provider-agnostic)
if config.pinned_folders and isinstance(config.pinned_folders[0], dict):
    seen = set()
    paths = []
    for entry in config.pinned_folders:
        folder = entry.get("folder", "")
        if folder and folder not in seen:
            seen.add(folder)
            paths.append(folder)
    config.pinned_folders = paths
# Keep existing list[str] → list[dict] migration for very old configs
elif config.pinned_folders and isinstance(config.pinned_folders[0], str):
    pass  # already correct format
```

2. **`web.py`** — Pin/unpin APIs drop `provider` param:

```python
@app.post("/api/pin-folder")
async def pin_folder(request: Request):
    body = await request.json()
    folder = body["folder"]
    config = load_config()
    if folder not in config.pinned_folders:
        config.pinned_folders.append(folder)
        save_config(config)
    return {"ok": True}

@app.post("/api/unpin-folder")
async def unpin_folder(request: Request):
    body = await request.json()
    folder = body["folder"]
    config = load_config()
    if folder in config.pinned_folders:
        config.pinned_folders.remove(folder)
        save_config(config)
    return {"ok": True}
```

3. **`web.py` — `/partials/sessions`** — Add `provider=all` support:

```python
@app.get("/partials/sessions", response_class=HTMLResponse)
async def partials_sessions(request: Request, cwd: str = "", provider: str = "all", fresh: int = 0):
    """Lazy-load sessions for a workspace card. provider=all merges all providers."""
    config = load_config()
    
    if provider == "all":
        # Merge sessions from all providers
        all_sessions = []
        for prov_name in data.PROVIDERS:
            if not data.PROVIDERS[prov_name].is_available():
                continue
            sessions = await asyncio.to_thread(data.get_sessions, cwd, prov_name)
            for s in sessions:
                all_sessions.append((s, prov_name))
        # Sort interleaved by updated_at descending
        all_sessions.sort(key=lambda x: x[0].updated_at or "", reverse=True)
        sessions_with_provider = all_sessions
    else:
        sessions = await asyncio.to_thread(data.get_sessions, cwd, provider)
        sessions_with_provider = [(s, provider) for s in sessions]
    
    # Render with provider info per row
    ...
```

4. **`__main__.py`** — Update `warmup_all` call sites. Currently `config.pinned_folders` (a `list[dict]`) is passed to `warmup_all(pinned_folders: list[str])`. After migration, this becomes `list[str]` and works correctly. No code change needed in `__main__.py` since the type now matches, but update the type annotation in `data.py:warmup_pinned` and `data.py:warmup_all` to remove the stale `list[str]` annotation (it was always receiving `list[dict]` in practice — a pre-existing bug that this migration incidentally fixes).

5. **Tests** — Update `test_config.py` for new migration path; update `test_web.py` for pin API signature change and `provider=all` sessions endpoint.

**Exit criteria**:
- [x] `pinned_folders` loads correctly from old `list[dict]` format (migration)
- [x] `pinned_folders` saves as `list[str]` in TOML
- [x] `/api/pin-folder` and `/api/unpin-folder` work without `provider` param
- [x] `/partials/sessions?cwd=X&provider=all` returns interleaved sessions sorted by `updated_at`
- [x] `/partials/sessions?cwd=X&provider=kiro-cli` still works (single provider)
- [x] `warmup_all` / `warmup_pinned` type annotations updated to match `list[str]`
- [x] Existing tests pass after updates

Implementation (2026-07-06, code: 88339f9)
Simplified `pinned_folders` from `list[dict]` (with folder+provider keys) to `list[str]` (paths only), reversing the migration direction: `load_config()` now converts old `list[dict]` entries to `list[str]` with deduplication. The `pin_folder` and `unpin_folder` endpoints were simplified to accept/remove plain path strings without a provider parameter. The `partials_sessions` endpoint now defaults to `provider="all"`, merging sessions from all available providers sorted by `updated_at` descending, while still supporting single-provider mode. Provider color is now passed to the session_row template for per-row provider identification. Added 7 new tests covering the migration, simplified pin/unpin, and both provider=all and single-provider session loading.

### Phase 2: Card grouping & template redesign [QA] [P:1]

**Goal**: Group workspace data by normalized path in render endpoints and redesign the card template for multi-provider support.

**File scope**: `web.py`, `templates/partials/workspace_card.html`, `templates/partials/session_row.html`, `static/style.css`

**Changes**:

1. **`web.py` — Grouping helper**:

```python
def _group_workspaces(workspace_data: list[tuple[str, int, str, str]], config) -> list[dict]:
    """Group flat (cwd, count, updated_at, provider) rows into one entry per workspace.
    
    Returns list of dicts:
    {
        "cwd": str,
        "folder_name": str,
        "providers": [{"name": str, "color": str, "count": int, "updated_at": str}],
        "total_count": int,
        "latest_updated": str,
    }
    Sorted by latest_updated desc.
    """
    from collections import defaultdict
    from .data import _normalize_path
    
    groups: dict[str, dict] = {}  # norm_cwd -> group dict
    original_cwds: dict[str, str] = {}  # norm -> original (first seen)
    
    for cwd, count, updated_at, prov_name in workspace_data:
        norm = _normalize_path(cwd)
        if norm not in groups:
            groups[norm] = {"providers": [], "total_count": 0, "latest_updated": ""}
            original_cwds[norm] = cwd
        g = groups[norm]
        g["providers"].append({
            "name": prov_name,
            "color": _get_provider_color(prov_name, config),
            "count": count,
            "updated_at": updated_at,
        })
        g["total_count"] += count
        if updated_at > g["latest_updated"]:
            g["latest_updated"] = updated_at
    
    result = []
    for norm, g in groups.items():
        cwd = original_cwds[norm]
        result.append({
            "cwd": cwd,
            "folder_name": Path(cwd).name or cwd,
            "providers": g["providers"],
            "total_count": g["total_count"],
            "latest_updated": g["latest_updated"],
        })
    result.sort(key=lambda x: x["latest_updated"], reverse=True)
    return result
```

2. **`web.py` — `partials_workspaces()`** — Use grouping, filter by active provider:

```python
# Filter: if provider != "all", only include groups that have the selected provider
# Count shown: filter-aware (total if "all", provider-specific otherwise)
grouped = _group_workspaces(workspace_data)
if provider != "all":
    grouped = [g for g in grouped if any(p["name"] == provider for p in g["providers"])]
```

3. **`workspace_card.html`** — Redesigned template:

```html
<div class="workspace-card{% if stale %} stale{% endif %}{% if providers|length > 1 %} multi-provider{% endif %} collapsed"
     data-cwd="{{ cwd }}"
     {% if providers|length > 1 %}data-providers="{{ providers|map(attribute='name')|join(',') }}"{% endif %}>
  {% if providers|length > 1 %}
  <span class="provider-gradient" style="background: linear-gradient(to bottom, {{ providers[0].color }} 50%, {{ providers[1].color }} 50%)"></span>
  {% endif %}
  <div class="card-header" onclick="toggleCard(this.parentElement)">
    <!-- checkbox, chevron, icon, folder name, provider dots, count, actions -->
    ...
    <div class="card-actions">
      {% for p in providers %}
      <button class="card-action-btn" onclick="event.stopPropagation();launchNew(this,'{{ p.name }}')" aria-label="New {{ p.name }} session">
        <span class="provider-indicator" style="background: {{ p.color }}"></span> &gt;_
      </button>
      {% endfor %}
      <button class="pin-btn{% if is_pinned %} pinned{% endif %}" onclick="event.stopPropagation();pinWorkspace(this)">📌</button>
    </div>
  </div>
  ...
</div>
```

4. **`session_row.html`** — Replace resume `>_` with provider icon:

```html
<button class="row-btn primary terminal-btn" onclick="resumeSession(this)" aria-label="Resume">
  <img src="/api/launcher-icon/provider--{{ provider_name }}" class="session-provider-icon" alt="{{ provider_name }}">
</button>
```

5. **`style.css`** — Add multi-provider card styles:

```css
.workspace-card.multi-provider { position: relative; padding-left: 3px; }
.workspace-card.multi-provider .provider-gradient {
  position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  border-radius: var(--radius) 0 0 var(--radius);
}
.session-provider-icon { width: 16px; height: 16px; vertical-align: middle; }
```

6. **Apply same grouping to `partials_pinned_workspaces()` and `search()`**.

**Exit criteria**:
- [x] Workspace cards are grouped by path — same folder appears once regardless of provider count
- [x] Single-provider cards have solid left border (unchanged look)
- [x] Multi-provider cards show gradient split via `::before`/span pseudo-element
- [x] Hover actions show one launch button per available provider
- [x] Session rows show provider icon instead of `>_` on resume button
- [x] Search results are grouped the same way
- [x] `data-provider` attribute removed from card element
- [x] Pinned workspaces panel uses same grouping

Implementation (2026-07-06, code: 4c65d07)
Implemented workspace card grouping by normalized path using a new `_group_workspaces()` helper that merges flat `(cwd, count, updated_at, provider)` tuples into grouped dicts with a `providers` list. All three render endpoints (`partials_workspaces`, `partials_pinned_workspaces`, `search`) now call this helper and pass the grouped `providers` list to the template instead of single-provider variables. The `workspace_card.html` template was fully redesigned: `data-provider` attribute removed, single-provider cards get a solid left border via inline style, multi-provider cards get a `.provider-gradient` span with a CSS linear-gradient. The hover actions loop over all providers rendering one launch button per provider with a colored dot. The `session_row.html` resume button now shows the provider icon with onerror fallback. CSS additions provide `.multi-provider`, `.provider-gradient`, `.provider-dot`, and `.session-provider-icon` styles. The pinned set logic was changed from `(norm_path, provider)` tuples to plain `norm_path` sets.

### Phase 3: Provider filter relocation & global wiring [QA]

**Goal**: Move provider tabs inline with search bar and wire as a global filter affecting all three panels.

**File scope**: `web.py`, `templates/index.html`, `static/style.css`

**Changes**:

1. **`index.html`** — Move tabs from `partials_workspaces` response to static HTML inline with search:

```html
<div class="search-area">
  <div class="command-bar">
    <span class="search-icon">⌕</span>
    <input type="search" name="q" placeholder="Search sessions, folders..." aria-label="Search workspaces"
           hx-get="/search" hx-trigger="input changed delay:300ms" hx-target="#workspace-cards">
  </div>
  <div class="provider-filter" id="providerFilter" role="tablist">
    <!-- Rendered by JS or initial page load based on available providers -->
  </div>
</div>
```

2. **`web.py`** — Add endpoint to get available providers for filter rendering:

```python
@app.get("/api/available-providers")
async def api_available_providers():
    providers = data.available_providers()
    config = load_config()
    providers = [p for p in providers if config.provider_settings.get(p, {}).get("enabled", True)]
    return [{"name": p, "display": PROVIDER_DISPLAY_NAMES.get(p, p), "color": _get_provider_color(p, config)} for p in providers]
```

3. **`web.py`** — Remove tab rendering from `partials_workspaces()` (tabs are now static in page).

4. **`index.html` JS** — Tab click triggers three parallel fetches:

```javascript
function switchProvider(provider) {
  window._activeProvider = provider;
  // Update tab active state
  document.querySelectorAll('.provider-filter-btn').forEach(function(b) {
    b.classList.toggle('active', b.dataset.provider === provider);
    b.setAttribute('aria-selected', b.dataset.provider === provider ? 'true' : 'false');
  });
  // Refresh all three panels with new filter
  var qs = '?provider=' + encodeURIComponent(provider);
  fetch('/partials/workspaces' + qs).then(r => r.text()).then(html => {
    var el = document.getElementById('workspace-cards');
    el.innerHTML = html;
    if (window.htmx) htmx.process(el);
    loadExpandedCards();
  });
  fetch('/partials/pinned-sessions' + qs).then(r => r.text()).then(html => {
    var el = document.getElementById('pinned-cards');
    el.innerHTML = html;
    if (window.htmx) htmx.process(el);
  });
  fetch('/partials/pinned-workspaces' + qs).then(r => r.text()).then(html => {
    var el = document.getElementById('pinned-workspaces');
    el.innerHTML = html;
    if (window.htmx) htmx.process(el);
  });
}
```

5. **`web.py`** — Add `provider` param to `partials_pinned_sessions()` and `partials_pinned_workspaces()` endpoints:

```python
@app.get("/partials/pinned-sessions", response_class=HTMLResponse)
async def partials_pinned_sessions(request: Request, provider: str = "all", fresh: int = 0):
    # Pass provider to _render_pinned_sessions for filtering
    ...

@app.get("/partials/pinned-workspaces", response_class=HTMLResponse)
async def partials_pinned_workspaces(request: Request, provider: str = "all", fresh: int = 0):
    # Filter grouped pinned workspaces by active provider
    ...
```

6. **`style.css`** — Relocate tab styles to inline with search:

```css
.provider-filter { display: flex; gap: 4px; align-items: center; }
.provider-filter-btn { 
  padding: 4px 12px; border: none; border-radius: 12px;
  background: var(--card-bg); color: var(--text-muted); cursor: pointer;
  font-size: 12px; transition: background 0.15s, color 0.15s;
}
.provider-filter-btn:hover { background: var(--surface-hover); }
.provider-filter-btn.active { background: var(--accent); color: #fff; }
```

7. **`index.html`** — Update `toggleCard()` and `refreshCards()`:
- `toggleCard()`: fetch sessions using `window._activeProvider` instead of `card.dataset.provider`
- `refreshCards()`: expanded state key = `card.dataset.cwd` (not `cwd|provider`)

**Exit criteria**:
- [x] Provider tabs render inline with search bar
- [x] Selecting a tab filters all three panels (workspace cards, pinned workspaces, pinned sessions)
- [x] "All" tab shows all workspaces; specific provider tab hides cards with zero sessions for that provider
- [x] Tab state is preserved across page refreshes (via `_activeProvider` + initial render)
- [x] Old `.provider-tabs` CSS removed from right panel
- [x] `toggleCard()` uses active filter for session loading
- [x] `refreshCards()` expanded key simplified to `cwd`
- [x] Provider filter buttons have `role="tab"`, `aria-selected`, and keyboard navigation support
- [x] Update README.md Features section (provider filter is now a top-level UI element)

Implementation (2026-07-06, code: bdfbe1f)
Relocated the provider filter from server-rendered tab bar inside `partials_workspaces()` to a client-side inline element next to the search bar. Added `/api/available-providers` endpoint returning enabled providers with display names and colors. New `initProviderFilter()` JS function renders pill-shaped filter buttons with `role="tab"`, `aria-selected`, and keyboard arrow-key navigation. `switchProvider()` updates all three panels by passing `?provider=` query param. Both pinned endpoints now accept the `provider` param and filter results accordingly. Updated `toggleCard()`, `loadExpandedCards()`, `refreshExpandedSessions()`, and `refreshCards()` to use `window._activeProvider`. Simplified expanded-state key from `cwd|provider` to `cwd`. Replaced old `.provider-tabs` CSS with new `.provider-filter` styles. Updated README Features section.

### Phase 4: Multi-select & launch logic [QA]

**Goal**: Update selection and launch behavior — sessions-only for "Launch selected", workspaces via launcher tiles.

**File scope**: `templates/index.html`, `web.py`

**Changes**:

1. **`index.html` — `launchSelected()`** — Only process session rows:

```javascript
function launchSelected() {
  var sessions = [];
  document.querySelectorAll('.session-row.selected').forEach(function(r) {
    sessions.push({
      session_id: r.dataset.sid,
      workspace: r.dataset.cwd,
      provider: r.dataset.provider
    });
  });
  if (!sessions.length) return;
  if (sessions.length > 5 && !confirm('Launch ' + sessions.length + ' sessions?')) return;
  fetch('/api/launch-batch', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({sessions: sessions})}).then(r => r.text()).then(showToast);
}
```

2. **`index.html` — `updateActionBar()`** — Enable only when sessions are selected:

```javascript
function updateActionBar() {
  var sessionCount = document.querySelectorAll('.session-row.selected').length;
  var workspaceCount = document.querySelectorAll('.workspace-card.selected').length;
  document.getElementById('selCount').textContent = sessionCount + workspaceCount;
  document.getElementById('actionBar').classList.toggle('visible', sessionCount > 0 || workspaceCount > 0);
  // Launch button only enabled for sessions
  var launchBtn = document.querySelector('.action-btn.launch');
  launchBtn.disabled = sessionCount === 0;
  launchBtn.title = sessionCount === 0 ? 'Select sessions to launch (use launcher tiles for workspaces)' : '';
  updateLauncherBadges();
}
```

3. **`index.html` — `launchNew(btn, provider)`** — New function for per-provider card launch:

```javascript
function launchNew(btn, provider) {
  var card = btn.closest('.workspace-card');
  fetch('/api/launch', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({workspace: card.dataset.cwd, provider: provider})
  }).then(r => r.text()).then(showToast);
}
```

4. **`index.html` — `pinWorkspace()`** — Drop provider param:

```javascript
function pinWorkspace(btn) {
  var card = btn.closest('.workspace-card');
  var pinned = btn.classList.contains('pinned');
  var url = pinned ? '/api/unpin-folder' : '/api/pin-folder';
  fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({folder: card.dataset.cwd})
  }).then(function() { refreshCards(true); ... });
}
```

5. **Action bar text update** — Show "sessions" vs "workspaces" count separately:

```html
<span class="selection-info">
  <strong id="selCount">0</strong> selected
  (<span id="selSessionCount">0</span> sessions, <span id="selWorkspaceCount">0</span> workspaces)
</span>
```

**Exit criteria**:
- [x] "Launch selected" only dispatches session rows (not workspace cards)
- [x] "Launch selected" button disabled when only workspace cards selected
- [x] Workspace selection still updates launcher tile badges
- [x] Per-provider `launchNew()` on card hover buttons works
- [x] `pinWorkspace()` sends only folder path (no provider)
- [x] Selection info shows session/workspace breakdown

Implementation (2026-07-06, code: c4acfda)
Updated `launchSelected()` to only collect `.session-row.selected` entries (workspace cards excluded from batch payload). `updateActionBar()` now disables the launch button with a tooltip when `sessionCount===0`. Added `launchNew(btn, provider)` for per-provider launch from card hover buttons. `pinWorkspace()` already sends only folder path (from Phase 3). Workspace selection continues to update launcher tile badges via `updateLauncherBadges()`.

### Phase 5: Test updates & cleanup [QA]

**Goal**: Update existing tests for all behavior changes and remove prototype file.

**File scope**: `tests/test_web.py`, `tests/test_config.py`, `tests/test_data.py`, `_proto/border-gradient-prototype.html`

**Changes**:

1. **`test_config.py`**:
   - Test `list[dict]` → `list[str]` migration (new direction)
   - Test deduplication during migration
   - Test save/load round-trip with `list[str]` format
   - Remove tests that assert `{"folder", "provider"}` structure for new config

2. **`test_web.py`**:
   - Update `/api/pin-folder` tests — remove `provider` from request body
   - Update `/api/unpin-folder` tests — same
   - Add test for `/partials/sessions?provider=all` — verify interleaved results
   - Update `partials_workspaces` tests to verify grouped output (one card per workspace)
   - Test provider filter param on pinned endpoints

3. **`test_data.py`**:
   - No changes needed — data layer API is preserved unchanged

4. **Remove `_proto/border-gradient-prototype.html`** (cleanup after verification).

**Exit criteria**:
- [x] All existing tests pass
- [x] Config migration test covers `list[dict]` → `list[str]` with dedup
- [x] Pin API tests updated (no provider param)
- [x] Sessions merge endpoint tested
- [x] Prototype file removed
- [x] `pytest` runs clean

Implementation (2026-07-06, code: b280736)
Verified existing test coverage for: config migration (list[dict]→list[str] with dedup), pin API (no provider param), sessions merge (provider=all), available-providers endpoint, grouped workspace output. Added `test_partials_pinned_workspaces_provider_filter` verifying provider param filtering on pinned workspaces. Deleted `_proto/border-gradient-prototype.html`. 233 passed, 1 skipped.

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| `border-image` kills `border-radius` | Visual regression on card corners | Use `::before` pseudo-element instead (prototype-validated) |
| Config migration loses provider association | Users who pinned same folder per-provider get one pin | Intentional — matches new semantics. Dedup ensures no data loss |
| Three parallel fetches on tab switch | Brief panel inconsistency | Acceptable — independent renders, same as existing refresh |
| Expanded card state across tab switch | Card collapses unexpectedly | Preserve expanded state by `cwd` key; re-fetch sessions with filter |
| Session sort performance (interleaved) | Slow render for high-session workspaces | Negligible — sorting <200 cached items, sub-ms |
| htmx `process()` needed after innerHTML swaps | New tabs unresponsive | Already a known pattern (project MEMORY.md) — always call `htmx.process()` |

## 7) Verification

**Automated**:
```bash
pytest tests/test_config.py tests/test_web.py tests/test_data.py -v
```

**Manual** (via `/qbrowser-test` against running instance):
- Verify multi-provider workspace shows gradient border
- Verify single-provider workspace shows solid border
- Verify hover shows per-provider launch buttons
- Verify provider tab inline with search filters all panels
- Verify expanded card shows interleaved sessions with provider colors
- Verify "Launch selected" disabled when only workspaces selected
- Verify pin/unpin works without provider
- Verify search returns grouped results

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Update Features section: mention unified provider cards, provider filter | 3 |

## 9) Implementation Divergences from Plan

<Reserved — filled during implementation>

## Review Log

### 2026-07-06 -- Plan Review (via /qplan, High effort)

4 personas dispatched (Architect, Senior engineer, End-user advocate, Maintainability reviewer). Sub-agents interrupted mid-execution due to network instability; Architect findings recovered from session file, remaining 3 personas' lenses applied in-context. 5 findings (1 High, 3 Medium, 1 Low). 5 auto-resolved.

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | High | `warmup_all`/`warmup_pinned` callers in `__main__.py` not in Phase 1 file scope; type mismatch pre-exists. | Added `__main__.py` to Phase 1 scope; added exit criterion for type annotation fix. |
| 2 | Medium | `_group_workspaces` calls `load_config()` inside loop — redundant I/O, couples helper to config. | Fixed: helper now accepts `config` parameter passed by caller. |
| 3 | Medium | Phase 3 exit criteria miss README.md update despite Documentation Updates table assigning it there. | Added README.md exit criterion to Phase 3. |
| 4 | Medium | No accessibility consideration for provider filter tabs (keyboard nav, aria-attributes). | Noted in Phase 3 changes — filter buttons need `role="tab"`, `aria-selected`, keyboard handling. |
| 5 | Low | `pinned_sessions` search fallback in `search()` only scans kiro-cli metadata — pre-existing limitation. | Noted as pre-existing; out of scope for this plan (search scope is unchanged per Q13). |

**Plan health: Green** — all High/Medium findings auto-resolved.

### 2026-07-06 -- Implementation Review (after Phase 1, persona: Senior engineer)

Implementation health: Green.
1 finding (0 High, 0 Medium, 1 Low).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Low | 6 residual `isinstance(pf, dict)` guards in web.py are dead code after config migration. | Fixed — removed all 6 guards in review auto-fix cycle. |

Cycle 2 skipped — cycle 1 findings all Low + auto-fixes purely mechanical.

### 2026-07-06 -- Implementation Review (after Phase 2, persona: End-user advocate)

Implementation health: Green.
3 findings (0 High, 1 Medium, 2 Low).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | Card header not keyboard-accessible — no tabindex, role, or keydown handler. | Fixed — added tabindex="0", role="button", onkeydown for Enter/Space. |
| 2 | Low | Provider icon alt text uses raw slug rather than human-readable display name. | Fixed — added display field to provider dicts, used in alt/title attributes. |
| 3 | Low | Provider-dot (8px) may become invisible on hover when button background matches. | Fixed — title tooltip provides identification fallback. |

Cycle 2 skipped — Medium fix was a standard a11y pattern (not a design choice), Low fixes mechanical. 126/126 tests pass.

### 2026-07-06 -- Post-Implementation Review

Overall implementation health: Green.
Personas: Senior engineer, End-user advocate.
11 findings (3 Medium, 8 Low). 4 auto-fixed, 7 accepted as Low-priority follow-ups.
QA verification: PASS (3 surfaces verified: merged cards, provider filter, session interleaving).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | loadTail() reads removed card.dataset.provider, fails for Claude Code sessions. | Fixed — reads row.dataset.provider instead (c29c763). |
| 2 | Medium | aria-expanded never updated by toggleCard() — screen readers always hear "collapsed". | Fixed — added setAttribute in toggleCard (c29c763). |
| 3 | Medium | Session rows not keyboard-accessible (no tabindex/onkeydown). | User: accepted — pre-existing gap, out of scope for this plan. |
| 4 | Low | launchFresh() is dead code after Phase 2 replaced it with launchNew(). | Fixed — removed (c29c763). |
| 5 | Low | Disabled launch button has no visual differentiation. | Fixed — added opacity:0.5 + cursor:not-allowed CSS (c29c763). |
| 6 | Low | search() doesn't filter by active provider tab (shows all regardless). | User: accepted — search scope unchanged per plan Q13 decision. |
| 7 | Low | No direct unit test for _group_workspaces() merge behavior. | User: accepted — merge exercised implicitly via integration tests. |
| 8 | Low | Gradient hard-codes 2 providers (not future-proof). | User: accepted — 2-provider assumption explicit in non-goals. |
| 9 | Low | No prefers-reduced-motion media query. | User: accepted — pre-existing gap, not plan-scoped. |
| 10 | Low | Provider-dot may be insufficient for colorblind users. | User: accepted — title tooltip provides fallback identification. |
| 11 | Low | README feature documentation could be more explicit. | User: accepted — adequate for current scope. |
