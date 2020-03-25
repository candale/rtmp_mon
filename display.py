import os

from bokeh.driving import count
from bokeh.models import ColumnDataSource
from bokeh.plotting import curdoc, figure

from bs4 import BeautifulSoup
import requests


UPDATE_INTERVAL = 50
ROLLOVER = 100  # Number of displayed data points

source = ColumnDataSource({"x": [], "y": []})
last_bytes_in = None


def get_data(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.content)
    app_data = soup.rtmp.server.application.live
    try:
        data = {
            'bytes_in': int(app_data.bytes_in.get_text()),
            'bytes_out': int(app_data.bytes_out.get_text()),
            'time': int(app_data.time.get_text())
        }
    except AttributeError:
        return None

    return data


def compute_y():
    global last_bytes_in
    data = get_data(os.environ['STAT_URL'])
    if data is None:
        return 103

    bytes_in = data['bytes_in']
    if last_bytes_in is None:
        last_bytes_in = bytes_in
        return

    y = bytes_in - last_bytes_in
    last_bytes_in = bytes_in

    return y


@count()
def update(x):
    y = compute_y()
    if y is None:
        return

    source.stream({"x": [x], "y": [y]}, rollover=ROLLOVER)


p = figure()
p.line("x", "y", source=source)

doc = curdoc()
doc.add_root(p)
doc.add_periodic_callback(update, UPDATE_INTERVAL)
