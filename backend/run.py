"""启动入口：读 .env 的端口，起 uvicorn。"""
import uvicorn

from app.config import settings

if __name__ == "__main__":
    print(f"FactoryHub 启动中  →  http://127.0.0.1:{settings.PORT}")
    print(f"门店地址: {settings.STORE_BASE_URL}  | key已配: {bool(settings.STORE_API_KEY)}")
    uvicorn.run("app.main:app", host="127.0.0.1", port=settings.PORT, reload=False)
