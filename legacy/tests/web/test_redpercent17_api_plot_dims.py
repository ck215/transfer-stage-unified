"""REDPERCENT-17 (Web renderer half): /api/plot honours an explicit
dim1/dim2/dim3 pick and falls back to file order when one is absent.

Server-side half of the same finding covered end-to-end (client) by
tests/web/test_redpercent17_plot_dims.py (Node harness) and at the pure
renderer level by tests/core/test_redpercent17_plot_blank_figure_message.py.
This exercises the real HTTP route -- the same `SESSION_TOKEN` request
pattern tests/web/test_web_security.py uses.

`matplotlib` is globally mocked for the whole suite (tests/conftest.py),
so `fig.savefig(buf, ...)` never actually writes bytes into `buf` --
asserting on `image_base64` being non-empty would be vacuous (it is always
`''` under the mock, whether or not the fix works). What *is* real: the
route's own dim-selection logic, which picks between the client's
dim1/dim2/dim3 and file order before calling `render_red_percent_figure`
at all. Patch that one function (imported fresh inside
`_do_GET_impl`'s sibling `_do_POST_impl` on every request, so the patch is
picked up) and read the arguments the route actually passed it -- real
evidence of which dims were selected, not a byte count.
"""
import json
from unittest.mock import patch

import pytest

from model.system_manager import SystemManager
from views.web.web_server import SESSION_TOKEN, TOKEN_HEADER, WebDashboardServer

CSV_TEXT = (
    "# Probe Name,TestProbe\n"
    "\n"
    "Red Percent,Stepper X Location,Stepper Y Location\n"
    "10.5,1.0,2.0\n"
    "20.5,3.0,4.0\n"
)


@pytest.fixture
def server():
    srv = WebDashboardServer(SystemManager(), host="127.0.0.1", port=0)
    srv.start(background=True)
    yield srv
    srv.stop()


def _post(server, path, body):
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", TOKEN_HEADER: SESSION_TOKEN}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8") if e.fp else ""
        return e.code, (json.loads(body_text) if body_text else {})


def test_api_plot_reports_the_csvs_dims(server):
    status, data = _post(server, "/api/plot", {"csv_data": CSV_TEXT, "plot_type": "0D"})
    assert status == 200
    assert data["dims"] == ["X", "Y"]


def test_api_plot_honours_an_explicit_dim_pick_over_file_order(server):
    # File order would pick dim1=X, dim2=Y for a bare 2D request. Ask for
    # the reverse explicitly and require the actual render call to have
    # received exactly that pick.
    with patch("model.plot_data.render_red_percent_figure") as mock_render:
        status, data = _post(server, "/api/plot", {
            "csv_data": CSV_TEXT, "plot_type": "2D", "dim1": "Y", "dim2": "X",
        })
    assert status == 200
    assert mock_render.called
    plot_type, dim1, dim2, dim3, red_percents, dim_data = mock_render.call_args[0]
    assert (dim1, dim2) == ("Y", "X"), (
        "an explicit dim pick must reach render_red_percent_figure, not be "
        "silently overridden by file order"
    )


def test_api_plot_falls_back_to_file_order_when_dims_are_unset(server):
    # No dim1/dim2 at all -- must still behave exactly like the
    # pre-REDPERCENT-17 client (first two header dims in file order).
    with patch("model.plot_data.render_red_percent_figure") as mock_render:
        status, data = _post(server, "/api/plot", {"csv_data": CSV_TEXT, "plot_type": "2D"})
    assert status == 200
    assert mock_render.called
    plot_type, dim1, dim2, dim3, red_percents, dim_data = mock_render.call_args[0]
    assert (dim1, dim2) == ("X", "Y")


def test_api_plot_ignores_a_dim_not_present_in_the_csv(server):
    # An unrecognised pick (e.g. a stale selection left over from a
    # previously loaded CSV) must fall back to file order rather than be
    # sent straight through to a dimension that does not exist.
    with patch("model.plot_data.render_red_percent_figure") as mock_render:
        status, data = _post(server, "/api/plot", {
            "csv_data": CSV_TEXT, "plot_type": "2D", "dim1": "Z", "dim2": "Y",
        })
    assert status == 200
    assert mock_render.called
    plot_type, dim1, dim2, dim3, red_percents, dim_data = mock_render.call_args[0]
    assert dim1 == "X", "an unrecognised dim1 must fall back to file order, not pass through"
    assert dim2 == "Y"
