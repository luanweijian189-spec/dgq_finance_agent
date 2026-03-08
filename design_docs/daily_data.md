# 免费盘中数据方案（更新版）

## 结论先说

如果目标是：

- 免费
- 尽可能稳定
- 盘中能长期跑
- 能拿分钟线、分时、逐笔、盘口

那**不要再把 AKShare 当唯一主源**。

免费前提下，当前最现实的方案是：

1. **主拉取源：`pytdx` / 通达信行情服务器**
2. **补偿源：`AKShare` 的东方财富链路**
3. **兜底：本地缓存 + 本地落库 + 失败降级**

也就是：

> `pytdx -> AKShare -> 本地缓存/存储`

这比“只用东财公开 HTTP 接口硬拉”稳定得多。

---

## 为什么原来那套说法不够

原文里最核心的问题有两个：

### 1）把问题理解成“只要上代理池就能解决”

这不完整。

代理池只能缓解：

- 被限频
- 某个出口 IP 被风控
- 某些 HTTP 公开接口的访问不稳定

但解决不了：

- 上游接口字段改版
- 上游直接空响应
- 逐笔接口返回非 JSON / 非结构化内容
- 免费网页接口本身不是给高强度程序采集用的

所以**代理池不是根治方案**，最多是补丁。

### 2）把盘中数据默认等同于“东财网页接口”

这也是问题。

公开网页接口适合：

- 快速验证
- 单股查询
- 轻量轮询

不适合：

- 长时间稳定盘中监控
- 高频重复抓取
- 唯一生产主源

---

## 网络资料和文档核验后的客观结论

### A. AKShare

AKShare 官方文档自己就明确提示了数据使用风险：

- 网络超时可“重新运行”
- 可以“更换 IP”
- 需要“降低访问频率”

而且 changelog 里多次反复修复这些盘中相关接口：

- `stock_zh_a_hist_min_em`
- `stock_zh_a_minute`
- `stock_intraday_em`
- `stock_intraday_sina`
- `stock_bid_ask_em`

这说明一件事：

> AKShare 能用，但底层依赖网页接口，稳定性天然有限。

### B. pytdx

pytdx 文档支持的盘中能力非常完整：

- `get_security_quotes()`：实时快照、五档盘口
- `get_security_bars()`：1/5/15/30/60 分钟 K
- `get_minute_time_data()`：当日分时
- `get_history_minute_time_data()`：历史分时
- `get_transaction_data()`：逐笔成交
- `get_history_transaction_data()`：历史逐笔成交

而且文档明确支持：

- `heartbeat=True`
- `auto_retry=True`
- 多行情主机列表
- 连接池 / 热备思路

这意味着：

> 在免费世界里，`pytdx` 更像一个可以持续运行的实时底座。

### C. efinance

`efinance` 也能拿：

- 全市场实时行情
- 分钟 K
- 分钟级资金流

但它本质上仍然主要是东财公开数据的封装，定位更接近：

- 好用的备用封装
- 补偿源

不是唯一主源。

### D. baostock

`baostock` 适合：

- 历史日线
- 历史分钟补数
- 夜间回补

不适合实时盘中主链路。

### E. tushare

Tushare 的分钟/实时分钟不是免费主路径，文档里写得很明确：

- 历史分钟：独立权限
- 实时分钟：独立权限

所以如果要求是**必须免费**，Tushare 不能当免费盘中解法。

---

## 最优免费架构

### 第一层：主拉取源

使用 `pytdx`

负责：

- 单股分钟线
- 实时快照
- 五档盘口
- 逐笔成交

原因：

- 免费里最接近实时底座
- 不完全依赖网页反爬链路
- 支持多主机轮询、心跳、自动重连

### 第二层：补偿源

使用 `AKShare`

负责：

- pytdx 失败时补分钟线
- 板块/资金流这类 pytdx 不擅长的数据
- 某些展示型补字段

### 第三层：本地事实层

必须保留：

- `intraday_bars`
- `intraday_ticks`
- `source`
- `ingest_time`

原则：

> 盘中分析尽量读本地，不要每次都临时打第三方。

---

## 当前项目里的落地方案

已经做的改造：

1. 新增了 `pytdx` 依赖
2. 新增了 `PytdxIntradayDataProvider`
3. 新增了 `CompositeIntradayDataProvider`
4. `INTRADAY_DATA_PROVIDER` 现在支持：
	- `akshare`
	- `pytdx`
	- `freebest`

其中：

- `pytdx`：只走通达信行情主机
- `freebest`：**先 pytdx，失败再自动回退到 AKShare**

这就是当前最适合你的免费稳定方案。

---

## 推荐配置

建议环境变量：

```env
INTRADAY_DATA_PROVIDER=freebest
INTRADAY_REQUEST_INTERVAL_SECONDS=1.2
INTRADAY_MAX_RETRIES=2
INTRADAY_PYTDX_BAR_COUNT=800
INTRADAY_PYTDX_TICK_LIMIT=2000
```

如果你手里有稳定可用的通达信主机，可以额外指定：

```env
INTRADAY_PYTDX_HOSTS=119.147.212.81:7709,221.231.141.60:7709
```

不填则自动使用 `pytdx` 内置主机列表。

---

## 盘中采集频率建议

不要再暴力打源。

建议：

- 单股分钟线：每 $5\sim15$ 秒一次足够
- 股票池快照：分批轮询，不要一次全打
- 逐笔：只对重点股票开
- 历史补数：收盘后跑

---

## 生产级注意事项

即便改成 `pytdx + AKShare fallback`，也要接受一个现实：

> 这是“免费里尽可能稳”，不是商业级 SLA。

所以必须加：

1. 限频
2. 缓存
3. 熔断
4. 失败切源
5. 本地落库
6. 夜间补数

否则免费源再好也会被自己打崩。

---

## 最终建议

如果你现在就是要一个**免费、尽可能稳定的盘中链路**，当前结论已经很明确：

### 不要再做的

- 只靠 AKShare 硬顶
- 只靠东财 HTTP 全盘轮询
- 指望代理池解决全部问题

### 应该做的

- 主源改成 `pytdx`
- AKShare 留作补偿源
- 启用 `freebest` 多源降级
- 所有盘中数据先落本地再消费

一句话：

> **免费条件下，最优解不是“找一个更猛的单接口”，而是“pytdx 主拉取 + AKShare 回退 + 本地缓存落库”的多源架构。**

---

## 参考资料

- AKShare 数据说明: https://akshare.akfamily.xyz/data_tips.html
- AKShare 答疑专栏: https://akshare.akfamily.xyz/answer.html
- AKShare 更新日志: https://akshare.akfamily.xyz/changelog.html
- pytdx 标准行情文档: https://pytdx-docs.readthedocs.io/zh-cn/latest/pytdx_hq/
- pytdx 连接池文档: https://pytdx-docs.readthedocs.io/zh-cn/latest/pytdx_pool/
- pytdx 命令行示例: https://pytdx-docs.readthedocs.io/zh-cn/latest/hqget/
- efinance 文档: https://efinance.readthedocs.io/
- baostock PyPI: https://pypi.org/project/baostock/
- Tushare `pro_bar`: https://tushare.pro/document/2?doc_id=109
- Tushare 权限说明: https://tushare.pro/document/2?doc_id=290