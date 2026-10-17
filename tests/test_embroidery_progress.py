from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from inkex import Group, PathElement, Rectangle
from inkex.tester.svg import svg

from lib.elements import nodes_to_elements
from lib.extensions.base import InkstitchExtension
from lib.gui import embroidery_progress
from lib.gui.embroidery_progress import ShapeProgress, stitch_groups_with_progress
from lib.svg.tags import INKSCAPE_LABEL


def test_shapes_and_operations_have_separate_counts():
    root = svg()
    group = root.add(Group(transform='scale(2)'))
    first = group.add(Rectangle(width='10', height='20', style='fill:red;stroke:blue'))
    first.set(INKSCAPE_LABEL, 'Left leaf')
    second = root.add(Rectangle(width='20', height='40'))
    elements = nodes_to_elements([first, second])
    progress = ShapeProgress(elements)

    # Both shapes have transformed bounding-box area 800; fill and stroke
    # divide the first shape's area rather than counting it twice.
    assert progress.completed_areas == [0, 400, 800, 1600]
    value, message = progress.update(elements[0], 1)
    assert value == 250
    assert 'Left leaf' in message
    assert '(rect)' in message
    assert getattr(elements[0], 'element_name') in message
    assert '0 of 2 shapes complete' in message
    assert '2 remaining' in message
    assert progress.update(elements[1], 2)[0] == 500
    assert '1 of 2 shapes complete' in progress.update(elements[1], 2)[1]
    assert progress.update(elements[2], 3)[0] == 1000


def test_zero_area_uses_operation_progress():
    root = svg()
    line = root.add(PathElement(d='M0,0 L10,0', style='fill:none;stroke:black'))
    elements = nodes_to_elements([line])
    progress = ShapeProgress(elements)
    assert progress.update(elements[0], 0)[0] == 0
    value, message = progress.update(elements[0], 1)
    assert value == 1000
    assert 'Area estimate unavailable' in message


def test_estimation_failure_does_not_prevent_conversion():
    node = Mock()
    node.shape_box.side_effect = ValueError('Invalid geometry')
    progress = ShapeProgress([SimpleNamespace(node=node)])
    assert progress.completed_areas == [0, 0]


def test_callback_preserves_stitch_order_and_neighbours():
    first_group, second_group = object(), object()
    first = SimpleNamespace(embroider=Mock(return_value=[first_group]))
    second = SimpleNamespace(embroider=Mock(return_value=[second_group]))
    report = Mock()

    result = InkstitchExtension().elements_to_stitch_groups([first, second], report)

    assert result == [first_group, second_group]
    first.embroider.assert_called_once_with(None, second)
    second.embroider.assert_called_once_with(first_group, None)
    assert [call.args for call in report.call_args_list] == [(first, 0), (first, 1), (second, 1), (second, 2)]


@pytest.mark.parametrize('failure', [None, ValueError('Conversion failed'), SystemExit(1)])
def test_dialog_runs_conversion_in_worker_and_propagates_errors(monkeypatch, failure):
    main_thread = get_ident()
    displayed = Event()
    values = []
    destroyed = []

    class Dialog:
        def __init__(self, *args, **kwargs):
            assert get_ident() == main_thread

        def Update(self, value, message):
            assert get_ident() == main_thread
            values.append(value)
            displayed.set()

        def Pulse(self):
            assert get_ident() == main_thread

        def Destroy(self):
            assert get_ident() == main_thread
            destroyed.append(True)

    monkeypatch.setattr(embroidery_progress.wx.App, 'IsDisplayAvailable', staticmethod(lambda: True))
    monkeypatch.setattr(embroidery_progress.wx, 'GetApp', lambda: object())
    monkeypatch.setattr(embroidery_progress.wx, 'ProgressDialog', Dialog)
    root = svg()
    elements = nodes_to_elements([root.add(Rectangle(width='10', height='20'))])
    expected = object()

    def convert(elements, progress_callback):
        assert get_ident() != main_thread
        progress_callback(elements[0], 0)
        assert displayed.wait(timeout=5)
        if failure is not None:
            raise failure
        progress_callback(elements[0], 1)
        return expected

    if failure is None:
        assert stitch_groups_with_progress(elements, convert) is expected
        assert values[-1] == 1000
    else:
        with pytest.raises(type(failure)) as error:
            stitch_groups_with_progress(elements, convert)
        assert error.value is failure
        assert 1000 not in values
    assert destroyed == [True]


def test_headless_export_does_not_create_a_dialog(monkeypatch):
    monkeypatch.setattr(embroidery_progress.sys, 'platform', 'linux')
    monkeypatch.setattr(embroidery_progress.wx.App, 'IsDisplayAvailable', staticmethod(lambda: False))
    dialog = Mock(side_effect=AssertionError('Must not create a dialog'))
    monkeypatch.setattr(embroidery_progress.wx, 'ProgressDialog', dialog)
    convert = Mock(return_value=['stitches'])
    elements = [object()]
    assert stitch_groups_with_progress(elements, convert) == ['stitches']
    convert.assert_called_once_with(elements)


def test_new_wx_application_outlives_the_dialog(monkeypatch):
    # Releasing a locally owned wx.App on return can crash during pending
    # native dialog cleanup. Keep a process-lifetime reference instead.
    app = object()
    factory = Mock(return_value=app)
    factory.IsDisplayAvailable.return_value = True
    dialog = Mock()
    monkeypatch.setattr(embroidery_progress, '_app', None)
    monkeypatch.setattr(embroidery_progress.wx, 'App', factory)
    monkeypatch.setattr(embroidery_progress.wx, 'GetApp', lambda: None)
    monkeypatch.setattr(embroidery_progress.wx, 'ProgressDialog', Mock(return_value=dialog))
    root = svg()
    elements = nodes_to_elements([root.add(Rectangle(width='10', height='20'))])

    assert stitch_groups_with_progress(elements, lambda *args, **kwargs: ['stitches']) == ['stitches']

    assert embroidery_progress._app is app
    factory.assert_called_once_with()
    dialog.Destroy.assert_called_once_with()
