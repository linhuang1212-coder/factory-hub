# FactoryHub · 珠宝工厂端系统

> 珠宝生态「工厂端」项目文档。当前版本 **v0.4**（2026-07-03）。
> 关联蓝图：门店仓库 `AI-ERP2.0/docs/factory-pre-inbound-plan.md`（预入库完整设计，含门店侧改造方案）。

---

## 1. 项目定位

用户的珠宝生态由四段组成，FactoryHub 是其中的**工厂段**：

```
[设计] jewelry-design-studio → [工厂] FactoryHub → [零售] AI-ERP2.0 → [消费者]
        AI出图/出STL              本项目               进销存+金料+结算
```

**解决的核心痛点**：工厂发货靠纸质/Excel 单，门店收货要把同一批货**再录一遍**（或拍照 OCR）。
FactoryHub 让货在工厂源头数字化一次，转移时结构化推送门店，门店直接收到**预入库单**（draft），
店员过秤核对后确认即入账——消除双重录入，且预报重 vs 实收重的差异全程留痕。

**试点计划**：两家工厂先行——**梵贝琳工厂**（自家板房）+ **华玥珠宝**（外部合作工厂）。
一厂一实例、独立数据库、账号密码登录，后续部署阿里云服务器（拟端口 8201/8202）。

## 2. 业务模型（同构 AI-ERP：入库 → 库存 → 转移）

```
📥 产品入库(FRK单)      板房把生产好的货逐件过秤录入 → 进工厂库存。保存即生效。
📦 工厂库存             在库/待转移/已转移 三态管理，按品名/款号/成色搜索，克重精确汇总。
🚚 转移商品(ZY单)       挑在库货 → 选择转移给哪个客户 → 推送 → 对方生成预入库 draft。
👥 客户管理(仅admin)    客户=收货方门店/公司。一客户一端点一Key，一厂可服务多客户。
```

**库存件状态机**（防错核心）：

```
in_stock(在库) ──建转移单──► reserved(锁定,防重复转移) ──推送成功──► transferred(已转移)
      ▲                          │
      └────────删除转移草稿(解锁)──┘
```

- 入库单里有货进了转移流程 → 该入库单不可删（防账实脱节）
- 转移单必选客户；停用的客户不能建新转移
- 推送失败：转移单留 draft、货保持锁定、失败原因留痕（push_response），可安全重推
- **幂等**：ZY 转移单号是推送幂等键，网络重试不会在门店产生重复单

## 3. 技术栈与架构

- **后端**：FastAPI + SQLAlchemy + SQLite（单文件库，每实例独立）
- **前端**：零构建 vanilla JS 单页（ERP 式左侧导航），无 node 依赖
- **登录**：stdlib PBKDF2 密码哈希 + HMAC-SHA256 签名令牌（7 天），零第三方依赖；
  首启种子管理员；admin 可建子账号；除 `/api/health` 外所有接口须登录
- **精度铁律**：克重/金额**全程字符串 + Decimal**（SQLite NUMERIC 会退化 float，故存 TEXT），
  克重 4 位小数、金额 2 位，入口 validator 规范化。实测 `5.23+18.66+8.10=31.9900` 分毫不差
- **隔离**：一厂一实例一数据库（同构 AI-ERP 的"一店一库"哲学），两厂数据物理不可能混

```
factory-hub/
├── start_factory_hub.bat        一键启动(自动建venv/装依赖/复制.env)
├── backend/
│   ├── .env                     实例配置(端口/门店/登录/开关)
│   ├── run.py                   启动入口
│   ├── factory_hub.db           SQLite(运行生成)
│   └── app/
│       ├── main.py              装配+建表+轻量迁移+种子(管理员/默认客户)
│       ├── config.py            读 .env
│       ├── models.py            FactoryInbound/StockItem/TransferOrder/Customer/User/StyleCache
│       ├── schemas.py           入参校验(克重金额字符串+Decimal规范化)
│       ├── security.py          PBKDF2+HMAC令牌+require_auth/require_admin
│       ├── decimal_utils.py     精度工具(禁float)
│       ├── doc_no.py            FRK-/ZY- 单号生成
│       ├── services/store_client.py   按客户档案推送/拉款式(优雅降级)
│       └── routers/             auth_routes/inbounds/stock/transfers/customers/styles
├── frontend/                    index.html/app.js/styles.css
└── deploy/                      两厂实例 env 模板(fanbeilin/huayue)
```

## 4. 数据模型

| 表 | 说明 | 关键字段 |
|----|------|---------|
| `factory_inbounds` | 工厂入库单 | order_no(FRK-日期-序号)/order_date/operator |
| `stock_items` | 库存件（一行=一件/批） | style_no/product_name/fineness/**weight(str)**/labor_cost/piece_count/ring_size/gold_price/**status**/inbound_id/transfer_id |
| `transfer_orders` | 转移单 | transfer_no(ZY-,幂等键)/**customer_id+customer_name快照**/status/store_order_no(对方回执)/push_response |
| `customers` | 客户档案 | name/store_base_url/**store_api_key(只写不读)**/supplier_name(本厂在对方ERP的名字)/enabled |
| `users` | 登录账号 | username/password_hash(pbkdf2)/is_admin |
| `style_cache` | 门店款式缓存 | 从门店电子板房拉取，入库下拉自动带 品名/成色/参考工费 |

## 5. API 一览（全部须登录，除 health）

```
POST /api/auth/login              登录 → 令牌(7天)
GET  /api/auth/me                 当前用户+实例配置
GET/POST /api/auth/users          账号列表/新建(admin)
POST /api/inbounds                产品入库(保存即进库存)
GET  /api/inbounds                入库单历史
DELETE /api/inbounds/{id}         删入库单(仅整单在库)
GET  /api/stock?q=&status=        库存查询+三态汇总
POST /api/transfers               建转移单{customer_id,item_ids}(锁定货品)
GET  /api/transfers               转移单列表
POST /api/transfers/{id}/push     推送给客户(幂等,失败留痕可重推)
DELETE /api/transfers/{id}        删草稿(货解锁回在库)
GET  /api/customers               客户列表(Key永不回显,仅报已配/未配)
POST/PUT/DELETE /api/customers    客户增改删(admin;有转移记录只能停用)
GET  /api/styles + POST /sync     款式缓存/从门店同步(可开关)
GET  /api/health                  健康检查(开放,watchdog用)
```

## 6. 与门店(AI-ERP)的对接契约

转移推送 → 对方 `POST {客户.store_base_url}/api/external/pre-inbound`，
Header `X-API-Key: {客户.store_api_key}`：

```jsonc
{
  "factory_order_no": "ZY-20260703-001",   // 幂等键(转移单号)
  "supplier": "梵贝琳工厂",                  // 客户档案里的供应商名,须与对方ERP一字不差
  "order_date": "2026-07-03",
  "pushed_by": "factory-hub",
  "auto_confirm": false,                    // 合作工厂锁死false,门店必须人工过秤确认
  "items": [{
    "style_no": "JZ-A001",                  // 关门店电子板房(确认卡可调款图)
    "product_name": "足金古法戒指",
    "fineness": "足金999",                   // 门店侧走严格白名单,非标成色拒收
    "expected_weight": "5.2300",            // ★字符串传克重!工厂过秤重=门店预报重
    "labor_cost": "12.00", "piece_count": 1,
    "piece_labor_cost": null, "ring_size": "12",
    "gold_price": "560.00",                 // 仅留存,不参与门店任何计算
    "remark": null
  }]
}
```

**门店侧还需实现**（方案见 `AI-ERP2.0/docs/factory-pre-inbound-plan.md`，4 个硬约束已核实）：
① wire 层 Decimal 化；② `expected_weight` 双轨 + confirm 复称卡口（差异必填原因）；
③ `factory_order_no` 唯一索引幂等；④ 成色严格白名单。另加 key↔supplier 绑定（工厂A的 key
只能报"工厂A"的货）。`FACTORY_INTEGRATION` feature flag 默认关。
款式下拉另需门店 `GET /api/external/styles`（仅自家工厂实例开放）。

## 7. 运行与配置

**本机启动**：双击 `start_factory_hub.bat` → http://127.0.0.1:8200
登录账号 `admin`，密码见 `backend/.env` 的 `FACTORY_ADMIN_PASSWORD`。

**backend/.env 关键项**：

| 项 | 说明 |
|----|------|
| `FACTORY_SUPPLIER_NAME` | 本厂在门店 ERP 的供应商名（一字不差） |
| `STORE_BASE_URL` / `STORE_API_KEY` / `STORE_CUSTOMER_NAME` | 首启种子默认客户；日常客户管理在界面 |
| `FACTORY_ADMIN_USER` / `FACTORY_ADMIN_PASSWORD` | 首启种子管理员（密码留空=随机生成写 ADMIN_PASSWORD.txt） |
| `STYLE_SYNC_ENABLED` | 款式同步开关。**外部合作工厂实例必须 false**（防款式库外泄） |
| `AUTO_CONFIRM` | 合作工厂必须 false |
| `FACTORY_HUB_PORT` / `DATABASE_URL` | 实例端口/独立库 |

**两厂部署规划**（阿里云，照 SGW 开通规律：目录+计划任务+watchdog+nginx）：

| 实例 | 端口 | 款式同步 | env 模板 |
|------|------|---------|----------|
| 梵贝琳工厂(自家) | 8201 | 开 | `deploy/fanbeilin.env.example` |
| 华玥珠宝(外部) | 8202 | **关** | `deploy/huayue.env.example` |

## 8. 当前状态（2026-07-03）

| 事项 | 状态 |
|------|------|
| 工厂端 入库/库存/转移/多客户/登录 | ✅ 完成，冒烟测试全过（精度/锁定/幂等/权限） |
| 界面（ERP式导航/加n行/客户管理） | ✅ 完成，本机 8200 运行中 |
| 门店侧接收端（pre-inbound + 4硬约束 + 复称卡口） | 🔨 进行中（侦察已完成，方案已定稿） |
| 本地联调（工厂→门店真实推送） | ⏳ 等门店侧完成 |
| 部署两厂实例上阿里云 + HTTPS | ⏳ 联调通过后 |
| 影子期（工厂推送 vs 门店人工录入 并行比对2-4周） | ⏳ 上线后 |

## 9. 踩坑记录（已修，防再犯）

1. **初始密码带符号**：`@` 在中文输入法下打出全角 `＠`，肉眼无差登录必败 → 初始密码一律纯小写字母+数字。
2. **CSS 压过 hidden 属性**：`.login-mask{display:flex}` 优先级高于 UA 的 `[hidden]{display:none}`，
   登录成功但登录框不消失（曾九次"没反应"实为九次成功）→ 全局加 `[hidden]{display:none!important}`。
3. **PowerShell 改 .env 乱码**：PS5.1 `Get-Content` 按 GBK 读 UTF-8 无 BOM 文件，中文全毁、
   `DATABASE_URL` 被吞进注释 → 配置文件改动一律用编辑器/Write 工具，不用 PS 管道。
4. **SQLite NUMERIC 退化 float**：克重/金额存 TEXT、全程 Decimal，入口字符串传输。

## 10. 后续路线

1. **门店侧接收端**（关键路径）→ 本地联调 → 部署两厂 → 影子期 → 正式切换
2. 款式同步 MVP（工厂当款式源头，翻转电子板房方向）
3. 工厂对账闭环（泛化门店 recon_public 的 token/快照/异议到供应商维度，金料克重+工费双轨分对）
4. 远期：数字护照（passport_id 贯穿设计→工厂→门店→消费者扫码溯源）
