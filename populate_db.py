import mysql.connector
from neo4j import GraphDatabase
import sys

# Database configs matching database.py configuration
MYSQL_CONFIG_ROOT = {
    "host": "localhost",
    "user": "root",
    "password": "",
}
DB_NAME = "transport_db"

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "0362007Ag"


def populate_mysql():
    print("----------------------------------------")
    print("🤖 Seeding MySQL Database...")
    print("----------------------------------------")
    
    # 1. Connect to MySQL Server (without database to create it if not exists)
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG_ROOT)
        cursor = conn.cursor()
    except mysql.connector.Error as err:
        print(f"❌ Failed to connect to MySQL server: {err}")
        print("   Please ensure MySQL is running locally on port 3306 with the correct credentials.")
        return False

    # 2. Create database
    try:
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}")
        print(f"✓ Database '{DB_NAME}' verified/created.")
    except mysql.connector.Error as err:
        print(f"❌ Failed to create database '{DB_NAME}': {err}")
        conn.close()
        return False
    
    conn.close()

    # 3. Connect to the specific database
    try:
        conn = mysql.connector.connect(database=DB_NAME, **MYSQL_CONFIG_ROOT)
        cursor = conn.cursor()
    except mysql.connector.Error as err:
        print(f"❌ Failed to connect to database '{DB_NAME}': {err}")
        return False

    # 4. Create Tables
    try:
        # Drop tables in correct order if they exist for clean seeding
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
        cursor.execute("DROP TABLE IF EXISTS Schedules;")
        cursor.execute("DROP TABLE IF EXISTS Transport_Details;")
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")

        # Create Transport_Details table
        cursor.execute("""
            CREATE TABLE Transport_Details (
                transport_id INT PRIMARY KEY AUTO_INCREMENT,
                type VARCHAR(50) NOT NULL
            );
        """)
        print("✓ Created table 'Transport_Details'.")

        # Create Schedules table
        cursor.execute("""
            CREATE TABLE Schedules (
                schedule_id INT PRIMARY KEY AUTO_INCREMENT,
                route_id INT NOT NULL,
                transport_id INT,
                departure_time TIME NOT NULL,
                arrival_time TIME NOT NULL,
                available_seats INT NOT NULL,
                FOREIGN KEY (transport_id) REFERENCES Transport_Details(transport_id)
            );
        """)
        print("✓ Created table 'Schedules'.")
    except mysql.connector.Error as err:
        print(f"❌ Failed to create tables: {err}")
        conn.close()
        return False

    # 5. Populate Transport Details
    try:
        # We insert 3 transport types
        cursor.execute("INSERT INTO Transport_Details (transport_id, type) VALUES (1, 'Flight'), (2, 'Train'), (3, 'Bus');")
        conn.commit()
        print("✓ Populated transport types: Flight (1), Train (2), Bus (3).")
    except mysql.connector.Error as err:
        print(f"❌ Failed to insert transport details: {err}")
        conn.close()
        return False

    # 6. Populate Schedules
    # Route IDs mapping:
    # 1: New Delhi -> Mumbai
    # 2: Mumbai -> Bangalore
    # 3: New Delhi -> Bangalore
    # 4: Pune -> Bangalore
    # 5: Mumbai -> Pune
    # 6: New Delhi -> Pune
    schedules_data = [
        # Route 1: New Delhi -> Mumbai
        (1, 1, "08:00:00", "10:15:00", 45),   # Flight (1)
        (1, 2, "16:30:00", "08:30:00", 120),  # Train (2)
        (1, 3, "19:00:00", "14:30:00", 25),   # Bus (3)
        
        # Route 2: Mumbai -> Bangalore
        (2, 1, "11:30:00", "13:10:00", 30),   # Flight (1)
        (2, 2, "20:15:00", "14:45:00", 90),   # Train (2)
        (2, 3, "21:00:00", "07:00:00", 15),   # Bus (3)

        # Route 3: New Delhi -> Bangalore
        (3, 1, "06:15:00", "09:00:00", 25),   # Flight (1)
        (3, 2, "14:10:00", "18:40:00", 75),   # Train (2)

        # Route 4: Pune -> Bangalore
        (4, 3, "22:30:00", "08:15:00", 20),   # Bus (3)
        (4, 2, "15:45:00", "06:00:00", 85),   # Train (2)

        # Route 5: Mumbai -> Pune
        (5, 2, "17:10:00", "20:25:00", 150),  # Train (2)
        (5, 3, "07:00:00", "10:30:00", 35),   # Bus (3)

        # Route 6: New Delhi -> Pune
        (6, 1, "14:00:00", "16:15:00", 50),   # Flight (1)
        (6, 2, "21:30:00", "23:45:00", 60),   # Train (2)

        # Route 7: Mumbai -> New Delhi (Return leg)
        (7, 1, "11:00:00", "13:15:00", 50),   # Flight (1)
        (7, 2, "18:00:00", "10:00:00", 110),  # Train (2)
        (7, 3, "20:00:00", "15:30:00", 30),   # Bus (3)

        # Route 8: Bangalore -> Mumbai (Return leg)
        (8, 1, "14:30:00", "16:10:00", 40),   # Flight (1)
        (8, 2, "08:00:00", "02:30:00", 80),   # Train (2)
        (8, 3, "19:30:00", "05:30:00", 20),   # Bus (3)

        # Route 9: Bangalore -> New Delhi (Return leg)
        (9, 1, "10:00:00", "12:45:00", 35),   # Flight (1)
        (9, 2, "19:00:00", "23:30:00", 95),   # Train (2)

        # Route 10: Bangalore -> Pune (Return leg)
        (10, 3, "09:00:00", "18:45:00", 25),  # Bus (3)
        (10, 2, "21:00:00", "11:15:00", 70),  # Train (2)

        # Route 11: Pune -> Mumbai (Return leg)
        (11, 2, "06:00:00", "09:15:00", 120), # Train (2)
        (11, 3, "13:00:00", "16:30:00", 40),  # Bus (3)

        # Route 12: Pune -> New Delhi (Return leg)
        (12, 1, "17:30:00", "19:45:00", 45),  # Flight (1)
        (12, 2, "09:00:00", "11:15:00", 75),  # Train (2)
    ]

    try:
        insert_query = """
            INSERT INTO Schedules (route_id, transport_id, departure_time, arrival_time, available_seats)
            VALUES (%s, %s, %s, %s, %s);
        """
        cursor.executemany(insert_query, schedules_data)
        conn.commit()
        print(f"✓ Successfully seeded {cursor.rowcount} schedules into MySQL.")
    except mysql.connector.Error as err:
        print(f"❌ Failed to insert schedules: {err}")
        conn.close()
        return False

    cursor.close()
    conn.close()
    return True


def populate_neo4j():
    print("\n----------------------------------------")
    print("🕸️ Seeding Neo4j Graph Database...")
    print("----------------------------------------")

    # 1. Establish Driver connection
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        # Verify connection by running a quick session check
        with driver.session() as session:
            session.run("RETURN 1")
    except Exception as e:
        print(f"❌ Failed to connect to Neo4j database: {e}")
        print("   Please ensure Neo4j is running locally on port 7687 with the correct credentials.")
        return False

    # 2. Clear previous nodes/relationships and rebuild station topology
    try:
        with driver.session() as session:
            # Clear all relationships and nodes
            print("✓ Clearing old nodes/relationships...")
            session.run("MATCH (n) DETACH DELETE n")

            # Create Station Nodes
            stations = ["New Delhi", "Mumbai", "Bangalore", "Pune"]
            for station in stations:
                session.run("CREATE (:Station {name: $name})", name=station)
            print(f"✓ Created {len(stations)} Station nodes: {', '.join(stations)}.")

            # Create CONNECTS_TO relationships mapping to MySQL Route IDs
            connections = [
                ("New Delhi", "Mumbai", 1),
                ("Mumbai", "Bangalore", 2),
                ("New Delhi", "Bangalore", 3),
                ("Pune", "Bangalore", 4),
                ("Mumbai", "Pune", 5),
                ("New Delhi", "Pune", 6),
                ("Mumbai", "New Delhi", 7),
                ("Bangalore", "Mumbai", 8),
                ("Bangalore", "New Delhi", 9),
                ("Bangalore", "Pune", 10),
                ("Pune", "Mumbai", 11),
                ("Pune", "New Delhi", 12),
            ]

            cypher_rel_query = """
            MATCH (start:Station {name: $start_name})
            MATCH (end:Station {name: $end_name})
            CREATE (start)-[:CONNECTS_TO {route_id: $route_id}]->(end)
            """

            for start, end, r_id in connections:
                session.run(cypher_rel_query, start_name=start, end_name=end, route_id=r_id)
            
            print(f"✓ Established directed CONNECTS_TO relationships (Route IDs: 1 to 12).")

    except Exception as e:
        print(f"❌ Failed to seed Neo4j graph nodes and links: {e}")
        driver.close()
        return False
    finally:
        driver.close()

    return True


if __name__ == "__main__":
    mysql_ok = populate_mysql()
    neo4j_ok = populate_neo4j()
    
    if mysql_ok and neo4j_ok:
        print("\n🎉 Polyglot database seeding completed successfully!")
        sys.exit(0)
    else:
        print("\n⚠️ Database seeding completed with errors. Please check the logs above.")
        sys.exit(1)
