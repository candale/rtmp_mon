import os
import copy
from typing import List
from datetime import datetime
from dataclasses import dataclass, field
from collections import defaultdict

from bokeh.models import ColumnDataSource
from bokeh.plotting import curdoc, figure
from bokeh.layouts import row

from lxml import etree
import requests


UPDATE_INTERVAL = 50
ROLLOVER = 1000  # Number of displayed data points

raw_data_source = ColumnDataSource({"x": [], "y": []})
median_line_source = ColumnDataSource({"x": [], "y": []})

last_bytes_in = None


@dataclass
class StreamStat:

    name: str = None
    last_bytes_in: int = None
    current_rate: int = None
    history: List[int] = field(default_factory=list)


def add_to_list_max_n(l, n):
    if len(l) > n:
        raise ValueError('List already longer than n')

    l.append(n)
    if len(l) > n:
        l.pop(0)


class RTMPStat:

    def __init__(self, url):
        self.url = url

    def get_keys_from_elements(self, keys, element):
        result = {}
        for key in keys:
            xp_object = element.xpath('./{}'.format(key))
            if not xp_object:
                continue

            # we assume there is only one
            value = xp_object[0].text
            if value and value.isdigit():
                value = int(value)

            result[key] = value

        return result

    def parse_client_info(self, client_xp):
        interesting_keys = [
            'id', 'address', 'time', 'flashver', 'swfurl', 'dropped', 'avsync',
            'timestamp', 'publishing'
        ]

        client_info = self.get_keys_from_elements(interesting_keys, client_xp)
        client_info['active'] = bool(client_xp.xpath('./active'))
        # if this is True, it means that this is the client that is generating
        # the stream. when false, it's a client that consumes the stream
        client_info['publishing'] = bool(client_xp.xpath('./publishing'))

        return client_info

    def parse_stream_info(self, stream):
        interesting_keys = [
            'name', 'time', 'bw_in', 'bytes_in', 'bw_out', 'bytes_out',
            'bw_audio', 'bw_video', 'nclients'
        ]
        stream_info = {
            'clients': []
        }
        stream_info.update(
            self.get_keys_from_elements(interesting_keys, stream))

        for client_xp in stream.xpath('./client'):
            stream_info['clients'].append(self.parse_client_info(client_xp))

        return stream_info

    def get_streams(self):
        response = requests.get(self.url)
        xml = etree.fromstring(response.content)
        streams = xml.xpath('//stream')
        if len(streams) == 0:
            return []

        streams_info = []
        for stream_xp in streams:
            stream_info = self.parse_stream_info(stream_xp)
            stream_info['active'] = bool(stream_xp.xpath('./active'))
            stream_info['publishing'] = bool(stream_xp.xpath('./publishing'))
            streams_info.append(stream_info)

        return streams_info


class RTMPDataMon:

    def __init__(self, stat_url, max_history_points=30):
        self._stream_stats = defaultdict(StreamStat)
        self.max_history_points = max_history_points
        self.rtmp_stat = RTMPStat(stat_url)

    @property
    def stream_stats(self):
        return copy.copy(self._stream_stats)

    def compute_point_in_time(self, streams):
        for stream_info in streams:
            stream_stat = self._stream_stats[stream_info['name']]
            if stream_stat.last_bytes_in is None:
                stream_stat.name = stream_info['name']
                stream_stat.last_bytes_in = stream_info['bytes_in']
                continue

            stream_stat.current_rate = (
                stream_info['bytes_in'] - stream_stat.last_bytes_in)
            stream_stat.last_bytes_in = stream_info['bytes_in']
            add_to_list_max_n(stream_stat.history, self.max_history_points)

    def run(self):
        streams = self.rtmp_stat.get_streams()
        if not streams:
            return False

        self.compute_point_in_time(streams)


class GraphManager:

    def __init__(
            self,
            rtmp_data_mon: RTMPDataMon,
            rollover=100,
            update_interval=100
    ):
        self.rtmp_data_mon = rtmp_data_mon
        self.rollover = rollover
        self.update_interval = update_interval
        self._sources = {}
        self._figures = {}
        self._x_count = 0

    @property
    def figures(self):
        return list(self._figures.values())

    def make_figure_and_source(self, stream_stat):
        source = ColumnDataSource({"x": [], "y": []})
        graph = figure(
            title="STREAM: {} | BYTES IN".format(stream_stat.name),
            x_axis_type="datetime"
        )
        graph.line('x', 'y', source=source)

        return graph, source

    def _init_sources(self):
        self.rtmp_data_mon.run()
        for stream_name, stream_stat in self.rtmp_data_mon.stream_stats.items():
            self._figures[stream_name], self._sources[stream_name] = (
                self.make_figure_and_source(stream_stat))

    def update_data_sources(self):
        self.rtmp_data_mon.run()
        for stream_name, stream_stat in self.rtmp_data_mon.stream_stats.items():
            data_source = self._sources.get(stream_name)
            if data_source is None:
                continue

            data_source.stream(
                {'x': [datetime.utcnow()], 'y': [stream_stat.current_rate]},
                rollover=self.rollover
            )
            self._x_count += 1

    def run(self):
        self._init_sources()
        doc = curdoc()
        doc.add_root(row(*self.figures))
        doc.add_periodic_callback(self.update_data_sources, self.update_interval)


rtmp_data_mon = RTMPDataMon(os.environ['STAT_URL'])
graph_manger = GraphManager(rtmp_data_mon)
graph_manger.run()
