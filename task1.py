import json
import re
import mysql.connector
from mysql.connector import Error


RAW_TEXT = """
1. Индивидуальный заказ Крем - 60 дней Кол-во: 2
- Прогестерон - 50 мг
- Эстриол - 1 мг
2. Индивидуальный заказ Капсулы - 30 дней Колво: 3
- NАцетилцистеин  200 мг
3. Индивидуальный заказ Капли - 30 дней Колво: 1
- N-Ацетилцистеин - 50 мг
""".strip()


def normalize_component_name(raw_name: str) -> str:
    cleaned = re.sub(r"\s+", " ", raw_name.strip())
    if re.fullmatch(r"[NН]-?\s*Ацетилцистеин", cleaned, flags=re.IGNORECASE):
        return "N-Ацетилцистеин"
    return cleaned


def parse_orders(text: str) -> list[dict]:
    orders = []
    current_order = None

    header_pattern = re.compile(
        r"^\d+\.\s*Индивидуальный заказ\s+(.+?)\s*-\s*(\d+\s*дн\w*)\s+Кол-?во:\s*(\d+)\s*$",
        flags=re.IGNORECASE,
    )
    item_pattern = re.compile(
        r"^-\s*(.+?)\s*-?\s*(\d+(?:[.,]\d+)?)\s*мг\s*$",
        flags=re.IGNORECASE,
    )

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        header_match = header_pattern.match(line)
        if header_match:
            if current_order:
                orders.append(current_order)

            form = re.sub(r"\s+", " ", header_match.group(1).strip())
            duration = re.sub(r"\s+", " ", header_match.group(2).strip())
            quantity = int(header_match.group(3))

            current_order = {
                "form": form,
                "duration": duration,
                "quantity": quantity,
                "components": [],
            }
            continue

        item_match = item_pattern.match(line)
        if item_match and current_order is not None:
            comp_name = normalize_component_name(item_match.group(1))
            dosage_str = item_match.group(2).replace(",", ".")
            dosage_mg = float(dosage_str)
            if dosage_mg.is_integer():
                dosage_mg = int(dosage_mg)

            current_order["components"].append({
                "name": comp_name,
                "dosage_mg": dosage_mg,
            })

    if current_order:
        orders.append(current_order)

    return orders


def calculate_total_consumption(orders: list[dict]) -> dict[str, int | float]:
    totals: dict[str, int | float] = {}

    for order in orders:
        qty = order["quantity"]
        for comp in order["components"]:
            name = comp["name"]
            total_mg = comp["dosage_mg"] * qty
            totals[name] = totals.get(name, 0) + total_mg

    for name, val in totals.items():
        if isinstance(val, float) and val.is_integer():
            totals[name] = int(val)

    return totals


def save_to_mysql(orders: list[dict], db_config: dict) -> None:
    conn = None
    try:
        conn = mysql.connector.connect(
            host=db_config["host"],
            port=db_config["port"],
            user=db_config["user"],
            password=db_config["password"],
        )
        cursor = conn.cursor()

        db_name = db_config["database"]
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        cursor.execute(f"USE `{db_name}`")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INT AUTO_INCREMENT PRIMARY KEY,
                form VARCHAR(100) NOT NULL,
                duration VARCHAR(50) NOT NULL,
                quantity INT NOT NULL,
                raw_json JSON NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS order_components (
                id INT AUTO_INCREMENT PRIMARY KEY,
                order_id INT NOT NULL,
                component_name VARCHAR(255) NOT NULL,
                dosage_mg DECIMAL(10, 2) NOT NULL,
                total_mg DECIMAL(12, 2) NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
            )
        """)

        for order in orders:
            cursor.execute(
                "INSERT INTO orders (form, duration, quantity, raw_json) VALUES (%s, %s, %s, %s)",
                (
                    order["form"],
                    order["duration"],
                    order["quantity"],
                    json.dumps(order, ensure_ascii=False),
                ),
            )
            order_id = cursor.lastrowid

            for comp in order["components"]:
                total_mg = comp["dosage_mg"] * order["quantity"]
                cursor.execute(
                    """
                    INSERT INTO order_components (order_id, component_name, dosage_mg, total_mg)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (order_id, comp["name"], comp["dosage_mg"], total_mg),
                )

        conn.commit()
        print("\n[OK] Данные успешно сохранены в MySQL.")
    except Error as e:
        print(f"\n[ВНИМАНИЕ] Не удалось сохранить в MySQL: {e}")
        print("Проверьте, запущен ли MySQL и верны ли логин/пароль в настройках.")
    finally:
        if conn and conn.is_connected():
            conn.close()


def main(db_config: dict | None = None):
    orders = parse_orders(RAW_TEXT)
    orders_json = json.dumps(orders, ensure_ascii=False, indent=2)

    print("=== Разобранные заказы (JSON) ===")
    print(orders_json)

    totals = calculate_total_consumption(orders)
    print("\n=== Общий расход компонентов ===")
    for name, total_mg in totals.items():
        print(f"- {name}: {total_mg} мг")

    if db_config is None:
        db_config = {
            "host": "localhost",
            "port": 3306,
            "user": "root",
            "password": "Adiko_05",
            "database": "pharmacy_test",
        }

    save_to_mysql(orders, db_config)


if __name__ == "__main__":
    main(
        db_config={
            "host": "localhost",
            "port": 3306,
            "user": "root",
            "password": "Adiko_05",  # пароль
            "database": "pharmacy_test",
        }
    )