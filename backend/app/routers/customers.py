"""客户档案管理：查看人人可用（供转移下拉，Key 永不回显）；增改删仅 admin。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Customer, TransferOrder
from ..schemas import CustomerIn, CustomerUpdate
from ..security import require_auth, require_admin

router = APIRouter(prefix="/api/customers", tags=["customers"],
                   dependencies=[Depends(require_auth)])


def _dict(c: Customer) -> dict:
    return {
        "id": c.id, "name": c.name, "store_base_url": c.store_base_url,
        "supplier_name": c.supplier_name, "enabled": bool(c.enabled),
        "code_prefix": c.code_prefix,                              # 工厂发码前缀 TF/FF（空=不发码）
        "key_configured": bool((c.store_api_key or "").strip()),   # 只报有没有，绝不回显 Key
        "remark": c.remark,
    }


@router.get("")
def list_customers(db: Session = Depends(get_db)):
    rows = db.query(Customer).order_by(Customer.id).all()
    return {"success": True, "data": [_dict(c) for c in rows]}


@router.post("")
def create_customer(data: CustomerIn, _: dict = Depends(require_admin), db: Session = Depends(get_db)):
    if db.query(Customer).filter(Customer.name == data.name).first():
        raise HTTPException(409, f"客户「{data.name}」已存在")
    c = Customer(name=data.name, store_base_url=data.store_base_url.rstrip("/"),
                 supplier_name=data.supplier_name,
                 store_api_key=(data.store_api_key or "").strip() or None,
                 code_prefix=data.code_prefix,
                 remark=data.remark, enabled=1)
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"success": True, "data": _dict(c)}


@router.put("/{cid}")
def update_customer(cid: int, data: CustomerUpdate, _: dict = Depends(require_admin),
                    db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == cid).first()
    if not c:
        raise HTTPException(404, "客户不存在")
    if data.name is not None and data.name.strip():
        dup = db.query(Customer).filter(Customer.name == data.name.strip(), Customer.id != cid).first()
        if dup:
            raise HTTPException(409, f"客户「{data.name}」已存在")
        c.name = data.name.strip()
    if data.store_base_url is not None and data.store_base_url.strip():
        c.store_base_url = data.store_base_url.strip().rstrip("/")
    if data.supplier_name is not None and data.supplier_name.strip():
        c.supplier_name = data.supplier_name.strip()
    if data.store_api_key is not None and data.store_api_key.strip():
        c.store_api_key = data.store_api_key.strip()   # 传非空才覆盖
    if data.code_prefix is not None:                   # 传空串=清除前缀、传值=设为大写、None=不改
        c.code_prefix = data.code_prefix.strip().upper() or None
    if data.enabled is not None:
        c.enabled = 1 if data.enabled else 0
    if data.remark is not None:
        c.remark = data.remark
    db.commit()
    db.refresh(c)
    return {"success": True, "data": _dict(c)}


@router.delete("/{cid}")
def delete_customer(cid: int, _: dict = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == cid).first()
    if not c:
        raise HTTPException(404, "客户不存在")
    used = db.query(TransferOrder.id).filter(TransferOrder.customer_id == cid).first()
    if used:
        raise HTTPException(400, "该客户已有转移单记录，不能删除（可改为停用）")
    db.delete(c)
    db.commit()
    return {"success": True}
