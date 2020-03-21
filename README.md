v2ray-auto-config
=================


v2ray 定时更新脚本工具, 可以定时测速重新选择节点, 自己编写拉取机场节点列表逻辑, 根据机场设备限制数量选择多个节点, 
或者使用 middleware 方法自定义最终 v2ray 配置


```shell script
pip install v2ray-auto-config
```


```python
import asyncio
import logging
import v2ray

logging.getLogger().setLevel(logging.getLevelName('INFO'))


class FakeAirport(v2ray.Airport):
    async def pull_node_list(self) -> list:
        settings = {
            "servers": [
                {
                    "address": '127.0.0.1',
                    "port": 12345,
                    "method": "chacha20",
                    "password": "qwerty",
                }
            ]
        }
        node = v2ray.Node(tag=f'node@{self.airport_name}', host='127.0.0.1', port=12345, protocol='shadowsocks', settings=settings)
        return [node, ]

async def main():
    v = v2ray.V2Ray(config_path='/etc/v2ray/config.json')
    v.airport_list.append(FakeAirport(airport_name='fake.com', max_devices=1))
    await v.run()


if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main())

```

V2Ray
-----

V2Ray 可以设置如下参数:

*   config_path: v2ray 配置文件路径, 新的配置将写入这个位置
*   template: 可以传入初始 v2ray 模板(字符串或者字典对象)作为基础进行设置, 设置后将不会从 config_path 读取配置信息作为基础来操作
*   connect_timeout: 每个节点测试连接时的连接超时
*   period_seconds: v2ray 服务重启周期
*   restart_command: 重启 v2ray 需要的命令


你可以在 Airport 对象创建时设置机场类型(level):

*   AirportLevel.SOS: 对于连接长期稳定但质量较差的机场, 如果存活节点中有更好质量的机场时, 他们将排除出可用节点列表
*   AirportLevel.MEDIUM: 高连接质量但长期服务稳定不能保证的机场


配置文件操作中间件
-----------------

通常更新配置操作只会修改 v2ray 配置文件的 outbounds 节内容为选择好的存活节点, 
如果你需要更复杂的配置操作, 可以传入 middleware 方法:

```python
import v2ray

async def update_config(config, handler):
    print('before config dict change', config)
    await handler
    print('after config dict change', config)


async def main():
    v = v2ray.V2Ray()
    # ...
    v.middleware_list.append(update_config)
    # ...
    await v.run()
```


routing
-------

```python
import v2ray


class ConfigBuilder(v2ray.ConfigBuilder):
    async def build(self, config: dict, nodes: list):
        outbounds = config['outbounds']
        freedom = v2ray.Node(
            'direct',
            None,
            None,
            protocol='freedom',
        )
        outbounds.append(freedom.to_outbound())

        outside_balancer = v2ray.Balancer('outside', [node.tag for node in nodes])
        balancers = config['routing']['balancers']
        balancers.append(outside_balancer.to_balancer())

        rules: list = config['routing']['rules']
        direct_rule = v2ray.Rule(
            ip=[
                "geoip:private",
                "geoip:cn"
            ],
            outboundTag="direct",
            type="field",
        )
        rules.append(direct_rule.to_rule())
        outside_rule = v2ray.Rule(
            balancerTag="outside",
            network="tcp,udp",
            type="field",
        )
        rules.append(outside_rule.to_rule())


async def main():
    config_builder = ConfigBuilder()
    v = v2ray.V2Ray(..., config_object=config_builder)
    # ...
    # ...
    await v.run()
```


测试节点
-------

`SpeedTest` 提供了通过连接速度的节点测试

`AvailableTest` 通过建立临时v2ray进程测试节点线路是否可用, 达到稳定要求

```python
import v2ray


async def main():
    # 测试每个节点需要完成3次代理请求, 必须达到2次以上请求成功, 请求间隔2秒
    test = v2ray.AvailableTest(v2ray_path='/path/to/v2ray', response_times=2, times=3, sleep_seconds=2)
    v = v2ray.V2Ray(..., test_object=test)
    # ...
    # ...
    await v.run()

```

其他
----

```python
import v2ray


async def main():
    # 下载订阅文件内容
    subscribe = await v2ray.V2Ray.download_subscribe('https://example.com/subscribe')
    # 解析订阅文件
    node_list = await v2ray.V2Ray.decode_subscribe(subscribe)
    
```
