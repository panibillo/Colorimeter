"""
Mock ili9341 display driver using matplotlib.

Displays widgets with their bounding boxes, IDs, and text values.
"""

import logging
from typing import Optional, Tuple
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.figure import Figure
from matplotlib.backend_bases import KeyEvent, MouseEvent

log = logging.getLogger(__name__)


class ILI9341:
    """
    Mock display driver using matplotlib.

    Displays 320x240 screen with widgets rendered as bounding boxes.
    """

    WIDTH = 320
    HEIGHT = 240

    # Ceiling on retained artists.  _cover() keeps the scene graph naturally
    # bounded, but a draw that nothing ever paints over would still accumulate,
    # so cap it as a backstop: an unbounded scene graph here is not a slow
    # display, it is a wedged process (see _cover).
    MAX_ARTISTS = 4000

    # RGB565 constants (same as real driver)
    WHITE = 0xFFFF
    BLACK = 0x0000
    DKGRAY = 0x8410
    RED = 0xF800
    GREEN = 0x07E0
    BLUE = 0x001F

    def __init__(self):
        self.fig: Optional[Figure] = None
        self.ax = None
        self._initialized = False
        self._key_event_handler = None
        self._mouse_event_handler = None
        self._z = 0          # monotonically increasing z-order: draw order = paint order
        log.info("ILI9341 (mock): initialized")

    def _next_z(self) -> int:
        """
        Next paint layer.  matplotlib is retained-mode and draws patches *below*
        text by default zorder, so a later fill_rect could not erase earlier
        text.  Stamping every artist with an increasing zorder makes call order
        the paint order — a real framebuffer's behaviour — so per-widget redraws
        (clock, timer) correctly cover what they replace.
        """
        self._z += 1
        return self._z

    @staticmethod
    def _area(rect) -> Tuple[float, float, float, float]:
        """A rect as (x0, y0, x1, y1) in screen coordinates."""
        return (rect.x, rect.y, rect.x + rect.w, rect.y + rect.h)

    def _stamp(self, artist, area: Tuple[float, float, float, float]) -> None:
        """
        Record the screen area an artist occupies, so a later opaque fill over
        that area can discard it.  Text is stamped with the rect it was asked
        to fill rather than its rendered extent: that is the widget contract,
        and it is what the hardware clips to.
        """
        artist._cm_area = area

    def _cover(self, area: Tuple[float, float, float, float]) -> None:
        """
        Discard every artist wholly inside `area`.

        The panel has no scene graph: filling a rectangle overwrites those
        pixels and what was under them is gone.  matplotlib is retained-mode,
        so an opaque repaint only *hides* the old artist — it keeps rendering
        it forever.  Without this the 1 Hz clock alone grows the scene graph
        without bound, and every frame re-rasterises every glyph ever drawn;
        the font file is re-read per glyph, so the redraw eventually takes
        longer than the interval that triggers it and the loop never returns.
        """
        x0, y0, x1, y1 = area
        for group in (self.ax.texts, self.ax.patches, self.ax.images):
            for artist in list(group):
                covered = getattr(artist, "_cm_area", None)
                if covered is None:
                    continue
                ax0, ay0, ax1, ay1 = covered
                if ax0 >= x0 and ay0 >= y0 and ax1 <= x1 and ay1 <= y1:
                    artist.remove()

    def _trim(self) -> None:
        """Drop the oldest artists once past MAX_ARTISTS.  zorder is paint
        order, so it doubles as age."""
        artists = [a for group in (self.ax.texts, self.ax.patches, self.ax.images)
                   for a in group]
        excess = len(artists) - self.MAX_ARTISTS
        if excess <= 0:
            return
        artists.sort(key=lambda a: a.get_zorder())
        for artist in artists[:excess]:
            artist.remove()
        log.warning("ILI9341 (mock): artist cap hit, trimmed %d; some draw is "
                    "never being painted over", excess)

    def init(self) -> None:
        """Initialize the display and matplotlib figure."""
        if self._initialized:
            return

        # Disable matplotlib's default key bindings.  They hijack ordinary typing:
        # 'l'/'k'/'L' toggle log axes (which crashes on our flipped y-limits),
        # 'f' toggles fullscreen, 'q' quits the window, 's' saves, 'g' toggles the
        # grid, 'p'/'o' pan/zoom, etc.  This is a text-entry surface, so silence
        # all of them and route keys only through our own handler.
        for key in [k for k in plt.rcParams if k.startswith("keymap.")]:
            plt.rcParams[key] = []

        fig, ax = plt.subplots(figsize=(8, 6), dpi=100)
        self.fig = fig
        self.ax = ax

        # Setup axes
        ax.set_xlim(0, self.WIDTH)
        ax.set_ylim(self.HEIGHT, 0)  # Flip Y so (0,0) is top-left
        ax.set_aspect('equal')
        ax.set_title('Colorimeter Display (Mock)')
        ax.set_xlabel('X')
        ax.set_ylabel('Y')

        # Grid for reference
        ax.grid(True, alpha=0.3)

        # Connect mouse and keyboard events
        fig.canvas.mpl_connect('button_press_event', self._on_mouse_press)
        fig.canvas.mpl_connect('button_release_event', self._on_mouse_release)
        fig.canvas.mpl_connect('key_press_event', self._on_key_event)

        self._initialized = True
        self.fill_screen(self.WHITE)

        # Show the window non-blocking so the main loop can continue
        plt.show(block=False)
        self.fig.canvas.flush_events()  # Force immediate display
        log.info("ILI9341 (mock): display initialized, showing matplotlib window")

    def cleanup(self) -> None:
        """Clean up resources."""
        if self.fig:
            plt.close(self.fig)
            self._initialized = False
            log.info("ILI9341 (mock): cleanup")

    def fill_screen(self, color: int) -> None:
        """Fill entire screen with a color (RGB565). Clears all existing drawings."""
        if not self._initialized:
            return
        rgb = self._rgb565_to_rgb(color)

        # Clear all existing artists (patches, text) from the axes
        # This mirrors the real ili9341 behavior of clearing display memory
        self.ax.clear()
        self._z = 0          # fresh frame: restart the paint-order counter

        # Re-setup the axes
        self.ax.set_xlim(0, self.WIDTH)
        self.ax.set_ylim(self.HEIGHT, 0)  # Flip Y so (0,0) is top-left
        self.ax.set_aspect('equal')
        self.ax.set_facecolor(rgb)
        self.ax.grid(True, alpha=0.3)
        self.ax.set_xlabel('X')
        self.ax.set_ylabel('Y')

        self.fig.canvas.draw_idle()

    def fill_rect(self, rect, color: int, widget_id: str = "") -> None:
        """Fill a rectangle."""
        if not self._initialized:
            return
        rgb = self._rgb565_to_rgb(color)
        area = self._area(rect)
        self._cover(area)          # opaque fill: what was under it is gone
        rect_patch = patches.Rectangle(
            (rect.x, rect.y), rect.w, rect.h,
            linewidth=0, facecolor=rgb, edgecolor='none', zorder=self._next_z()
        )
        self.ax.add_patch(rect_patch)
        self._stamp(rect_patch, area)

        # Draw widget bounds outline and ID if provided
        if widget_id:
            # Gray dashed outline around widget bounds
            outline = patches.Rectangle(
                (rect.x, rect.y), rect.w, rect.h,
                linewidth=1, facecolor='none', edgecolor=(0.5, 0.5, 0.5),
                linestyle='--', zorder=self._next_z()
            )
            self.ax.add_patch(outline)
            self._stamp(outline, area)

            # Widget ID in corner with larger bold font
            label = self.ax.text(rect.x + 1, rect.y + 1, widget_id,  # type: ignore
                        color=(0.5, 0.5, 0.5), fontsize=12, va='top', ha='left',
                        fontweight='bold', zorder=self._next_z())
            self._stamp(label, area)

        self._trim()
        self.fig.canvas.draw_idle()

    def draw_rect(self, rect, color: int, fill: bool = False, thickness: int = 1, widget_id: str = "") -> None:
        """Draw a rectangle outline or filled."""
        if not self._initialized:
            return
        rgb = self._rgb565_to_rgb(color)
        area = self._area(rect)
        if fill:                   # only a filled rect is opaque
            self._cover(area)
        rect_patch = patches.Rectangle(
            (rect.x, rect.y), rect.w, rect.h,
            linewidth=thickness, facecolor='none' if not fill else rgb,
            edgecolor=rgb, zorder=self._next_z()
        )
        self.ax.add_patch(rect_patch)  # type: ignore
        self._stamp(rect_patch, area)

        # Draw widget bounds outline and ID if provided
        if widget_id:
            # Gray dashed outline around widget bounds (drawn behind if widget already has outline)
            outline = patches.Rectangle(
                (rect.x - 0.5, rect.y - 0.5), rect.w + 1, rect.h + 1,
                linewidth=0.5, facecolor='none', edgecolor=(0.5, 0.5, 0.5),
                linestyle=':', zorder=self._next_z()
            )
            self.ax.add_patch(outline)  # type: ignore
            self._stamp(outline, area)

            # Widget ID in corner with larger bold font
            label = self.ax.text(rect.x + 1, rect.y + 1, widget_id,  # type: ignore
                        color=(0.5, 0.5, 0.5), fontsize=12, va='top', ha='left',
                        fontweight='bold', zorder=self._next_z())
            self._stamp(label, area)

        self._trim()
        self.fig.canvas.draw_idle()

    def draw_text(
        self,
        text: str,
        rect,
        color: int = 0xFFFF,
        bg: int = 0x0000,
        size: int = 16,
        align: str = "left",
        valign: str = "top",
        widget_id: str = ""
    ) -> None:
        """Draw text in a rectangle."""
        if not self._initialized or not text:
            return

        text_color = self._rgb565_to_rgb(color)
        
        size *= 2

        # Map alignment to matplotlib ha/va
        ha_map = {"left": "left", "center": "center", "right": "right"}
        va_map = {"top": "top", "middle": "center", "bottom": "bottom"}
        ha = ha_map.get(align, "left")
        va = va_map.get(valign, "top")

        # Compute text position based on alignment
        if ha == "left":
            x = rect.x + 2
        elif ha == "center":
            x = rect.x + rect.w / 2
        else:  # right
            x = rect.x + rect.w - 2

        if va == "top":
            y = rect.y + 2
        elif va == "center":
            y = rect.y + rect.h / 2
        else:  # bottom
            y = rect.y + rect.h - 2

        fontsize = max(6, size // 2)  # Scale down matplotlib fontsize
        area = self._area(rect)
        glyphs = self.ax.text(x, y, text, color=text_color, ha=ha, va=va,  # type: ignore
                     fontsize=fontsize, zorder=self._next_z())
        self._stamp(glyphs, area)

        # Draw widget bounds outline and ID if provided
        if widget_id:
            # Gray dashed outline around widget bounds
            outline = patches.Rectangle(
                (rect.x, rect.y), rect.w, rect.h,
                linewidth=0.5, facecolor='none', edgecolor=(0.5, 0.5, 0.5),
                linestyle=':', zorder=self._next_z()
            )
            self.ax.add_patch(outline)  # type: ignore
            self._stamp(outline, area)

            # Widget ID in top-left corner with larger bold font
            label = self.ax.text(rect.x + 1, rect.y + 1, widget_id,  # type: ignore
                        color=(0.5, 0.5, 0.5), fontsize=size, va='top', ha='left',
                        fontweight='bold', zorder=self._next_z())
            self._stamp(label, area)

        self._trim()
        self.fig.canvas.draw_idle()

    def draw_image(self, image, x: int = 0, y: int = 0) -> None:
        """
        Blit a PIL image at (x, y), matching the real driver's contract.

        Like the hardware, this does no scaling: the image is placed at its
        natural size and clipped to the display. `extent` is given as
        (left, right, bottom, top) with bottom > top, which is the correct
        order for these axes — init() sets ylim (HEIGHT, 0) so y grows
        downward — and it makes the image land in screen pixels regardless of
        its own resolution.

        Implementing this is what lets simulation mode double as the desktop
        image viewer: the same composed canvas renders here and on the panel.
        """
        if image is None or self.ax is None:
            return

        import numpy as np

        # Clip to the display, exactly as the hardware driver does.
        iw, ih = image.size
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(x + iw, self.WIDTH), min(y + ih, self.HEIGHT)
        if x1 <= x0 or y1 <= y0:
            return
        if (x0, y0, x1, y1) != (x, y, x + iw, y + ih):
            image = image.crop((x0 - x, y0 - y, x1 - x, y1 - y))

        area = (x0, y0, x1, y1)
        self._cover(area)          # an image is opaque over its extent
        blit = self.ax.imshow(np.asarray(image.convert("RGB")),
                       extent=(x0, x1, y1, y0),      # left, right, bottom, top
                       interpolation="nearest",
                       zorder=self._next_z())
        self._stamp(blit, area)
        self._trim()
        self.fig.canvas.draw_idle()

    def register_key_handler(self, handler) -> None:
        """Register a callback for keyboard events."""
        self._key_event_handler = handler

    def register_mouse_handler(self, handler) -> None:
        """Register a callback for mouse events."""
        self._mouse_event_handler = handler

    def _on_key_event(self, event: KeyEvent) -> None:
        """Handle matplotlib key events."""
        if not event.key:
            return

        # Map matplotlib key names to our key names
        key_map = {
            'tab': 'tab',
            'shift+tab': 'shift-tab',
            'return': 'enter',
            'backspace': 'backspace',
            'delete': 'delete',
        }

        our_key = key_map.get(event.key, event.key)

        if self._key_event_handler:
            self._key_event_handler(our_key)
            log.debug(f"ILI9341 (mock): key event: {our_key}")

    def _on_mouse_press(self, event: MouseEvent) -> None:
        """Handle mouse button press."""
        if event.xdata is None or event.ydata is None:
            return

        x, y = int(event.xdata), int(event.ydata)

        if self._mouse_event_handler:
            self._mouse_event_handler(x, y)
            log.debug(f"ILI9341 (mock): mouse press at ({x}, {y})")

    def _on_mouse_release(self, event: MouseEvent) -> None:
        """Handle mouse button release."""
        # Notify touch driver of release
        if hasattr(self, '_touch_release_handler') and self._touch_release_handler:
            self._touch_release_handler()
        log.debug("ILI9341 (mock): mouse release")

    def register_touch_release_handler(self, handler) -> None:
        """Register a callback for mouse button release."""
        self._touch_release_handler = handler

    def process_events(self) -> None:
        """Process pending matplotlib events. Call from main loop."""
        if self._initialized and self.fig:
            try:
                self.fig.canvas.flush_events()
            except Exception as e:
                log.debug(f"Error flushing events: {e}")

    @staticmethod
    def _rgb565_to_rgb(color565: int) -> Tuple[float, float, float]:
        """Convert RGB565 (16-bit) to RGB (0.0–1.0 float tuple)."""
        r = ((color565 >> 11) & 0x1F) / 31.0
        g = ((color565 >> 5) & 0x3F) / 63.0
        b = (color565 & 0x1F) / 31.0
        return (r, g, b)
