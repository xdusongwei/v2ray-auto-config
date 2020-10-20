import os
import abc
import enum
import json
import time
import shlex
import base64
import string
import random
import asyncio
import logging
import subprocess
from collections import defaultdict
import aiohttp


class AirportLevel(enum.Enum):
    SOS = enum.auto()
    MEDIUM = enum.auto()


class ConfigBuilder(abc.ABC):
    async def build(self, config: dict, nodes: list):
        raise NotImplementedError


class OutboundConfigBuilder(ConfigBuilder):
    async def build(self, config: dict, nodes: list):
        outbounds = [node.to_outbound() for node in nodes]
        config["outbounds"] = outbounds


class Node:
    def __init__(self, tag, test_host=None, test_port=None, weight=5, protocol=None, settings=None, stream_settings=None, **kwargs):
        '''
        :param tag:
        :param test_host: 执行SpeedTest需要的host地址
        :param test_port: 执行SpeedTest需要的port地址
        :param weight: 设置权重，以影响节点最终的选择
        :param protocol: v2ray outbound 协议参数
        :param settings: v2ray settings 参数
        :param stream_settings: v2ray stream_settings 参数
        :param kwargs: 其他 v2ray outbound 参数
        '''
        self.tag = tag
        self.test_host = test_host
        self.test_port = test_port
        self.weight = weight
        self.ping = None
        self.protocol = protocol
        self.settings = settings or dict()
        self.stream_settings = stream_settings
        self.other_settings = kwargs

    @property
    def is_connected(self):
        return self.ping is not None

    def __str__(self):
        return f'<Node tag:{self.tag} weight:{self.weight} {self.ping or "--"}ms>'

    def __repr__(self):
        return self.__str__()

    def to_outbound(self):
        outbound = dict(
            tag=self.tag,
            protocol=self.protocol,
            settings=self.settings,
            streamSettings=self.stream_settings,
            **self.other_settings,
        )
        return outbound


class Airport:
    def __init__(self, airport_name: str, max_devices: int = 1, level: AirportLevel = AirportLevel.MEDIUM):
        assert airport_name
        assert isinstance(max_devices, int) and max_devices > 0
        self.airport_name = airport_name
        self.max_devices = max_devices
        self.level = level

    def __str__(self):
        return f'<Airport {self.airport_name}, {self.level}>'

    def __repr__(self):
        return self.__str__()

    async def pull_node_list(self) -> list:
        raise NotImplementedError


class SpeedTest:
    async def connection_test(self, v):
        task_list = list()
        node_list = list()
        for airport in v.airport_list:
            servers = await airport.pull_node_list()
            for server in servers:
                node_list.append((airport, server))
                task = asyncio.create_task(self.try_connect(server, v.connect_timeout))
                task_list.append(task)
        await asyncio.wait(task_list)
        node_list.sort(key=lambda x: (not x[1].is_connected, -x[1].weight, x[1].ping, x[0].airport_name))
        for airport, node in node_list:
            if node.is_connected:
                logging.info(f'{node} alive')
            else:
                logging.warning(f'{node} lost')
        node_list = [(airport, node,) for airport, node in node_list if node.is_connected]
        v.available_airport = defaultdict(set)
        for airport, node in node_list:
            v.available_airport[airport].add(node)

    async def try_connect(self, node: Node, connect_timeout, retry_times: int = 3):
        node.ping = None
        ping = 9999999
        for i in range(retry_times):
            sleep_time = 3 + i * 2
            try:
                begin_time = time.time()
                open_connection = asyncio.open_connection(node.test_host, node.test_port)
                reader, writer = await asyncio.wait_for(open_connection, timeout=connect_timeout)
                try:
                    await asyncio.sleep(sleep_time)
                    assert not writer.is_closing()
                    writer.write(''.join(random.choices(string.ascii_letters, k=64)).encode("utf8"))
                    await writer.drain()
                    finish_time = time.time()
                    ping = min(int((finish_time - begin_time - sleep_time) * 1000), ping)
                finally:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception as e:
                        pass
            except Exception as e:
                pass
        if ping >= 9999999:
            ping = None
        node.ping = ping


class AvailableTest:
    TEMP_CONFIG_PREFIX = '/tmp/v2ray_available_test'

    CONFIG_TEMPLATE = {
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": None,
                "protocol": "http",
                "tag": "test-http"
            },
        ],
        "outbounds": [

        ],
    }

    def __init__(self, http_proxy_port=range(4000, 4008), times=3, response_times=3, sleep_seconds=5,
                 v2ray_path='/usr/bin/v2ray/v2ray', url_list=None):
        self.http_proxy_port = http_proxy_port
        self.times = times
        self.response_times = response_times
        self.sleep_seconds =sleep_seconds
        self.url_list = url_list or ['https://google.com/', 'https://youtube.com/', 'https://github.com/', ]
        self.v2ray_path = v2ray_path

    async def connection_test(self, v):
        task_list = list()
        port_lock = {port: asyncio.Lock() for port in self.http_proxy_port}
        node_list = list()
        for airport in v.airport_list:
            nodes = await airport.pull_node_list()
            for node in nodes:
                node_list.append((airport, node))
                port = random.choice(list(port_lock.keys()))
                task = asyncio.create_task(self.try_connect(node, port, port_lock[port]))
                task_list.append(task)
        await asyncio.wait(task_list)
        node_list.sort(key=lambda x: (not x[1].is_connected, -x[1].weight, x[1].ping, x[0].airport_name))
        for airport, node in node_list:
            if node.is_connected:
                logging.info(f'{node} stable')
            else:
                logging.warning(f'{node} unstable')
        node_list = [(airport, node,) for airport, node in node_list if node.is_connected]
        v.available_airport = defaultdict(set)
        for airport, node in node_list:
            v.available_airport[airport].add(node)

    async def _popen_connect(self, node: Node, port: int):
        logger = logging.getLogger()
        ping = 9999999
        response_times = 0
        url = random.choice(self.url_list)
        config = json.loads(json.dumps(self.CONFIG_TEMPLATE))
        config['inbounds'][0]['port'] = port
        config['outbounds'].append(node.to_outbound())
        config_path = f'{self.TEMP_CONFIG_PREFIX}_{port}.json'
        args = shlex.split(f'"{self.v2ray_path}" "-config" "{config_path}"')
        p = subprocess.Popen(args)
        pid = p.pid
        try:
            for i in range(self.times):
                await asyncio.sleep(self.sleep_seconds)
                try:
                    begin_time = time.time()
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url=url, timeout=5, proxy=f"http://127.0.0.1:{port}") as resp:
                            text = await resp.text()
                    length = len(text or '')
                    response_times += 1
                    finish_time = time.time()
                    current_ping = int((finish_time - begin_time) * 1000)
                    logger.info(f'{node} times: {i + 1} ping:{current_ping}ms response: {length} bytes')
                    ping = min(current_ping, ping)
                except Exception as e:
                    logger.error(f'{node} available test failed: {e}')
                    if self.times - i - 1 + response_times < self.response_times:
                        break
        finally:
            p.kill()
            os.popen(f'kill {pid}')
            os.remove(config_path)
            p.communicate()
        return ping, response_times


    async def _http_connect(self, node: Node):
        logger = logging.getLogger()
        ping = 9999999
        response_times = 0
        url = random.choice(self.url_list)
        host = node.settings['servers'][0]['address']
        port = node.settings['servers'][0]['port']
        for i in range(self.times):
            await asyncio.sleep(self.sleep_seconds)
            try:
                begin_time = time.time()
                async with aiohttp.ClientSession() as session:
                    async with session.get(url=url, timeout=5, proxy=f"http://{host}:{port}") as resp:
                        text = await resp.text()
                length = len(text or '')
                response_times += 1
                finish_time = time.time()
                current_ping = int((finish_time - begin_time) * 1000)
                logger.info(f'{node} times: {i + 1} ping:{current_ping}ms response: {length} bytes')
                ping = min(current_ping, ping)
            except Exception as e:
                logger.error(f'{node} available test failed: {e}')
                if self.times - i - 1 + response_times < self.response_times:
                    break
        return ping, response_times

    async def try_connect(self, node: Node, port: int, port_lock: asyncio.Lock):
        node.ping = None
        async with port_lock:
            if node.protocol == 'http':
                ping, response_times = await self._http_connect(node)
            else:
                ping, response_times = await self._popen_connect(node, port)
        if ping >= 9999999:
            ping = None
        if response_times < self.response_times:
            ping = None
        node.ping = ping


class V2Ray:
    def __init__(self,
                 config_path: str = '/etc/v2ray/config.json',
                 config_template: dict = None,
                 restart_command: str = 'service v2ray restart',
                 connect_timeout: int = 4,
                 period_seconds: int = 8 * 3600,
                 ping_latency_ms: int = None,
                 test_object = None,
                 config_object = None,
                 ):
        assert config_path
        self.config_path = config_path
        self.airport_list = list()
        self.connect_timeout = connect_timeout
        self.available_airport = defaultdict(set)
        self.middleware_list = list()
        self.period_seconds = period_seconds
        self.template = config_template
        self.restart_command = restart_command
        self.ping_latency_ms = ping_latency_ms
        self.test_object = test_object or SpeedTest()
        self.config_object = config_object or OutboundConfigBuilder()

    def choose_node(self):
        nodes = list()
        available_airport = dict(self.available_airport.copy())
        not_sos = [airport for airport in self.available_airport if airport.level != AirportLevel.SOS]
        if not_sos:
            available_airport = {airport: node_set for airport, node_set in self.available_airport.items()
                                 if airport.level != AirportLevel.SOS}
        for airport, node_set in available_airport.items():
            node_list = sorted(node_set, key=lambda x: x.ping)
            node_list = node_list[:airport.max_devices]
            for node in node_list:
                if self.ping_latency_ms and node.ping > self.ping_latency_ms:
                    continue
                nodes.append(node)
        return nodes

    async def set_config(self, nodes: list):
        if self.template:
            if isinstance(self.template, dict):
                data = json.dumps(self.template)
            else:
                data = self.template
        else:
            file = open(self.config_path, "r")
            data = file.read()
            file.close()
        config_json = json.loads(data)

        handler = self.config_object.build(config_json, nodes=nodes)
        for middleware in self.middleware_list:
            handler = middleware(config_json, handler)
        await handler
        new_config = json.dumps(config_json, indent=True, sort_keys=True)
        file = open(self.config_path, "w")
        file.write(new_config)
        file.close()

    async def run(self):
        while True:
            logging.info(f'test begin')
            await self.test_object.connection_test(self)
            nodes = self.choose_node()
            logging.info(f'choose nodes: {nodes}')
            await self.set_config(nodes)
            logging.info('config saved')
            if self.restart_command:
                os.system(self.restart_command)
                logging.info('v2ray restart')
            await asyncio.sleep(self.period_seconds)

    @classmethod
    def decode_subscribe(cls, subscription_content: str):
        s = subscription_content
        vmess_lines = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).split()
        config_list = [json.loads(base64.urlsafe_b64decode(line.replace(b"vmess://", b""))) for line in vmess_lines]
        return config_list

    @classmethod
    async def download_subscribe(cls, url):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url=url, timeout=12) as resp:
                    assert resp.status == 200
                    text = await resp.text()
            return text
        except Exception as e:
            logging.getLogger().exception(f'download subscribe {url} failed')
        return None


class Balancer:
    def __init__(self, tag, selector):
        self.tag = tag
        self.selector = selector or list()

    @property
    def is_empty(self):
        return not bool(self.selector)

    def to_balancer(self):
        return {
            'tag': self.tag,
            'selector': self.selector,
        }


class Rule:
    def __init__(self, **kwargs):
        self.args = kwargs.copy()

    def to_rule(self):
        return self.args.copy()


__all__ = [
    'V2Ray',
    'Airport',
    'AirportLevel',
    'Node',
    'SpeedTest',
    'AvailableTest',
    'Balancer',
    'Rule',
    'ConfigBuilder',
    'OutboundConfigBuilder',
]
