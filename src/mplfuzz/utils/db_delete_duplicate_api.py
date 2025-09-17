import fire

from mplfuzz.db.api_parse_record_table import get_db_cursor


def fun(library_name: str):
    with get_db_cursor() as cur:
        sql = """
        WITH cte AS (
            SELECT 
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY api_name, source
                    ORDER BY id
                ) AS rn
            FROM api
            WHERE library_name = ?
        )
        DELETE FROM api
        WHERE id IN (
            SELECT id FROM cte WHERE rn > 1
        );
        """
        cur.execute(sql, (library_name,))
        cur.connection.commit()


def main():
    fire.Fire(fun)


if __name__ == "__main__":
    main()
