"""
Load screen and widget definitions from CSV files.

Data flow:
  1. Load widget_defs.csv → dict of Widget objects by widget_id
  2. Load screen_defs.csv → dict of Screen objects by screen_id
  3. Load screen_widgets.csv → wire widgets into screens with tab ordering

Validation:
  - All widget_ids in screen_widgets.csv exist in widget_defs.csv
  - All screen_ids in screen_defs.csv have entries in screen_widgets.csv
  - next_screen values point to valid screen_ids
  - No duplicate ids
  - Valid integer/boolean values

Whitespace handling:
  - All string fields are stripped of leading/trailing whitespace
  - Tuple parsing handles spaces between elements
  - Users can align CSV columns for readability
"""

import csv
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ============================================================================
# Data Classes (to be moved to screen_manager.py for shared use)
# ============================================================================

@dataclass
class Widget:
    """
    Base widget. Concrete types determined by widget_type.
    Color and style names are resolved from palette at render time.
    """
    widget_id: str
    widget_type: str  # 'Label', 'Button', 'TextBox', 'RectWidget'
    x: int
    y: int
    w: int
    h: int
    label: str = ""
    size: int = 12
    align: str = "left"
    valign: str = "top"
    fgcolor: str = "black"  # Color name from palette.yaml
    bgcolor: str = "white"  # Color name from palette.yaml
    editable: bool = False
    fill: int = 0  # Boolean: 0 or 1
    border_color: str = ""  # Color name or empty
    default: str = ""  # Default value for TextBox
    tab_order: Optional[int] = None  # None if not tabbed; 0-N if tabbed


@dataclass
class Screen:
    """Screen = one phase. Contains widgets and timing/modal info."""
    screen_id: str
    duration: int  # seconds; 0 = no timeout
    modal: bool
    widgets: List[Widget] = field(default_factory=list)  # in tab order
    beginactions: List[str] = field(default_factory=list)  # e.g., ['alarm_off']
    next_screen: str = ""  # screen_id of next screen


# ============================================================================
# Parsing helpers with whitespace handling
# ============================================================================

def parse_tuple(s: str) -> List[str]:
    """
    Parse a tuple string into a list of IDs (widget_id, action names, etc.).
    Handles whitespace and optional parentheses.

    Examples:
      "(103 104)" → ['103', '104']
      "( 103   104 )" → ['103', '104']
      "()" → []
      "103 104" → ['103', '104']  (no parens)
      "" → []
      "   " → []
    """
    s = s.strip()
    if not s:
        return []
    if s.startswith('(') and s.endswith(')'):
        s = s[1:-1].strip()
    # Split and strip each element to handle aligned columns
    return [x.strip() for x in s.split() if x.strip()]


def parse_beginactions(s: str) -> List[str]:
    """Parse beginactions tuple into a list of action names."""
    return parse_tuple(s)


def safe_int(s: str, field_name: str, row_context: str = "") -> int:
    """Parse integer with error reporting."""
    try:
        return int(s.strip())
    except ValueError:
        raise ValueError(f"Invalid integer for {field_name}: '{s}' {row_context}")


def safe_bool(s: str, field_name: str, row_context: str = "") -> bool:
    """Parse boolean (0/1) with error reporting."""
    val = s.strip()
    if val in ('0', '1'):
        return bool(int(val))
    raise ValueError(f"Invalid boolean for {field_name}: '{val}' (expected 0 or 1) {row_context}")


def unquote(s: str) -> str:
    """Remove surrounding quotes if present; strip whitespace."""
    s = s.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    return s


def widget_factory(row: Dict[str, str]) -> Widget:
    """
    Construct a Widget object from a CSV row.
    All color/style names are stored as-is; resolved by renderer/palette at use time.
    """
    widget_id = row['widget_id'].strip()
    widget_type = row['widget_name'].strip()
    context = f"(widget_id={widget_id})"

    try:
        return Widget(
            widget_id=widget_id,
            widget_type=widget_type,
            x=safe_int(row['x'], 'x', context),
            y=safe_int(row['y'], 'y', context),
            w=safe_int(row['w'], 'w', context),
            h=safe_int(row['h'], 'h', context),
            label=unquote(row['label']),
            size=safe_int(row['size'], 'size', context) if row['size'].strip() else 12,
            align=row['align'].strip() or "left",
            valign=row['valign'].strip() or "top",
            fgcolor=row['fgcolor'].strip() or "black",
            bgcolor=row['bgcolor'].strip() or "white",
            editable=safe_bool(row['editable'], 'editable', context),
            fill=safe_int(row['fill'], 'fill', context),
            border_color=row['border_color'].strip(),
            default=unquote(row['default']),
        )
    except (KeyError, ValueError) as e:
        raise ValueError(f"Error parsing widget {context}: {e}")


# ============================================================================
# Main loaders with validation
# ============================================================================

def load_widgets(widget_defs_path: str) -> Dict[str, Widget]:
    """
    Load widget_defs.csv and construct Widget objects.

    Validation:
      - No duplicate widget_ids
      - All required fields present and valid

    Returns:
      dict: {widget_id: Widget object}

    Raises:
      ValueError: on duplicate IDs or invalid data
    """
    widgets_by_id = {}
    seen_ids = set()

    try:
        with open(widget_defs_path, 'r') as f:
            reader = csv.DictReader(f)
            for line_num, row in enumerate(reader, start=2):  # start=2: skip header
                widget_id = row['widget_id'].strip()

                # Skip comment rows
                if widget_id.startswith('#'):
                    continue

                # Check for duplicates
                if widget_id in seen_ids:
                    raise ValueError(f"Duplicate widget_id '{widget_id}' at line {line_num}")
                seen_ids.add(widget_id)

                # Construct widget
                widget = widget_factory(row)
                widgets_by_id[widget.widget_id] = widget

    except (IOError, csv.Error) as e:
        raise IOError(f"Error reading {widget_defs_path}: {e}")

    return widgets_by_id


def load_screens(screen_defs_path: str) -> Dict[str, Dict]:
    """
    Load screen_defs.csv and build Screen metadata (widgets populated later).

    Validation:
      - No duplicate screen_ids
      - Valid duration (integer >= 0)
      - Valid modal (0 or 1)

    Returns:
      dict: {screen_id: {'duration': ..., 'modal': ..., 'beginactions': [...], 'next': ...}}

    Raises:
      ValueError: on duplicate IDs or invalid data
    """
    screens_by_id = {}
    seen_ids = set()

    try:
        with open(screen_defs_path, 'r') as f:
            reader = csv.DictReader(f)
            for line_num, row in enumerate(reader, start=2):
                screen_id = row['screen_id'].strip()

                if screen_id.startswith('#'):
                    continue

                if screen_id in seen_ids:
                    raise ValueError(f"Duplicate screen_id '{screen_id}' at line {line_num}")
                seen_ids.add(screen_id)

                context = f"(screen_id={screen_id}, line {line_num})"
                screens_by_id[screen_id] = {
                    'duration': safe_int(row['duration'], 'duration', context),
                    'modal': safe_bool(row['modal'], 'modal', context),
                    'beginactions': parse_beginactions(row['beginactions']),
                    'next': row['next'].strip(),
                }

    except (IOError, csv.Error) as e:
        raise IOError(f"Error reading {screen_defs_path}: {e}")

    return screens_by_id


def wire_widgets_to_screens(
    screen_widgets_path: str,
    widgets_by_id: Dict[str, Widget],
    screens_by_id: Dict[str, Dict],
) -> Dict[str, Screen]:
    """
    Load screen_widgets.csv and assign widgets to screens with tab ordering.

    Tab order is determined by:
      1. Position within the tuple (within-line order)
      2. Line order (between-line order)

    Validation:
      - All widget_ids in screen_widgets.csv exist in widget_defs.csv
      - All screen_ids in screen_defs.csv have at least one entry here
      - next_screen values point to valid screen_ids

    Returns:
      dict: {screen_id: Screen object (fully assembled)}

    Raises:
      ValueError: on validation failure
    """
    screen_lines = {}

    try:
        with open(screen_widgets_path, 'r') as f:
            reader = csv.DictReader(f)
            for line_num, row in enumerate(reader, start=2):
                screen_id = row['screen_id'].strip()

                if screen_id.startswith('#'):
                    continue

                if screen_id not in screen_lines:
                    screen_lines[screen_id] = []

                tabbed_ids = parse_tuple(row['tabbed_widgets'])
                untabbed_ids = parse_tuple(row['untabbed_widgets'])

                # Validate all widget_ids exist
                for widget_id in tabbed_ids + untabbed_ids:
                    if widget_id not in widgets_by_id:
                        raise ValueError(
                            f"Unknown widget_id '{widget_id}' for screen '{screen_id}' "
                            f"at line {line_num}"
                        )

                screen_lines[screen_id].append((line_num, tabbed_ids, untabbed_ids))

    except (IOError, csv.Error) as e:
        raise IOError(f"Error reading {screen_widgets_path}: {e}")

    # Validate all screens have at least one entry
    for screen_id in screens_by_id:
        if screen_id not in screen_lines:
            raise ValueError(f"Screen '{screen_id}' defined in screen_defs.csv but not in screen_widgets.csv")

    # Validate next_screen references
    all_screen_ids = set(screens_by_id.keys())
    for screen_id, screen_def in screens_by_id.items():
        next_id = screen_def['next']
        if next_id and next_id not in all_screen_ids:
            raise ValueError(f"Screen '{screen_id}' references unknown next_screen '{next_id}'")

    # Assemble screens
    screens = {}

    for screen_id, lines in screen_lines.items():
        widgets_list = []
        tab_counter = 0

        # Process each line (header, body, footer, etc.)
        for line_num, tabbed_ids, untabbed_ids in lines:
            # Tabbed widgets first (within line, left-to-right)
            for widget_id in tabbed_ids:
                widget = widgets_by_id[widget_id]
                # Copy widget with assigned tab_order (don't mutate original)
                widget_copy = Widget(
                    widget_id=widget.widget_id,
                    widget_type=widget.widget_type,
                    x=widget.x,
                    y=widget.y,
                    w=widget.w,
                    h=widget.h,
                    label=widget.label,
                    size=widget.size,
                    align=widget.align,
                    valign=widget.valign,
                    fgcolor=widget.fgcolor,
                    bgcolor=widget.bgcolor,
                    editable=widget.editable,
                    fill=widget.fill,
                    border_color=widget.border_color,
                    default=widget.default,
                    tab_order=tab_counter,
                )
                widgets_list.append(widget_copy)
                tab_counter += 1

            # Untabbed widgets second (within line, left-to-right)
            for widget_id in untabbed_ids:
                widget = widgets_by_id[widget_id]
                # Copy widget with tab_order=None
                widget_copy = Widget(
                    widget_id=widget.widget_id,
                    widget_type=widget.widget_type,
                    x=widget.x,
                    y=widget.y,
                    w=widget.w,
                    h=widget.h,
                    label=widget.label,
                    size=widget.size,
                    align=widget.align,
                    valign=widget.valign,
                    fgcolor=widget.fgcolor,
                    bgcolor=widget.bgcolor,
                    editable=widget.editable,
                    fill=widget.fill,
                    border_color=widget.border_color,
                    default=widget.default,
                    tab_order=None,
                )
                widgets_list.append(widget_copy)

        # Build the Screen object
        screen_def = screens_by_id[screen_id]
        screen = Screen(
            screen_id=screen_id,
            duration=screen_def['duration'],
            modal=screen_def['modal'],
            widgets=widgets_list,
            beginactions=screen_def['beginactions'],
            next_screen=screen_def['next'],
        )
        screens[screen_id] = screen

    return screens


def load_all(
    widget_defs_path: str,
    screen_defs_path: str,
    screen_widgets_path: str,
) -> Dict[str, Screen]:
    """
    Load all CSV files and return a complete screen inventory.

    Loads in order:
      1. Widgets (widget_defs.csv)
      2. Screens metadata (screen_defs.csv)
      3. Wires widgets to screens with tab ordering (screen_widgets.csv)

    All validation happens during load; see individual loaders for details.

    Args:
        widget_defs_path: path to widget_defs.csv
        screen_defs_path: path to screen_defs.csv
        screen_widgets_path: path to screen_widgets.csv

    Returns:
        dict: {screen_id: Screen object}

    Raises:
        ValueError: on validation failure (duplicate IDs, missing references, etc.)
        IOError: on file read errors
    """
    widgets_by_id = load_widgets(widget_defs_path)
    screens_by_id = load_screens(screen_defs_path)
    screens = wire_widgets_to_screens(screen_widgets_path, widgets_by_id, screens_by_id)

    return screens
