import importlib
import fire

from mplfuzz.db.base import get_db_cursor

def is_importable(package_path: str):
    """尝试导入模块，返回是否成功"""
    try:
        tokens = package_path.split('.')
        obj = importlib.import_module(tokens[0])
        for token in tokens[1:]:
            obj = getattr(obj, token)
        return True
    except Exception:
        return False

def cleanup_invalid_api_records():
    # 获取数据库连接游标
    with get_db_cursor() as cursor:
        # 查询所有 api_name
        cursor.execute("SELECT id, api_name FROM api")
        records = cursor.fetchall()

        invalid_ids = []

        for record_id, api_name in records:
            if not is_importable(api_name):
                invalid_ids.append(record_id)
                print(f"Invalid API: {api_name} (ID={record_id})")

        # 删除所有无效的记录
        if invalid_ids:
            placeholders = ','.join('?' * len(invalid_ids))
            cursor.execute(f"DELETE FROM api WHERE id IN ({placeholders})", invalid_ids)
            print(f"Deleted {len(invalid_ids)} invalid records.")
        else:
            print("No invalid records found.")

def main():
    fire.Fire(cleanup_invalid_api_records)

if __name__ == "__main__":
    main()