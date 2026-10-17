# Authors: see git history
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from math import isfinite
from queue import Empty, Queue

import wx
from lxml import etree

from ..i18n import _
from ..svg.tags import INKSCAPE_LABEL


# wx must outlive the export dialog and its pending native events.
_app = None


class ShapeProgress:
    """Cheap export estimates; no stitch generation or Shapely geometry needed."""

    def __init__(self, elements):
        self.nodes = [element.node for element in elements]
        operations_per_shape = Counter(self.nodes)
        self.total_shapes = len(operations_per_shape)
        last_operations = set({node: index + 1 for index, node in enumerate(self.nodes)}.values())
        areas = {}
        for node in operations_per_shape:
            try:
                # shape_box skips clipping and True includes all ancestor transforms.
                box = node.shape_box(transform=True)
                area = box.width * box.height if box is not None else 0
                areas[node] = area if isfinite(area) and area > 0 else 0
            except (AttributeError, TypeError, ValueError, ArithmeticError, NotImplementedError):
                # Estimation must not prevent exporting an otherwise valid object.
                areas[node] = 0

        # Divide a shape's area between its operations so fill + stroke doesn't
        # count the bounding box twice. This is area progress, not a time estimate.
        self.completed_areas = [0.0]
        self.completed_shapes = [0]
        for index, node in enumerate(self.nodes, 1):
            weight = areas[node] / operations_per_shape[node]
            self.completed_areas.append(self.completed_areas[-1] + weight)
            self.completed_shapes.append(self.completed_shapes[-1] + (index in last_operations))

    def update(self, element, completed):
        completed_shapes = self.completed_shapes[completed]
        label = element.node.get(INKSCAPE_LABEL) or element.node.get('id') or _('Unnamed object')
        svg_type = etree.QName(element.node).localname
        operation = getattr(element, 'element_name', element.__class__.__name__)
        message = _('Converting "%(name)s" (%(type)s) — %(operation)s') % {
            'name': label, 'type': svg_type, 'operation': operation,
        }
        message += '\n' + _('%(done)d of %(total)d shapes complete · %(remaining)d remaining') % {
            'done': completed_shapes, 'total': self.total_shapes,
            'remaining': self.total_shapes - completed_shapes,
        }
        message += '\n' + _('%(done)d of %(total)d operations complete') % {
            'done': completed, 'total': len(self.nodes),
        }
        total_area = self.completed_areas[-1]
        if total_area:
            fraction = self.completed_areas[completed] / total_area
            message += '\n' + _('Approximate area progress: %(percent)d%% (bounding boxes)') % {
                'percent': int(100 * fraction),
            }
        else:
            fraction = completed / len(self.nodes) if self.nodes else 1
            message += '\n' + _('Area estimate unavailable; showing operation progress.')
        return int(1000 * fraction), message


def _wait_for_conversion(future, updates, dialog):
    update = None
    while not future.done():
        try:
            update = updates.get(timeout=0.1)
            # Cached shapes can finish quickly; display the newest state.
            while not updates.empty():
                update = updates.get_nowait()
        except Empty:
            pass
        if update is None:
            dialog.Pulse()
        else:
            value, message = update
            # Only successful completion may close the dialog.
            dialog.Update(min(value, 999), message)
    result = future.result()
    dialog.Update(1000, _('Stitch generation complete.'))
    return result


def stitch_groups_with_progress(elements, convert):
    """Show export-only progress while preserving the converter's result/errors.

    All wx calls stay on the main thread. Only the worker accesses SVG geometry;
    the queue carries display text and numbers. Headless exports remain usable.
    """
    if not elements or (sys.platform.startswith('linux') and not wx.App.IsDisplayAvailable()):
        return convert(elements)

    global _app
    if wx.GetApp() is None:
        # A local reference would destroy wx.App on return, before pending
        # native dialog cleanup has finished. Retain it for the process lifetime.
        _app = wx.App()
    dialog = wx.ProgressDialog(
        _('Generating embroidery stitches'),
        _('Preparing shapes…') + '\n\n\n',
        maximum=1000,
        style=wx.PD_APP_MODAL | wx.PD_ELAPSED_TIME | wx.PD_AUTO_HIDE,
    )
    updates = Queue()

    def generate():
        progress = ShapeProgress(elements)

        def report(element, completed):
            updates.put(progress.update(element, completed))

        return convert(elements, progress_callback=report)

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(generate)
            return _wait_for_conversion(future, updates, dialog)
    finally:
        dialog.Destroy()
