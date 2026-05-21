"""
geometry.py  —  Shared geometry primitives for the Colorimeter display library.

Rect is the fundamental unit shared between the display driver, touch driver,
and all widgets.  Every widget owns a Rect; every touch hit-test operates on
a Rect.  Import this module wherever geometry is needed; do not duplicate the
class in other modules.
"""


class Rect:
    """
    An axis-aligned rectangle defined by top-left corner and dimensions.

    Parameters
    ----------
    x, y : int   top-left corner in display pixels (origin = top-left of screen)
    w, h : int   width and height in pixels

    All coordinates are integers.  Negative dimensions are not supported.
    """

    __slots__ = ("x", "y", "w", "h")

    def __init__(self, x: int, y: int, w: int, h: int):
        self.x = int(x)
        self.y = int(y)
        self.w = int(w)
        self.h = int(h)

    # ── Derived properties ────────────────────────────────────────────────────

    @property
    def right(self) -> int:
        """X coordinate of the right edge (exclusive)."""
        return self.x + self.w

    @property
    def bottom(self) -> int:
        """Y coordinate of the bottom edge (exclusive)."""
        return self.y + self.h

    @property
    def center(self):
        """(cx, cy) centre point as a tuple of ints."""
        return (self.x + self.w // 2, self.y + self.h // 2)

    @property
    def top_left(self):
        return (self.x, self.y)

    @property
    def top_right(self):
        return (self.right - 1, self.y)

    @property
    def bottom_left(self):
        return (self.x, self.bottom - 1)

    @property
    def bottom_right(self):
        return (self.right - 1, self.bottom - 1)

    @property
    def corners(self):
        """All four corners as a tuple: (TL, TR, BL, BR)."""
        return (self.top_left, self.top_right,
                self.bottom_left, self.bottom_right)

    # ── Geometric tests ───────────────────────────────────────────────────────

    def contains(self, px: int, py: int) -> bool:
        """Return True if pixel (px, py) is inside this rectangle."""
        return self.x <= px < self.right and self.y <= py < self.bottom

    def near_corner(self, px: int, py: int, radius: int = 12):
        """
        Return the name of the nearest corner if (px, py) is within
        radius pixels of it, otherwise return None.

        Return values: 'TL', 'TR', 'BL', 'BR', or None.

        Used by DraggableRect to detect corner-drag gestures.
        """
        candidates = [
            ("TL", self.top_left),
            ("TR", self.top_right),
            ("BL", self.bottom_left),
            ("BR", self.bottom_right),
        ]
        for name, (cx, cy) in candidates:
            if abs(px - cx) <= radius and abs(py - cy) <= radius:
                return name
        return None

    def intersects(self, other: "Rect") -> bool:
        """Return True if this rectangle overlaps with other."""
        return (self.x < other.right  and self.right  > other.x and
                self.y < other.bottom and self.bottom > other.y)

    def clipped_to(self, bounds: "Rect") -> "Rect":
        """
        Return a new Rect clipped to lie within bounds.
        Returns a zero-size Rect if there is no intersection.
        """
        x0 = max(self.x, bounds.x)
        y0 = max(self.y, bounds.y)
        x1 = min(self.right,  bounds.right)
        y1 = min(self.bottom, bounds.bottom)
        if x1 <= x0 or y1 <= y0:
            return Rect(x0, y0, 0, 0)
        return Rect(x0, y0, x1 - x0, y1 - y0)

    # ── Mutation helpers ──────────────────────────────────────────────────────

    def moved_by(self, dx: int, dy: int) -> "Rect":
        """Return a new Rect translated by (dx, dy)."""
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def inflated(self, dw: int, dh: int) -> "Rect":
        """
        Return a new Rect expanded by dw pixels on each side horizontally
        and dh pixels on each side vertically (centre stays the same).
        """
        return Rect(self.x - dw, self.y - dh,
                    self.w + 2 * dw, self.h + 2 * dh)

    def with_corner(self, corner: str, px: int, py: int) -> "Rect":
        """
        Return a new Rect with the named corner moved to (px, py).
        Used by DraggableRect to resize by dragging a corner.
        corner: 'TL', 'TR', 'BL', or 'BR'.
        The minimum dimension is clamped to 10 pixels.
        """
        MIN = 10
        x0, y0, x1, y1 = self.x, self.y, self.right - 1, self.bottom - 1
        if corner == "TL":
            x0, y0 = px, py
        elif corner == "TR":
            x1, y0 = px, py
        elif corner == "BL":
            x0, y1 = px, py
        elif corner == "BR":
            x1, y1 = px, py
        if x1 - x0 < MIN:
            x1 = x0 + MIN
        if y1 - y0 < MIN:
            y1 = y0 + MIN
        return Rect(x0, y0, x1 - x0 + 1, y1 - y0 + 1)

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

    @classmethod
    def from_dict(cls, d: dict) -> "Rect":
        return cls(d["x"], d["y"], d["w"], d["h"])

    # ── Dunder ────────────────────────────────────────────────────────────────

    def __repr__(self):
        return f"Rect(x={self.x}, y={self.y}, w={self.w}, h={self.h})"

    def __eq__(self, other):
        if not isinstance(other, Rect):
            return NotImplemented
        return (self.x == other.x and self.y == other.y and
                self.w == other.w and self.h == other.h)

    def __iter__(self):
        """Allow unpacking: x, y, w, h = rect"""
        return iter((self.x, self.y, self.w, self.h))
