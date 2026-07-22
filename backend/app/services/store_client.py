"""客户(门店)对接客户端。按客户档案(Customer)的地址+Key 调对方 /api/external/* 窄口径端点。
客户未配 Key 时优雅降级：工厂端照常入库/建转移单，推送提示未接通。"""
import httpx

from ..config import settings


def _headers(api_key: str):
    return {"X-API-Key": api_key, "Content-Type": "application/json"}


def fetch_styles(timeout: float = 10.0):
    """拉门店电子板房款式清单（仅自家工厂实例用，走 env 主门店配置）。"""
    if not settings.STORE_API_KEY:
        raise RuntimeError("未配置 STORE_API_KEY（门店端尚未就绪）")
    url = settings.STORE_BASE_URL.rstrip("/") + "/api/external/styles"
    r = httpx.get(url, headers=_headers(settings.STORE_API_KEY),
                  params={"status": "active"}, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    if isinstance(body, dict) and "data" in body:
        return body["data"] or []
    return body or []


def build_payload(transfer, items, customer) -> dict:
    """组装对方 /api/external/pre-inbound 报文（转移单 → 客户预入库）。
    关键映射：工厂过秤 weight → 对方 expected_weight（预报重）。克重/金额以字符串传，防 float。
    供应商名取客户档案里的 supplier_name（本厂在对方 ERP 的名字，一字不差）；ZY 转移单号为幂等键。"""
    return {
        "factory_order_no": transfer.transfer_no,
        "supplier": customer.supplier_name,
        "order_date": (transfer.created_at.strftime("%Y-%m-%d")
                       if getattr(transfer, "created_at", None) else None),
        "pushed_by": settings.PUSHED_BY,
        "auto_confirm": settings.AUTO_CONFIRM,
        "items": [
            {
                "style_no": it.style_no,
                "product_code": it.product_code,    # ★ 工厂发的一码一件码 TF/FF（前缀留空=不发码，码为 null）
                "is_unique": 1 if it.is_unique else 0,   # ★ 一码一件标志：工厂不发码时，门店按此标志自发本店 F 码（称重件不发）
                "product_name": it.product_name,
                "fineness": it.fineness,
                "expected_weight": it.weight,       # ★ 工厂过秤重 → 对方预报重
                "labor_cost": it.labor_cost,
                "piece_count": it.piece_count,
                "piece_labor_cost": it.piece_labor_cost,
                "ring_size": it.ring_size,
                "gold_price": it.gold_price,
                "remark": it.remark,
            }
            for it in items
        ],
    }


def push_pre_inbound(transfer, items, customer, timeout: float = 20.0) -> dict:
    """推送预入库到指定客户。返回统一结构 {ok, ...}。"""
    payload = build_payload(transfer, items, customer)
    api_key = (customer.store_api_key or "").strip()
    if not api_key:
        return {
            "ok": False, "stage": "config",
            "message": f"客户「{customer.name}」尚未配置对接 Key —— 对方系统就绪后，"
                       f"在「客户管理」里填入对方发的 API Key 即可转移。",
            "payload": payload,
        }
    url = customer.store_base_url.rstrip("/") + "/api/external/pre-inbound"
    try:
        r = httpx.post(url, headers=_headers(api_key), json=payload, timeout=timeout)
    except httpx.RequestError as e:
        return {"ok": False, "stage": "network",
                "message": f"连不上客户「{customer.name}」({url})：{e}", "payload": payload}
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}
    if r.status_code >= 400 or (isinstance(body, dict) and body.get("success") is False):
        msg = body.get("message") if isinstance(body, dict) else r.text
        return {"ok": False, "stage": "store", "status_code": r.status_code,
                "message": msg, "body": body, "payload": payload}
    data = body.get("data", {}) if isinstance(body, dict) else {}
    return {"ok": True, "store_order_no": data.get("order_no"),
            "body": body, "payload": payload}


def _get_json(customer, path, params=None, timeout: float = 15.0):
    """按客户档案 GET 对方 /api/external/* 端点，统一返回 {ok, data|reason}。未配Key/网络错静默降级。"""
    api_key = (getattr(customer, "store_api_key", None) or "").strip()
    if not api_key:
        return {"ok": False, "reason": "no_key"}
    url = (customer.store_base_url or "").rstrip("/") + path
    try:
        r = httpx.get(url, headers=_headers(api_key), params=params or {}, timeout=timeout)
    except Exception as e:   # 含 InvalidURL(地址手滑填错) 等非 RequestError 异常,一律降级不炸端点
        return {"ok": False, "reason": f"network: {e}"}
    try:
        body = r.json()
    except Exception:
        return {"ok": False, "reason": "bad_response"}
    if r.status_code >= 400 or (isinstance(body, dict) and body.get("success") is False):
        return {"ok": False, "reason": (body.get("message") if isinstance(body, dict) else r.text) or f"http {r.status_code}"}
    return {"ok": True, "data": (body.get("data") if isinstance(body, dict) else body) or []}


PO_FETCH_LIMIT = 500   # 与门店端上限一致；返回条数<此值=完整清单(可安全判定缺席单已撤)


def fetch_purchase_orders(customer, status: str = "all", timeout: float = 25.0):
    """拉门店订货单(DH,本厂名下)。status=open 只拉未到齐 / all 含已完结已取消。"""
    return _get_json(customer, "/api/external/purchase-orders",
                     {"status": status, "limit": PO_FETCH_LIMIT}, timeout)


def fetch_store_styles(customer, mine: bool = True, timeout: float = 40.0):
    """拉门店电子板房款式(含停产款,工厂侧镜像 status)。mine=True 只拉默认供应商=本厂的款。"""
    params = {"status": "all"}
    if mine:
        params["mine"] = 1
    return _get_json(customer, "/api/external/styles", params, timeout)


def download_image(customer, rel_url, timeout: float = 20.0, max_bytes: int = 12 * 1024 * 1024):
    """下载门店款式图(门店 /api/uploads 匿名静态,无需Key)。返回 {ok, content, ext} / {ok:False, reason}。"""
    if not rel_url:
        return {"ok": False, "reason": "no_url"}
    url = rel_url if str(rel_url).startswith("http") else (customer.store_base_url or "").rstrip("/") + rel_url
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True)
    except Exception as e:
        return {"ok": False, "reason": f"network: {e}"}
    if r.status_code != 200 or not r.content:
        return {"ok": False, "reason": f"http {r.status_code}"}
    if len(r.content) > max_bytes:
        return {"ok": False, "reason": "too_large"}
    ct = (r.headers.get("content-type") or "").lower()
    if "png" in ct:
        ext = ".png"
    elif "webp" in ct:
        ext = ".webp"
    elif "jpeg" in ct or "jpg" in ct:
        ext = ".jpg"
    else:
        tail = str(rel_url).rsplit(".", 1)[-1].lower() if "." in str(rel_url) else ""
        ext = "." + ("jpg" if tail in ("", "jpeg") else tail) if tail in ("", "jpg", "jpeg", "png", "webp") else ".jpg"
    return {"ok": True, "content": r.content, "ext": ext}


def fetch_inbound_status(customer, factory_order_no, timeout: float = 12.0):
    """回执闭环：查门店该预入库单(factory_order_no)当前状态。
    返回 {ok, found, status, store_order_no}。未配 key/连不上/门店未就绪 → ok False(静默,不报错)。"""
    api_key = (getattr(customer, "store_api_key", None) or "").strip()
    if not api_key:
        return {"ok": False, "reason": "no_key"}
    url = customer.store_base_url.rstrip("/") + "/api/external/inbound-status"
    try:
        r = httpx.get(url, headers=_headers(api_key),
                      params={"factory_order_no": factory_order_no}, timeout=timeout)
    except httpx.RequestError as e:
        return {"ok": False, "reason": f"network: {e}"}
    try:
        body = r.json()
    except Exception:
        return {"ok": False, "reason": "bad_response"}
    if r.status_code >= 400 or (isinstance(body, dict) and body.get("success") is False):
        return {"ok": False, "reason": (body.get("message") if isinstance(body, dict) else r.text)}
    data = body.get("data", {}) if isinstance(body, dict) else {}
    return {"ok": True, "found": data.get("found"), "status": data.get("status"),
            "store_order_no": data.get("order_no")}
