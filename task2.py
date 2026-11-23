import json
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET
import mysql.connector
from mysql.connector import Error
import requests


NBK_RATES_URL = "https://nationalbank.kz/rss/rates_all.xml"
CACHE_FILE = Path("last_usd_rate.json")

PRODUCTS_USD = [
    ("Сыворотка с ретинолом", 24.5),
    ("Дерматологический крем", 13.2),
    ("Комплекс витамина D3", 9.8),
    ("Очищающий гель", 17.4),
]

DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "Adiko_05",  # <-- пароль
    "database": "pharmacy_test",
}


def fetch_usd_rate_from_nbk(timeout: int = 10) -> float:
    """Запрашивает текущий курс USD к KZT с сайта Нацбанка РК."""
    response = requests.get(NBK_RATES_URL, timeout=timeout)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip().upper()
        description = (item.findtext("description") or "").strip()
        quant_text = (item.findtext("quant") or "1").strip().replace(",", ".")

        if title == "USD":
            rate = float(description.replace(",", "."))
            quant = float(quant_text) if quant_text else 1.0
            return rate / quant

    raise ValueError("Курс USD не найден в ответе Нацбанка РК")


def save_cached_rate(rate: float, source: str = "NBK") -> None:
    """Сохраняет последний актуальный курc"""
    payload = {
        "usd_to_kzt": rate,
        "source": source,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_cached_rate() -> dict | None:
    """Загружает последний сохранённый курс из файла."""
    if not CACHE_FILE.exists():
        return None
    return json.loads(CACHE_FILE.read_text(encoding="utf-8"))


def get_usd_rate() -> tuple[float, str]:
    """При ошибке сети берёт сохранённый курс."""
    try:
        rate = fetch_usd_rate_from_nbk()
        save_cached_rate(rate, source="Нацбанк РК (онлайн)")
        return rate, "Нацбанк РК (онлайн)"
    except Exception as exc:
        cached = load_cached_rate()
        if cached and "usd_to_kzt" in cached:
            info = (
                f"Резервный файл (ошибка: {exc}; "
                f"сохранён {cached.get('updated_at', 'ранее')})"
            )
            return float(cached["usd_to_kzt"]), info
        raise RuntimeError(
            f"Не удалось получить курс с сайта ({exc}) и нет сохранённого курса в {CACHE_FILE}."
        ) from exc


def round_to_ten(amount: float) -> int:
    """Округляет сумму"""
    return int(round(amount / 10.0) * 10)


def convert_prices(products: list[tuple[str, float]], usd_rate: float) -> list[dict]:
    """Переводит цены из USD в KZT с округлением"""
    result = []
    for name, price_usd in products:
        exact_kzt = price_usd * usd_rate
        rounded_kzt = round_to_ten(exact_kzt)
        result.append({
            "product": name,
            "price_usd": price_usd,
            "exact_kzt": round(exact_kzt, 2),
            "price_kzt": rounded_kzt,
        })
    return result


def save_conversions_to_mysql(
    converted_rows: list[dict],
    usd_rate: float,
    source: str,
    db_config: dict,
) -> None:
    """Создаёт таблицу и сохраняет результаты пересчёта в MySQL."""
    conn = None
    try:
        conn = mysql.connector.connect(
            host=db_config["host"],
            port=db_config["port"],
            user=db_config["user"],
            password=db_config["password"],
            database=db_config["database"],
        )
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usd_price_conversions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                product_name VARCHAR(255) NOT NULL,
                price_usd DECIMAL(10, 2) NOT NULL,
                usd_rate DECIMAL(10, 4) NOT NULL,
                price_kzt INT NOT NULL,
                rate_source VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        sql = """
            INSERT INTO usd_price_conversions
                (product_name, price_usd, usd_rate, price_kzt, rate_source)
            VALUES (%s, %s, %s, %s, %s)
        """
        values = [
            (row["product"], row["price_usd"], round(usd_rate, 4), row["price_kzt"], source)
            for row in converted_rows
        ]
        cursor.executemany(sql, values)
        conn.commit()
        print(f"\n[OK] Сохранено строк в usd_price_conversions: {cursor.rowcount}")
    except Error as e:
        print(f"\n[ВНИМАНИЕ] Ошибка записи в MySQL: {e}")
    finally:
        if conn and conn.is_connected():
            conn.close()


def main():
    usd_rate, source = get_usd_rate()
    print(f"Курс USD: {usd_rate:.2f} ₸ | Источник: {source}\n")

    converted = convert_prices(PRODUCTS_USD, usd_rate)
    for row in converted:
        print(
            f"{row['product']} — ${row['price_usd']:.2f} → "
            f"{row['price_kzt']} ₸ (точно: {row['exact_kzt']} ₸)"
        )

    save_conversions_to_mysql(converted, usd_rate, source, DB_CONFIG)


if __name__ == "__main__":
    main()