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
