"""Keyboard-focus measurements for ui_screens; never used by the application.

Contrast is measured from two rendered images of the same focused control, with
and without its outline. This includes real surfaces, filters and group opacity
instead of assuming the nearest background-color is the surface behind a ring.
The number is the minimum retained best-in-3x3 sample at 12 outline positions.
It can miss localized low contrast within or between those windows: it is not
the whole-perimeter minimum and is not a WCAG conformance certificate.
"""
from __future__ import annotations

import io
import math
from pathlib import Path
import re


MEASUREMENT_METHOD = (
    'Minimum retained contrast from 12 outline sample positions, each using its '
    'best pixel in a 3x3 window; sparse samples, not a whole-perimeter minimum '
    'and not a WCAG certificate.'
)


META_JS = r"""() => {
    let el = document.activeElement;
    while (el && el.shadowRoot && el.shadowRoot.activeElement) el = el.shadowRoot.activeElement;
    if (!el || el === document.body || el === document.documentElement) return null;
    window.__focusAuditIds ||= new WeakMap();
    window.__focusAuditNext ||= 1;
    if (!window.__focusAuditIds.has(el)) window.__focusAuditIds.set(el, window.__focusAuditNext++);
    let s = getComputedStyle(el), r = el.getBoundingClientRect();
    const controlRect = {x:r.x,y:r.y,width:r.width,height:r.height};
    let indicator = 'element', indicatorError = null;
    let opacity = 1;
    for (let p = el; p; p = p.parentElement) opacity *= Number(getComputedStyle(p).opacity);
    const after = getComputedStyle(el, '::after');
    if (after.content !== 'none' && after.content !== 'normal' &&
        !['none', 'hidden'].includes(after.outlineStyle) && parseFloat(after.outlineWidth) > 0) {
        indicator = '::after';
        s = after;
        opacity *= Number(s.opacity);
        try {
            const px = value => {
                if (value !== '0' && !/^-?[\d.]+px$/.test(value)) throw new Error('unresolved geometry: ' + value);
                const n = parseFloat(value);
                if (!Number.isFinite(n)) throw new Error('non-finite geometry');
                return n;
            };
            if (s.position !== 'absolute') throw new Error('only absolute pseudo indicators are measured');
            if (s.transform !== 'none' || s.translate !== 'none' || s.scale !== 'none' || s.rotate !== 'none')
                throw new Error('transformed pseudo indicator');
            // An absolute ::after is a generated child of its originating
            // element. Find its actual positioned containing block, including
            // the origin itself. Unsupported transform/filter/contain blocks
            // fail explicitly rather than borrowing the origin's rectangle.
            let cb = el;
            for (; cb; cb = cb.parentElement) {
                const cs = getComputedStyle(cb);
                if (cs.transform !== 'none' || cs.filter !== 'none' || cs.perspective !== 'none' ||
                    cs.translate !== 'none' || cs.scale !== 'none' || cs.rotate !== 'none' ||
                    /(layout|paint|strict|content)/.test(cs.contain) || cs.willChange !== 'auto')
                    throw new Error('unsupported containing-block geometry');
                if (cs.position !== 'static') break;
            }
            if (!cb) throw new Error('no positioned containing block');
            for (let p = cb.parentElement; p; p = p.parentElement) {
                const cs = getComputedStyle(p);
                if (cs.transform !== 'none' || cs.translate !== 'none' || cs.scale !== 'none' || cs.rotate !== 'none')
                    throw new Error('transformed containing-block ancestor');
            }
            const box = cb.getBoundingClientRect();
            let width = px(s.width), height = px(s.height);
            if (s.boxSizing !== 'border-box') {
                width += px(s.paddingLeft) + px(s.paddingRight) + px(s.borderLeftWidth) + px(s.borderRightWidth);
                height += px(s.paddingTop) + px(s.paddingBottom) + px(s.borderTopWidth) + px(s.borderBottomWidth);
            }
            const x = s.left !== 'auto' ? px(s.left) + px(s.marginLeft) :
                cb.clientWidth - px(s.right) - px(s.marginRight) - width;
            const y = s.top !== 'auto' ? px(s.top) + px(s.marginTop) :
                cb.clientHeight - px(s.bottom) - px(s.marginBottom) - height;
            r = {x:box.x + cb.clientLeft - cb.scrollLeft + x,
                 y:box.y + cb.clientTop - cb.scrollTop + y,width,height};
        } catch (error) {
            indicatorError = String(error.message);
            r = null;
        }
    }
    const ctx = document.createElement('canvas').getContext('2d');
    ctx.fillStyle = s.outlineColor; ctx.fillRect(0, 0, 1, 1);
    const outlineRgba = Array.from(ctx.getImageData(0, 0, 1, 1).data);
    const radius = value => {
        const parts = value.split(/\s+/), px = (part, size) =>
            parseFloat(part) * (part.endsWith('%') ? size / 100 : 1);
        return [px(parts[0], (r || controlRect).width), px(parts[1] || parts[0], (r || controlRect).height)];
    };
    const radii = [s.borderTopLeftRadius, s.borderTopRightRadius,
        s.borderBottomRightRadius, s.borderBottomLeftRadius].map(radius);
    const size = r || controlRect;
    const fit = (extent, total) => total > 0 ? extent / total : 1;
    const scale = Math.min(1, fit(size.width, radii[0][0] + radii[1][0]),
        fit(size.width, radii[3][0] + radii[2][0]), fit(size.height, radii[0][1] + radii[3][1]),
        fit(size.height, radii[1][1] + radii[2][1]));
    return {key:window.__focusAuditIds.get(el), id:el.id, tag:el.tagName.toLowerCase(),
        type:el.getAttribute('type') || '', role:el.getAttribute('role') || '',
        classes:el.getAttribute('class') || '', label:el.getAttribute('aria-label') ||
            el.getAttribute('title') || (el.textContent || '').trim().slice(0, 100),
        outlineStyle:s.outlineStyle, outlineWidth:parseFloat(s.outlineWidth),
        outlineColor:s.outlineColor, outlineAlpha:outlineRgba[3] / 255,outlineRgb:outlineRgba.slice(0, 3),
        outlineOffset:parseFloat(s.outlineOffset), effectiveOpacity:opacity,
        indicator,indicatorError,controlRect,
        radii:radii.map(pair => pair.map(value => value * scale)),
        rect:r ? {x:r.x,y:r.y,width:r.width,height:r.height} : null,
        viewport:{width:innerWidth,height:innerHeight}};
}"""

ACTIVE_JS = """() => {
    let el = document.activeElement;
    while (el && el.shadowRoot && el.shadowRoot.activeElement) el = el.shadowRoot.activeElement;
    return el;
}"""


def contrast_ratio(a, b):
    def luminance(rgb):
        linear = [v / 3294.6 if v <= 10.31475 else ((v / 255 + .055) / 1.055) ** 2.4 for v in rgb[:3]]
        return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))
    first, second = sorted((luminance(a), luminance(b)))
    return (second + .05) / (first + .05)


def problems(row):
    bad = []
    if row.get('indicatorError'):
        bad.append('pseudo indicator geometry: ' + row['indicatorError'])
    if row.get('measurementError'):
        bad.append(row['measurementError'])
    if row['outlineStyle'] in ('none', 'hidden') or row['outlineWidth'] <= 0:
        bad.append('no outline')
    if row['outlineAlpha'] == 0:
        bad.append('transparent outline')
    if row['effectiveOpacity'] <= 0:
        bad.append('control is invisible through opacity')
    ratio = row.get('contrast')
    if ratio is None:
        bad.append('ring could not be measured')
    elif ratio < 3:
        bad.append(f'sampled painted ring contrast {ratio:.2f}:1 is below 3:1')
    return bad


def _clip(row):
    r, viewport = row['rect'], row['viewport']
    if not r:
        return None
    pad = max(0, row['outlineOffset']) + row['outlineWidth'] + 3
    left, top = max(0, math.floor(r['x'] - pad)), max(0, math.floor(r['y'] - pad))
    right = min(viewport['width'], math.ceil(r['x'] + r['width'] + pad))
    bottom = min(viewport['height'], math.ceil(r['y'] + r['height'] + pad))
    if right <= left or bottom <= top or r['width'] <= 0 or r['height'] <= 0:
        return None
    return dict(x=left, y=top, width=right-left, height=bottom-top)


def _painted_contrast(normal, behind, row, clip):
    """Sample the middle of each straight outline edge, avoiding rounded corners.

    A small perpendicular window finds the stroke core despite fractional device
    coordinates. A low-change sample can be near-invisible paint, not necessarily
    clipping: without geometry proving clipping it remains an uncertainty. An
    entirely absent ring fails.
    """
    from PIL import Image
    a, b = (Image.open(io.BytesIO(data)).convert('RGB') for data in (normal, behind))
    r, distance = row['rect'], row['outlineOffset'] + row['outlineWidth'] / 2
    radii = row['radii']
    ratios, missing, same_colour, uncertain = [], 0, 0, 0
    declared = tuple(row['outlineRgb'])
    for side in ('top', 'bottom', 'left', 'right'):
        for fraction in (.25, .5, .75):
            # Follow the rounded border's parallel outline curve. Sampling the
            # rectangular bounds of a small circle hits only antialias pixels
            # (or no paint) even though its actual stroke has strong contrast.
            if side in ('top', 'bottom'):
                x = r['width'] * fraction
                left, right = (0, 1) if side == 'top' else (3, 2)
                corner = left if x < radii[left][0] else right if x > r['width'] - radii[right][0] else None
                y = -distance if side == 'top' else r['height'] + distance
                if corner is not None:
                    rx, ry = radii[corner]
                    cx = rx if corner == left else r['width'] - rx
                    cy = ry if side == 'top' else r['height'] - ry
                    curve = max(0, ry + distance) * math.sqrt(max(0, 1 - ((x - cx) / max(.001, rx + distance)) ** 2))
                    y = cy - curve if side == 'top' else cy + curve
            else:
                y = r['height'] * fraction
                top, bottom = (0, 3) if side == 'left' else (1, 2)
                corner = top if y < radii[top][1] else bottom if y > r['height'] - radii[bottom][1] else None
                x = -distance if side == 'left' else r['width'] + distance
                if corner is not None:
                    rx, ry = radii[corner]
                    cx = rx if side == 'left' else r['width'] - rx
                    cy = ry if corner == top else r['height'] - ry
                    curve = max(0, rx + distance) * math.sqrt(max(0, 1 - ((y - cy) / max(.001, ry + distance)) ** 2))
                    x = cx - curve if side == 'left' else cx + curve
            x, y = math.floor(r['x'] + x - clip['x']), math.floor(r['y'] + y - clip['y'])
            samples, points = [], []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    p = x + dx, y + dy
                    if 0 <= p[0] < a.width and 0 <= p[1] < a.height:
                        points.append(p)
                        samples.append(contrast_ratio(a.getpixel(p), b.getpixel(p)))
            ratio = max(samples, default=1)
            if ratio > 1.05:
                ratios.append(ratio)
            else:
                missing += 1
                # Unchanged is not necessarily clipped. If both frames already
                # have the declared ring RGB here, the ring and surface are the
                # same colour: this is known 1:1 contrast, including at partial
                # opacity. Other nearly unchanged pixels do not prove clipping.
                if any(a.getpixel(p) == b.getpixel(p) == declared for p in points):
                    ratios.append(1)
                    same_colour += 1
                elif points:
                    # These coordinates lie inside the captured viewport. No
                    # measured ancestor clip proves that paint should be absent
                    # here, so an opaque near-match is just as uncertain as group
                    # compositing. Only an empty window is geometrically clipped
                    # outside the image; never discard in-image low contrast.
                    uncertain += 1
    return min(ratios, default=1), missing, same_colour, uncertain


def measure(page, row):
    """Measure 12 best-of-3x3 windows; no whole-ring minimum is certified.

    The smallest retained window maximum is reported. Localized low contrast
    can be missed even inside a window; finite paint/geometry uncertainty is a
    separate failure rather than evidence that the entire perimeter passes.
    """
    clip = _clip(row)
    if not clip:
        row['contrast'], row['missingSamples'] = None, 12
        row['sameColorSamples'], row['uncertainSamples'] = 0, 0
        return None, None
    # A temporary stylesheet changes only the current ring, not an element's
    # attributes, geometry or focus order. Three non-matching IDs give this
    # measurement rule priority over the page's focus !important selectors.
    options = dict(clip=clip, animations='disabled', caret='hide')
    normal = page.screenshot(**options)
    captured = page.evaluate(META_JS)
    probe_keys = ('key', 'indicator', 'rect', 'outlineColor', 'outlineAlpha', 'effectiveOpacity')
    row['capturedOutline'] = {key: captured.get(key) for key in probe_keys} if captured else None
    override = None
    try:
        selector = ':focus:not(#_focus_measure_a):not(#_focus_measure_b):not(#_focus_measure_c)'
        override = page.add_style_tag(content=selector + ', ' + selector + '::after '
            '{ outline-color: transparent !important; transition: none !important; }')
        behind = page.screenshot(**options)
        hidden = page.evaluate(META_JS)
        row['hiddenOutline'] = {key: hidden.get(key) for key in probe_keys} if hidden else None
    finally:
        if override:
            override.evaluate('el => el.remove()')
            override.dispose()
    row['contrast'], row['missingSamples'], row['sameColorSamples'], row['uncertainSamples'] = _painted_contrast(normal, behind, row, clip)
    for phase, probe in (('normal', captured), ('hidden', hidden)):
        if not probe or probe['key'] != row['key'] or probe['indicator'] != row['indicator']:
            row['measurementError'] = f'{phase} screenshot changed the focused indicator'
        elif not probe['rect'] or any(abs(probe['rect'][key] - row['rect'][key]) > .05 for key in row['rect']):
            row['measurementError'] = f'{phase} screenshot changed indicator geometry'
    if hidden and hidden['outlineAlpha'] != 0:
        row['measurementError'] = 'temporary outline override did not suppress the focused indicator'
    if row.get('measurementError'):
        row['contrast'] = None
    elif row['uncertainSamples']:
        row['measurementError'] = (f"outline has {row['uncertainSamples']} unchanged samples "
                                   'whose paint cannot be distinguished from clipping')
        if row['contrast'] >= 3:
            row['contrast'] = None
    return normal, behind


def audit_page(page, out: Path, label: str, max_stops=2000):
    """Follow the browser's real Tab cycle; a truncated cycle is a failed audit."""
    out.mkdir(parents=True, exist_ok=True)
    rows, failures, seen, saved = [], [], set(), set()
    empty = 0
    for _ in range(max_stops):
        page.keyboard.press('Tab')
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        try:
            # Take metadata only after finite movement/opacity finishes. A
            # screenshot's animation fast-forward must not make its geometry
            # differ from an earlier DOM rectangle. Infinite cues are neither
            # finished nor waited on; the independent motion audit owns them.
            page.wait_for_function('''() => document.getAnimations().every(a => {
                const timing = a.effect && a.effect.getComputedTiming();
                return a.playState !== 'running' || !timing ||
                    timing.iterations === Infinity || !Number.isFinite(timing.endTime);
            })''', timeout=2000)
        except Exception as error:
            failures.append(f'{label}: finite focus transition did not settle: {error}')
            break
        row = page.evaluate(META_JS)
        if row is None:
            empty += 1
            if empty > 1 and not rows:
                failures.append(f'{label}: no keyboard focus stops')
                break
            continue
        empty = 0
        if row['key'] in seen:
            break
        seen.add(row['key'])
        row['state'] = label
        try:
            image, background = measure(page, row)
            bad = problems(row)
            if row['tag'] == 'iframe':
                bad.append('nested browsing context not audited')
        except Exception as error:
            image, background, bad = None, None, [f'measurement failed: {error}']
        row['problems'] = bad
        rows.append(row)
        control_type = (row['tag'], row['type'], row['role'], row['outlineColor'],
                        row['outlineOffset'], row['effectiveOpacity'])
        if image and (control_type not in saved or bad):
            name = re.sub(r'[^A-Za-z0-9_-]+', '_', label + '_' + row['tag'] + '_' + row['type'])
            filename = f'{name}_{len(rows):04}.png'
            (out / filename).write_bytes(image)
            row['screenshot'] = filename
            if bad and background:
                behind_name = filename.removesuffix('.png') + '_behind.png'
                (out / behind_name).write_bytes(background)
                row['backgroundScreenshot'] = behind_name
            saved.add(control_type)
        failures.extend(f"{label} {row['tag']}#{row['id']} ({row['label']}): {problem}" for problem in bad)
    else:
        failures.append(f'{label}: Tab traversal limit {max_stops} reached; coverage is incomplete')
    return rows, failures


def _settled(page):
    page.evaluate('document.fonts.ready.then(() => true)')
    page.wait_for_timeout(100)


def discover_roving(page):
    """Only explicit visible roving tab groups need Arrow-key extra states.

    A role=tablist alone does not imply roving behavior: /party's pc-tabs are
    ordinary native buttons, each already reached by the real Tab walk.
    """
    widgets, diagnostics = [], []
    for selector in ('[role="tablist"]', '.gm-reference-nav'):
        for number in range(page.locator(selector).count()):
            group = page.locator(selector).nth(number)
            members = group.locator('button, [role="tab"]').evaluate_all('''es => es.filter(el => {
                const r = el.getBoundingClientRect();
                if (!r.width || !r.height || el.disabled) return false;
                for (let p = el; p; p = p.parentElement) {
                    const s = getComputedStyle(p);
                    if (s.display === 'none' || ['hidden', 'collapse'].includes(s.visibility) || Number(s.opacity) === 0) return false;
                }
                return true;
            }).map(el => el.getAttribute('tabindex'))''')
            if not members or '-1' not in members:
                continue
            if len(members) < 2 or members.count('0') != 1 or any(value not in ('0', '-1') for value in members):
                diagnostics.append(f'{selector} group {number}: unsupported roving tabindex pattern {members}')
                continue
            widgets.append(dict(selector=selector, number=number, count=len(members), id=group.get_attribute('id') or ''))
    return widgets, diagnostics


def audit_states(page, out: Path, label: str, route: str):
    """View-only states. Never click an action that saves, rolls or applies data."""
    url = page.url
    recipes = []
    if page.locator('#qs-input').count():
        recipes.append(('quick-search', lambda: page.keyboard.press('Control+k'), '#qs-input'))
    if route.startswith('/player/builder'):
        recipes.append(('picker', lambda: page.evaluate("openModal('class')"), '#uni-modal'))
    if route.startswith('/player/levelup/'):
        recipes.append(('picker', lambda: page.evaluate("openModal('class_feat')"), '#uni-modal'))
    tab_ids = [tab.get_attribute('id') for tab in page.locator('.tab-btn[id]').all()
               if tab.is_visible() and tab.is_enabled()]
    recipes += [(name, lambda name=name: page.locator('[id="' + name + '"]').click(), None) for name in tab_ids]
    if route.startswith('/player/sheet/'):
        recipes.extend([
            ('equipment', lambda: page.evaluate('openWeaponBrowser()'), '#equip-modal'),
            ('dice-panel', lambda: page.evaluate('toggleDicePanel()'), '#dice-panel'),
            ('conditions', lambda: page.evaluate('openConditionPicker()'), '[data-cond-row]'),
        ])
    # Roving widgets: traverse with their real arrow handlers, never alter their
    # tabindex. Each selected panel gets its own ordinary Tab audit afterwards.
    widgets, roving_diagnostics = discover_roving(page)
    for widget in widgets:
        group, number, count = widget['selector'], widget['number'], widget['count']
        for index in range(1, count):
            def arrow(group=group, number=number, index=index):
                container = page.locator(group).nth(number)
                selected = container.locator('button[tabindex="0"]:visible, [role="tab"][tabindex="0"]:visible')
                # Reach the entry tab through the browser's actual tab order.
                for _ in range(2000):
                    if selected.evaluate('el => el === document.activeElement', timeout=5000):
                        break
                    page.keyboard.press('Tab')
                else:
                    raise RuntimeError('roving entry tab was not reached within the Tab limit')
                page.evaluate('window.__focusRovingVisited = new WeakSet([document.activeElement])')
                for _ in range(index):
                    previous = selected.element_handle(timeout=5000)
                    try:
                        selected.press('ArrowRight', timeout=5000)
                        if previous.evaluate('el => el === document.activeElement'):
                            raise RuntimeError('ArrowRight did not move focus in the roving widget')
                    finally:
                        previous.dispose()
                    valid = container.evaluate('''el => {
                        const active = document.activeElement;
                        if (!el.contains(active) || active.getAttribute('tabindex') !== '0' || window.__focusRovingVisited.has(active)) return false;
                        window.__focusRovingVisited.add(active);
                        return true;
                    }''')
                    if not valid:
                        raise RuntimeError('unsupported roving ArrowRight result or early focus cycle')
            recipes.append((f'roving-{number}-{index}', arrow, group))
    rows, failures = audit_page(page, out, label + '_load')
    failures.extend(f'{label}: {diagnostic}' for diagnostic in roving_diagnostics)
    for name, opener, target in recipes:
        state = label + '_' + name
        try:
            response = page.goto(url, wait_until='load')
            if not response or response.status >= 400 or page.url != url:
                raise RuntimeError('state reload failed or redirected')
            _settled(page)
            opener()
            if target:
                page.locator(target).first.wait_for(state='visible', timeout=5000)
            _settled(page)
            found, bad = audit_page(page, out, state)
            rows += found
            failures += bad
        except Exception as error:
            failures.append(f'{state}: could not audit required state: {error}')
    return rows, failures
