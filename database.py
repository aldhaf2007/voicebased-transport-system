import mysql.connector
from neo4j import GraphDatabase
import os

# ==========================================
# 1. DATABASE CONFIGURATION CONFIG
# ==========================================
# Modify these credentials to match your local installations
MYSQL_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "",
    "database": "transport_db",
}

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "0362007Ag")


# ==========================================
# 2. DATABASE CONNECTORS
# ==========================================
def get_mysql_connection():
    """Establishes and returns a connection to the MySQL database."""
    try:
        connection = mysql.connector.connect(**MYSQL_CONFIG)
        return connection
    except mysql.connector.Error as err:
        print(f"❌ MySQL Connection Error: {err}")
        return None


def get_neo4j_driver():
    """Establishes and returns a driver instance for the Neo4j Graph database."""
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        return driver
    except Exception as e:
        print(f"❌ Neo4j Connection Error: {e}")
        return None


# ==========================================
# 3. HYBRID DATA QUERY LAYER
# ==========================================
def query_transport_system(source, destination):
    """
    Executes a polyglot query across Neo4j and MySQL.
    1. Fetches route IDs from Neo4j topology.
    2. Fetches real-time schedules/seats from MySQL using those route IDs.
    """
    # Normalize input text (lowercasing/stripping whitespace)
    src = source.strip().title()
    dest = destination.strip().title()

    print(f"\n🔍 Searching routes from '{src}' to '{dest}'...")

    # --- PHASE 1: NEO4J GRAPH TRAVERSAL ---
    neo4j_driver = get_neo4j_driver()
    if not neo4j_driver:
        return {"error": "Graph database connection failed."}

    route_ids = []

    # Cypher query to match cities and pull the unique route identification code
    cypher_query = """
    MATCH (start:Station {name: $src})-[relationships:CONNECTS_TO*]->(end:Station {name: $dest})
    UNWIND relationships AS r
    RETURN r.route_id AS route_id
    """

    try:
        with neo4j_driver.session() as session:
            result = session.run(cypher_query, src=src, dest=dest)
            route_ids = [record["route_id"] for record in result]
    except Exception as e:
        print(f"❌ Neo4j Query Failure: {e}")
        return {"error": "Graph routing query execution failed."}
    finally:
        neo4j_driver.close()

    print(f"🔗 Neo4j matching route IDs found: {route_ids}")

    if not route_ids:
        return {
            "status": "No routes found",
            "origin": src,
            "destination": dest,
            "schedules": []
        }

    # --- PHASE 2: MYSQL RELATIONAL LOOKUP ---
    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        return {"error": "Relational database connection failed."}

    final_schedule_results = []
    cursor = mysql_conn.cursor(dictionary=True)  # Return rows as python dictionaries

    try:
        # Construct placeholders for SQL IN clause dynamically (%s, %s, ...)
        format_strings = ", ".join(["%s"] * len(route_ids))

        # SQL query to grab schedule, price, capacity details
        sql_query = f"""
        SELECT 
            s.route_id, 
            t.type AS transport_type, 
            s.departure_time, 
            s.arrival_time,  
            s.available_seats 
        FROM Schedules s
        INNER JOIN Transport_Details t ON s.transport_id = t.transport_id
        WHERE s.route_id IN ({format_strings}) AND s.available_seats > 0
        """

        cursor.execute(sql_query, tuple(route_ids))
        final_schedule_results = cursor.fetchall()

        # Convert timedelta and time objects to string for JSON compatibility
        import datetime
        for row in final_schedule_results:
            for key, val in row.items():
                if isinstance(val, (datetime.timedelta, datetime.time)):
                    row[key] = str(val)

    except mysql.connector.Error as err:
        print(f"❌ MySQL Query Failure: {err}")
        return {"error": "Relational schedule query execution failed."}
    finally:
        cursor.close()
        mysql_conn.close()

    return {
        "status": "Success",
        "origin": src,
        "destination": dest,
        "schedules": final_schedule_results,
    }


# ==========================================
# 4. ISOLATED LOCAL TEST BENCH
# ==========================================
if __name__ == "__main__":
    # Test your connection and querying directly before creating Flask files
    # Change 'Mumbai' and 'Pune' to values currently inside your databases
    test_result = query_transport_system("New Delhi", "Bangalore")

    print("\n--- TEST RUN RESULTS ---")
    import json

    print(json.dumps(test_result, indent=4, default=str))
