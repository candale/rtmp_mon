import os
import time
import copy
import logging
from typing import List
from datetime import datetime
from dataclasses import dataclass, field
from collections import defaultdict

from bokeh.models import ColumnDataSource
from bokeh.plotting import curdoc, figure
from bokeh.layouts import row, layout

from lxml import etree
import requests


logger = logging.getLogger(__name__)


@dataclass
class StreamStat:

    name: str = None
    last_bytes_in: int = None
    bytes_ps_in: int = None
    history: List[int] = field(default_factory=list)
    bps_in_audio: int = 0
    bps_in_video: int = 0
    publishing_dropped: int = 0
    last_time_sampled: int = 0


def add_to_list_max_n(l, n):
    if len(l) > n:
        raise ValueError('List already longer than n')

    l.append(n)
    if len(l) > n:
        l.pop(0)


class RTMPStat:
    """Parse the page and hand over the raw data"""

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
    """Create stats based on raw data coming from the page"""

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

            elapsed_ms = (time.time() - stream_stat.last_time_sampled) * 1000
            diff = stream_info['bytes_in'] - stream_stat.last_bytes_in
            stream_stat.bytes_ps_in = diff * 1000 / elapsed_ms

            stream_stat.last_bytes_in = stream_info['bytes_in']
            stream_stat.last_time_sampled = time.time()

            stream_stat.bps_in_video = stream_info['bw_video']
            stream_stat.bps_in_audio = stream_info['bw_audio']
            streaming_client = None
            for client in stream_info['clients']:
                if client['publishing']:
                    streaming_client = client
                    break
            else:
                logger.error('Weird, no streaming client?')
            stream_stat.publishing_dropped = streaming_client['dropped']

            add_to_list_max_n(stream_stat.history, self.max_history_points)

    def run(self):
        streams = self.rtmp_stat.get_streams()
        if not streams:
            return False

        self.compute_point_in_time(streams)


class StreamGraphManager:

    def __init__(self, stream_name, rollover=500):
        self.stream_name = stream_name
        self.rollover = rollover
        self._update_functions = []
        self._graphs = []
        self._make_representation()

    def make_bytes_in_graph(self):
        source = ColumnDataSource({'time': [], 'kpbs': []})
        graph = figure(
            title="STREAM: {} | KB IN".format(self.stream_name),
            x_axis_type="datetime",
            width=500, height=200
        )
        graph.line(
            'time', 'kpbs', source=source, line_color='blue',
            legend_label='KPBS')
        graph.legend.location = "top_left"

        def update_function(stream_stat):
            kbps_bytes_ps_in = round(
                stream_stat.bytes_ps_in / 1024, 2)
            source.stream(
                {'time': [datetime.utcnow()], 'kpbs': [kbps_bytes_ps_in]},
                rollover=self.rollover
            )

        return graph, update_function

    def make_bps_in_video_graph(self):
        source = ColumnDataSource({'time': [], 'bits/s': []})
        graph = figure(
            title="STREAM: {} | KBPS IN VIDEO".format(self.stream_name),
            x_axis_type="datetime",
            width=500, height=200
        )
        graph.line('time', 'bits/s', source=source)

        def update_function(stream_stat):
            kpbs_in_video = round(
                stream_stat.bps_in_video / 1024, 2)
            source.stream(
                {'time': [datetime.utcnow()], 'bits/s': [kpbs_in_video]},
                rollover=self.rollover
            )

        return graph, update_function

    def make_bps_in_audio_graph(self):
        source = ColumnDataSource({'time': [], 'bits/s': []})
        graph = figure(
            title="STREAM: {} | KBPS IN AUDIO".format(self.stream_name),
            x_axis_type="datetime",
            width=500, height=200
        )
        graph.line('time', 'bits/s', source=source)

        def update_function(stream_stat):
            kpbs_in_audio = round(
                stream_stat.bps_in_audio / 1024, 2)
            source.stream(
                {'time': [datetime.utcnow()], 'bits/s': [kpbs_in_audio]},
                rollover=self.rollover
            )

        return graph, update_function

    def make_dropped_graph(self):
        source = ColumnDataSource({'time': [], 'dropped': []})
        graph = figure(
            title="STREAM: {} | DROPPED FRAMES".format(self.stream_name),
            x_axis_type="datetime",
            width=500, height=200
        )
        graph.line('time', 'dropped', source=source)

        def update_function(stream_stat):
            source.stream(
                {'time': [datetime.utcnow()], 'dropped': [stream_stat.publishing_dropped]},
                rollover=self.rollover
            )

        return graph, update_function

    def _make_representation(self):
        graph_functions = [
            self.make_bytes_in_graph,
            self.make_bps_in_video_graph,
            self.make_bps_in_audio_graph,
            self.make_dropped_graph
        ]

        for graph_function in graph_functions:
            graph, update_function = graph_function()
            self._update_functions.append(update_function)
            self._graphs.append(graph)

    @property
    def representation(self) -> list:
        return self._graphs

    def update(self, stream_stat):
        for update_function in self._update_functions:
            update_function(stream_stat)


class GraphManager:
    """Handle the painting of the data based on calculated stats"""

    def __init__(
            self,
            rtmp_data_mon: RTMPDataMon,
            rollover=1000,
            update_interval=100
    ):
        self._rtmp_data_mon = rtmp_data_mon
        self.rollover = rollover
        self.update_interval = update_interval
        self._streams_graphs = {}

    def _init_streams(self):
        self._rtmp_data_mon.run()
        for stream_name, stream_stat in self._rtmp_data_mon.stream_stats.items():
            self._streams_graphs[stream_name] = StreamGraphManager(stream_name)

    def update_stream_graphs(self):
        self._rtmp_data_mon.run()
        for stream_name, stream_stat in self._rtmp_data_mon.stream_stats.items():
            if stream_name not in self._streams_graphs:
                logger.warning(
                    "Another stream [{}] appeared but we don't have a graph for it"
                    .format(stream_name)
                )
                continue

            stream_graph = self._streams_graphs[stream_name]
            stream_graph.update(stream_stat)

    def run(self):
        self._init_streams()
        doc = curdoc()

        rows = []
        for stream_name, stream_graph in self._streams_graphs.items():
            rows.append(stream_graph.representation)

        doc.add_root(layout(rows))
        doc.add_periodic_callback(self.update_stream_graphs, self.update_interval)


rtmp_data_mon = RTMPDataMon(os.environ['STAT_URL'])
graph_manger = GraphManager(rtmp_data_mon)
graph_manger.run()
