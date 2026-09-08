from app.settings import settings
from app.store import POCStore


if __name__ == "__main__":
    store = POCStore(settings.database_url)
    store.seed_demo_data()
    print("已初始化 POC 数据。")
