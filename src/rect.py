"""
rect.py — Axis-aligned rectangle for image ROI representation.

A Rect describes a rectangular region within a camera image using the
upper-left corner (x, y) in pixel coordinates, plus an optional name label
and a matplotlib display colour.

Coordinate convention
---------------------
    x   increases to the right  (column axis)
    y   increases downward      (row axis)
    (0, 0) is the top-left pixel of the image

Numpy slicing
-------------
    xyslice() returns (row_slice, col_slice) = (y-slice, x-slice) so that
    ``image[rect.xyslice()]`` extracts the correct region from a numpy array
    whose axes are ordered [row, col] = [y, x].
"""

DEFAULT_COLOR = '#FFFFFF'   # White


class Rect:
    """
    Axis-aligned rectangle anchored at the upper-left corner (x, y).

    Stores a rectangular image region in pixel coordinates plus an optional
    one-based grid position (row, col) and a matplotlib-compatible colour.

    Attributes:
        x:     Left edge in pixels from image left (int)
        y:     Top edge in pixels from image top (int)
        w:     Width in pixels (int)
        h:     Height in pixels (int)
        name:  Optional label for this rectangle (str)
        color: Matplotlib colour string, e.g. '#FFFFFF' (str)
    """

    def __init__(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        name: str = '',
        color: str = DEFAULT_COLOR,
    ) -> None:
        """
        Create a Rect from upper-left corner coordinates, size, and optional
        name label.

        Float arguments are truncated to int (same as int() conversion).

        Args:
            x:     Left edge in pixels (converted to int)
            y:     Top edge in pixels (converted to int)
            w:     Width in pixels (converted to int)
            h:     Height in pixels (converted to int)
            name:  Optional label for this rectangle (default '')
            color: Matplotlib colour string (default '#FFFFFF', white)

        Example:
            >>> r = Rect(10, 20, 100, 80, name='patch', color='#FF0000')
            >>> r.x2()
            110
        """
        self.x     = int(x)
        self.y     = int(y)
        self.w     = int(w)
        self.h     = int(h)
        self.name  = name
        self.color = color

    # ------------------------------------------------------------------
    # String representation
    # ------------------------------------------------------------------

    def __str__(self) -> str:
        """
        Return a human-readable summary of the rectangle.

        Returns:
            String in the form (name=N, x=X, y=Y, w=W, h=H, color=C)

        Example:
            >>> print(Rect(10, 20, 100, 80, name='patch'))
            (name=patch, x=10, y=20, w=100, h=80, color=#FFFFFF)
        """
        return (
            f"(name={self.name}, "
            f"x={self.x}, y={self.y}, w={self.w}, h={self.h}, "
            f"color={self.color})"
        )

    def __repr__(self) -> str:
        """
        Return an eval-able representation of the rectangle.

        Returns:
            String of the form Rect(x, y, w, h, name='...', color='...')
        """
        return (
            f"Rect({self.x}, {self.y}, {self.w}, {self.h}, "
            f"name={self.name!r}, color={self.color!r})"
        )

    # ------------------------------------------------------------------
    # Derived geometry
    # ------------------------------------------------------------------

    def x2(self) -> int:
        """
        Return the right edge of the rectangle (exclusive).

        Returns:
            x + w
        """
        return self.x + self.w

    def y2(self) -> int:
        """
        Return the bottom edge of the rectangle (exclusive).

        Returns:
            y + h
        """
        return self.y + self.h

    def xywh(self) -> tuple[int, int, int, int]:
        """
        Return the rectangle as (x, y, w, h).

        Useful for layout functions and OpenCV helper calls that accept
        this form.

        Returns:
            Tuple (x, y, w, h)

        Example:
            >>> Rect(10, 20, 100, 80).xywh()
            (10, 20, 100, 80)
        """
        return self.x, self.y, self.w, self.h

    def xyslice(self) -> tuple[slice, slice]:
        """
        Return numpy array slices to extract this region from an image.

        Returns (row_slice, col_slice) in numpy axis order [row, col],
        where row = y-axis and col = x-axis. Use as::

            region = image[rect.xyslice()]

        Returns:
            Tuple (slice(y, y2), slice(x, x2))

        Example:
            >>> r = Rect(10, 20, 100, 80)
            >>> r.xyslice()
            (slice(20, 100, None), slice(10, 110, None))
        """
        return slice(self.y, self.y2()), slice(self.x, self.x2())

    def area(self) -> int:
        """
        Return the area of the rectangle in pixels.

        Returns:
            w * h

        Example:
            >>> Rect(0, 0, 100, 80).area()
            8000
        """
        return self.w * self.h

    def contains(self, px: float, py: float) -> bool:
        """
        Return True if point (px, py) lies within the rectangle.

        The rectangle is half-open: the right and bottom edges are excluded,
        consistent with Python slice semantics.

        Args:
            px: x-coordinate of the point
            py: y-coordinate of the point

        Returns:
            True if x <= px < x2 and y <= py < y2

        Example:
            >>> r = Rect(10, 20, 100, 80)
            >>> r.contains(10, 20)   # top-left corner is included
            True
            >>> r.contains(110, 20)  # right edge is excluded
            False
        """
        return self.x <= px < self.x2() and self.y <= py < self.y2()
