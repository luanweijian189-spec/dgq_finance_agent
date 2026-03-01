# wechaty-relay

该服务用于把微信文本消息转发到后端接口：

- 目标接口：`POST /api/connectors/wechat/webhook`
- 默认地址：`http://127.0.0.1:8000/api/connectors/wechat/webhook`

## 使用步骤

1. 安装依赖

```bash
cd connectors/wechaty-relay
npm install
```

2. 启动 relay

```bash
BACKEND_WEBHOOK_URL=http://127.0.0.1:8000/api/connectors/wechat/webhook \
WECHAT_ROOM_WHITELIST=一起赚钱！,一起发财 \
WECHAT_ROOM_KEYWORDS=一起赚钱,赚钱 \
npm start
```

若不传 `WECHAT_ROOM_WHITELIST`，默认也会监控 `一起赚钱！` 群。

## 群匹配规则

支持两种配置，可同时使用：

1. `WECHAT_ROOM_WHITELIST`
	- 精确名单（支持多个群名，逗号分隔）
	- 同时做轻度模糊：忽略空格与中英文标点后，做包含匹配

2. `WECHAT_ROOM_KEYWORDS`
	- 关键词名单（支持多个关键词，逗号分隔）
	- 只要群名包含关键词即命中

示例：

- 群名 `一起赚钱！` 可被 `一起赚钱` 命中
- 群名 `一起赚钱-2群` 可被关键词 `赚钱` 命中

3. 扫码登录后，群聊或私聊中的文本消息会被过滤：

- 包含 A 股代码（`60/00/30/68` 开头的 6 位代码）
- 且包含意图关键词（看好/关注/推荐/逻辑/买入/加仓/估值/催化）

符合条件则转发给后端入库。

## 注意

- 需遵守微信平台与公司合规要求，仅用于内部投研。
- 建议在专用账号与专用机器上 7x24 运行。
