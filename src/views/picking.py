"""Where the region picker draws its picture of the desktop - one rule for
the Tk and the Qt overlay.

The picture is the model's `screen_image`: a screenshot of the virtual
desktop plus the `bounds` it was taken of (`left`, `top`, `width`,
`height`, in capture units). The overlay is a window the toolkit placed
somewhere over that desktop; the pointer reports toolkit ("logical")
desktop coordinates (`x_root`, `globalPosition`).

The picture used to be stretched to the overlay's own size. When the
overlay was not exactly the desktop - a window manager that keeps a
full-screen window inside the work area beside a panel, a second monitor,
a HiDPI ratio - the picture was squashed, and what the operator saw under
the pointer was not what the pointer reported (the bench, 2026-09-28:
"squished the screen so there was mild offset"). Now the picture is drawn
1:1 in desktop coordinates: scaled only by the capture-to-logical ratio
(1 on the bench, 2 on a Retina Mac) and anchored where the desktop's
origin falls in the overlay. Whatever size the overlay ends up, the pixel
under the pointer is the one the pointer names.
"""


def _number(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default        # NaN -> default


def _box(value):
    """`{"left","top","width","height"}` or `(left, top, width, height)` ->
    four floats; anything unreadable is 0."""
    if isinstance(value, dict):
        return tuple(_number(value.get(key)) for key in ("left", "top", "width", "height"))
    try:
        items = list(value or ())
    except TypeError:
        items = []
    items = (items + [0, 0, 0, 0])[:4]
    return tuple(_number(item) for item in items)


def picture_placement(bounds, desktop, overlay):
    """-> `(scale, x, y)`: draw the picture at `bounds` size times `scale`,
    its top-left at `(x, y)` in the overlay's own coordinates.

    `bounds` is what the screenshot was taken of (capture units); `desktop`
    is the logical virtual desktop the toolkit reports (Tk: the virtual
    root; Qt: the united screen geometry); `overlay` is the overlay
    window's actual logical geometry. Each is a `{"left","top","width",
    "height"}` dict or a 4-tuple.

    `ratio = bounds.width / desktop.width`; `scale = 1 / ratio`; the
    anchor is `(bounds.left / ratio - overlay.left, bounds.top / ratio -
    overlay.top)`. A desktop that cannot be measured is taken to be the
    bounds (ratio 1): an unscaled picture at its own origin is the honest
    guess, a stretched one never is.
    """
    b_left, b_top, b_width, _b_height = _box(bounds)
    _d_left, _d_top, d_width, _d_height = _box(desktop)
    o_left, o_top, _o_width, _o_height = _box(overlay)
    ratio = b_width / d_width if b_width > 0 and d_width > 0 else 1.0
    scale = 1.0 / ratio
    return scale, b_left * scale - o_left, b_top * scale - o_top


def drawn_size(bounds, scale):
    """The picture's drawn `(width, height)` in whole logical pixels for a
    `picture_placement` scale. Never empty."""
    _left, _top, width, height = _box(bounds)
    scale = _number(scale, 1.0) or 1.0
    return (max(1, int(round(width * scale))), max(1, int(round(height * scale))))


def placement_line(bounds, desktop, overlay):
    """The one debug line both pickers log on every open."""
    scale, x, y = picture_placement(bounds, desktop, overlay)
    ratio = 1.0 / scale if scale else 0.0
    return (f"bounds={_box(bounds)} desktop={_box(desktop)} "
            f"overlay={_box(overlay)} ratio={ratio:g} "
            f"drawn={drawn_size(bounds, scale)} at ({x:g}, {y:g})")
