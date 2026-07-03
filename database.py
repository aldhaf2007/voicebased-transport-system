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
    1. Fetches direct or multi-hop path configurations from Neo4j topology.
    2. If a direct path exists, returns schedules for that direct route.
    3. If no direct path exists but multi-hop paths are found, returns transit path details.
    """
    src = source.strip().title()
    dest = destination.strip().title()

    print(f"\n🔍 Searching routes from '{src}' to '{dest}'...")

    neo4j_driver = get_neo4j_driver()
    if not neo4j_driver:
        return {"error": "Graph database connection failed."}

    # Find paths from start to end (up to 2 hops)
    cypher_query = """
    MATCH p = (start:Station {name: $src})-[r:CONNECTS_TO*1..2]->(end:Station {name: $dest})
    RETURN [n in nodes(p) | n.name] AS stations, [rel in relationships(p) | rel.route_id] AS route_ids
    """
    
    paths = []
    try:
        with neo4j_driver.session() as session:
            result = session.run(cypher_query, src=src, dest=dest)
            for record in result:
                paths.append({
                    "stations": record["stations"],
                    "route_ids": record["route_ids"]
                })
    except Exception as e:
        print(f"❌ Neo4j Query Failure: {e}")
        return {"error": "Graph routing query execution failed."}
    finally:
        neo4j_driver.close()

    if not paths:
        return {
            "status": "No routes found",
            "origin": src,
            "destination": dest,
            "schedules": [],
            "is_transit": False
        }

    # Separate direct paths (1 hop) and transit paths (2 hops)
    direct_paths = [p for p in paths if len(p["route_ids"]) == 1]
    transit_paths = [p for p in paths if len(p["route_ids"]) > 1]

    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        return {"error": "Relational database connection failed."}
    cursor = mysql_conn.cursor(dictionary=True)

    import datetime
    def format_row_times(row):
        for key, val in row.items():
            if isinstance(val, (datetime.timedelta, datetime.time)):
                row[key] = str(val)
        return row

    try:
        # Case A: Direct routes are available
        if direct_paths:
            route_ids = [p["route_ids"][0] for p in direct_paths]
            format_strings = ", ".join(["%s"] * len(route_ids))
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
            rows = cursor.fetchall()
            schedules = [format_row_times(row) for row in rows]
            
            return {
                "status": "Success",
                "origin": src,
                "destination": dest,
                "schedules": schedules,
                "is_transit": False
            }

        # Case B: No direct route, but multi-hop transit paths exist
        resolved_transit_paths = []
        for path in transit_paths:
            legs_data = []
            valid_path = True
            
            # Fetch schedules for each leg
            for i in range(len(path["route_ids"])):
                r_id = path["route_ids"][i]
                leg_src = path["stations"][i]
                leg_dest = path["stations"][i+1]
                
                sql_query = """
                SELECT 
                    s.route_id, 
                    t.type AS transport_type, 
                    s.departure_time, 
                    s.arrival_time,  
                    s.available_seats 
                FROM Schedules s
                INNER JOIN Transport_Details t ON s.transport_id = t.transport_id
                WHERE s.route_id = %s AND s.available_seats > 0
                """
                cursor.execute(sql_query, (r_id,))
                rows = cursor.fetchall()
                schedules = [format_row_times(row) for row in rows]
                
                if not schedules:
                    # If any leg has no schedules, the whole path is invalid
                    valid_path = False
                    break
                    
                legs_data.append({
                    "source": leg_src,
                    "destination": leg_dest,
                    "route_id": r_id,
                    "schedules": schedules
                })
                
            if valid_path:
                resolved_transit_paths.append({
                    "legs": legs_data
                })

        if resolved_transit_paths:
            return {
                "status": "Success",
                "origin": src,
                "destination": dest,
                "schedules": [],
                "is_transit": True,
                "transit_paths": resolved_transit_paths
            }
        else:
            return {
                "status": "No routes found",
                "origin": src,
                "destination": dest,
                "schedules": [],
                "is_transit": False
            }

    except mysql.connector.Error as err:
        print(f"❌ MySQL Query Failure: {err}")
        return {"error": "Relational schedule query execution failed."}
    finally:
        cursor.close()
        mysql_conn.close()


# ==========================================
# 4. ADMIN DATA MANAGEMENT FUNCTIONS (CRUD)
# ==========================================

def get_all_stations():
    """Fetches all stations from Neo4j."""
    neo4j_driver = get_neo4j_driver()
    if not neo4j_driver:
        return []
    stations = []
    cypher_query = "MATCH (s:Station) RETURN s.name AS name ORDER BY s.name"
    try:
        with neo4j_driver.session() as session:
            result = session.run(cypher_query)
            stations = [record["name"] for record in result]
    except Exception as e:
        print(f"❌ Neo4j get_all_stations error: {e}")
    finally:
        neo4j_driver.close()
    return stations


def get_all_routes():
    """Fetches all routes (connections) from Neo4j."""
    neo4j_driver = get_neo4j_driver()
    if not neo4j_driver:
        return []
    routes = []
    cypher_query = """
    MATCH (start:Station)-[r:CONNECTS_TO]->(end:Station)
    RETURN r.route_id AS route_id, start.name AS source, end.name AS destination
    ORDER BY r.route_id
    """
    try:
        with neo4j_driver.session() as session:
            result = session.run(cypher_query)
            routes = [{
                "route_id": record["route_id"],
                "source": record["source"],
                "destination": record["destination"]
            } for record in result]
    except Exception as e:
        print(f"❌ Neo4j get_all_routes error: {e}")
    finally:
        neo4j_driver.close()
    return routes


def get_all_schedules():
    """Fetches all schedules from MySQL and maps them to Neo4j route stations."""
    # 1. Fetch routes map from Neo4j
    routes = get_all_routes()
    routes_map = {r["route_id"]: (r["source"], r["destination"]) for r in routes}

    # 2. Fetch MySQL schedules
    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        return []
    schedules = []
    cursor = mysql_conn.cursor(dictionary=True)
    
    sql_query = """
    SELECT 
        s.schedule_id,
        s.route_id, 
        t.type AS transport_type, 
        s.departure_time, 
        s.arrival_time,  
        s.available_seats 
    FROM Schedules s
    INNER JOIN Transport_Details t ON s.transport_id = t.transport_id
    ORDER BY s.schedule_id
    """
    try:
        cursor.execute(sql_query)
        rows = cursor.fetchall()
        
        import datetime
        for row in rows:
            # Join with Neo4j route mapping
            route_id = row["route_id"]
            source, destination = routes_map.get(route_id, ("Unknown", "Unknown"))
            
            # Format times
            for key in ["departure_time", "arrival_time"]:
                val = row[key]
                if isinstance(val, (datetime.timedelta, datetime.time)):
                    row[key] = str(val)

            schedules.append({
                "schedule_id": row["schedule_id"],
                "route_id": route_id,
                "source": source,
                "destination": destination,
                "transport_type": row["transport_type"],
                "departure_time": row["departure_time"],
                "arrival_time": row["arrival_time"],
                "available_seats": row["available_seats"]
            })
    except Exception as e:
        print(f"❌ MySQL get_all_schedules error: {e}")
    finally:
        cursor.close()
        mysql_conn.close()
    return schedules


def add_station(name):
    """Creates a new Station node in Neo4j."""
    name = name.strip()
    if not name:
        return False, "Station name cannot be empty."
    neo4j_driver = get_neo4j_driver()
    if not neo4j_driver:
        return False, "Neo4j connection failed."
    try:
        # Check if station already exists
        check_query = "MATCH (s:Station {name: $name}) RETURN count(s) AS cnt"
        create_query = "CREATE (:Station {name: $name})"
        with neo4j_driver.session() as session:
            res = session.run(check_query, name=name).single()
            if res and res["cnt"] > 0:
                return False, f"Station '{name}' already exists."
            session.run(create_query, name=name)
        return True, f"Station '{name}' added successfully."
    except Exception as e:
        return False, f"Failed to add station: {e}"
    finally:
        neo4j_driver.close()


def rename_station(old_name, new_name):
    """Renames a Station node in Neo4j."""
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not old_name or not new_name:
        return False, "Station names cannot be empty."
    if old_name == new_name:
        return True, "No changes made."
    
    neo4j_driver = get_neo4j_driver()
    if not neo4j_driver:
        return False, "Neo4j connection failed."
    try:
        check_query = "MATCH (s:Station {name: $name}) RETURN count(s) AS cnt"
        rename_query = "MATCH (s:Station {name: $old_name}) SET s.name = $new_name"
        with neo4j_driver.session() as session:
            res = session.run(check_query, name=new_name).single()
            if res and res["cnt"] > 0:
                return False, f"Station '{new_name}' already exists."
            session.run(rename_query, old_name=old_name, new_name=new_name)
        return True, f"Station renamed from '{old_name}' to '{new_name}' successfully."
    except Exception as e:
        return False, f"Failed to rename station: {e}"
    finally:
        neo4j_driver.close()


def delete_station(name):
    """Deletes a Station node in Neo4j, deletes its routes, and clears associated schedules in MySQL."""
    name = name.strip()
    if not name:
        return False, "Station name cannot be empty."
    
    # 1. Fetch all routes containing this station from Neo4j to find their route_ids
    neo4j_driver = get_neo4j_driver()
    if not neo4j_driver:
        return False, "Neo4j connection failed."
        
    route_ids = []
    find_routes_query = """
    MATCH (start:Station)-[r:CONNECTS_TO]->(end:Station)
    WHERE start.name = $name OR end.name = $name
    RETURN r.route_id AS route_id
    """
    delete_station_query = "MATCH (s:Station {name: $name}) DETACH DELETE s"
    
    try:
        with neo4j_driver.session() as session:
            res = session.run(find_routes_query, name=name)
            route_ids = [record["route_id"] for record in res]
            
            # Delete from Neo4j
            session.run(delete_station_query, name=name)
    except Exception as e:
        neo4j_driver.close()
        return False, f"Failed to delete station in Neo4j: {e}"
    finally:
        neo4j_driver.close()

    # 2. Delete corresponding schedules in MySQL
    if route_ids:
        mysql_conn = get_mysql_connection()
        if mysql_conn:
            cursor = mysql_conn.cursor()
            try:
                format_strings = ", ".join(["%s"] * len(route_ids))
                delete_schedules_query = f"DELETE FROM Schedules WHERE route_id IN ({format_strings})"
                cursor.execute(delete_schedules_query, tuple(route_ids))
                mysql_conn.commit()
            except Exception as e:
                print(f"⚠️ Failed to clean up MySQL schedules on station delete: {e}")
            finally:
                cursor.close()
                mysql_conn.close()

    return True, f"Station '{name}' and all associated routes/schedules deleted successfully."


def add_route(source, destination):
    """Establishes bidirectional route connections between two stations (creates two relationships in Neo4j)."""
    source = source.strip()
    destination = destination.strip()
    if not source or not destination:
        return False, "Source and destination stations must be specified."
    if source == destination:
        return False, "Source and destination stations cannot be the same."
        
    neo4j_driver = get_neo4j_driver()
    if not neo4j_driver:
        return False, "Neo4j connection failed."
        
    try:
        # Get max route_id
        max_id_query = "MATCH ()-[r:CONNECTS_TO]->() RETURN max(r.route_id) AS max_id"
        
        # Check forward route
        check_forward = "MATCH (a:Station {name: $source})-[r:CONNECTS_TO]->(b:Station {name: $destination}) RETURN count(r) AS cnt"
        # Check backward route
        check_backward = "MATCH (a:Station {name: $destination})-[r:CONNECTS_TO]->(b:Station {name: $source}) RETURN count(r) AS cnt"
        
        with neo4j_driver.session() as session:
            # Find next route_id
            max_res = session.run(max_id_query).single()
            max_id = max_res["max_id"] if max_res and max_res["max_id"] is not None else 0
            
            created_routes = []
            last_id = max_id
            
            # Forward connection
            forward_cnt = session.run(check_forward, source=source, destination=destination).single()["cnt"]
            if forward_cnt == 0:
                last_id += 1
                forward_id = last_id
                create_forward = """
                MATCH (start:Station {name: $source})
                MATCH (end:Station {name: $destination})
                CREATE (start)-[:CONNECTS_TO {route_id: $route_id}]->(end)
                """
                session.run(create_forward, source=source, destination=destination, route_id=forward_id)
                created_routes.append(f"'{source}' to '{destination}' (ID: {forward_id})")
            
            # Backward connection
            backward_cnt = session.run(check_backward, source=source, destination=destination).single()["cnt"]
            if backward_cnt == 0:
                last_id += 1
                backward_id = last_id
                create_backward = """
                MATCH (start:Station {name: $destination})
                MATCH (end:Station {name: $source})
                CREATE (start)-[:CONNECTS_TO {route_id: $route_id}]->(end)
                """
                session.run(create_backward, source=source, destination=destination, route_id=backward_id)
                created_routes.append(f"'{destination}' to '{source}' (ID: {backward_id})")
            
            if not created_routes:
                return False, "Connections in both directions already exist."
                
        return True, {"message": f"Established bidirectional connections: {', '.join(created_routes)}.", "route_id": last_id}
    except Exception as e:
        return False, f"Failed to create bidirectional route: {e}"
    finally:
        neo4j_driver.close()


def delete_route(route_id):
    """Deletes a route connection from Neo4j and clears its schedules in MySQL."""
    try:
        route_id = int(route_id)
    except ValueError:
        return False, "Invalid route ID format."

    # 1. Delete from Neo4j
    neo4j_driver = get_neo4j_driver()
    if not neo4j_driver:
        return False, "Neo4j connection failed."
    cypher_query = "MATCH ()-[r:CONNECTS_TO {route_id: $route_id}]->() DELETE r"
    try:
        with neo4j_driver.session() as session:
            session.run(cypher_query, route_id=route_id)
    except Exception as e:
        neo4j_driver.close()
        return False, f"Failed to delete route in Neo4j: {e}"
    finally:
        neo4j_driver.close()

    # 2. Delete from MySQL Schedules
    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        return False, "MySQL connection failed. Route deleted in graph, but schedules could not be deleted."
    cursor = mysql_conn.cursor()
    try:
        sql_query = "DELETE FROM Schedules WHERE route_id = %s"
        cursor.execute(sql_query, (route_id,))
        mysql_conn.commit()
    except Exception as e:
        return False, f"Failed to delete schedules in MySQL: {e}"
    finally:
        cursor.close()
        mysql_conn.close()

    return True, f"Route ID {route_id} and all its schedules deleted successfully."


def add_schedule(route_id, transport_type, departure_time, arrival_time, available_seats):
    """Inserts a new schedule slot in MySQL."""
    try:
        route_id = int(route_id)
        available_seats = int(available_seats)
    except ValueError:
        return False, "Route ID and Available Seats must be numeric integers."

    # Map transport_type to transport_id
    transport_map = {"Flight": 1, "Train": 2, "Bus": 3}
    transport_id = transport_map.get(transport_type)
    if not transport_id:
        return False, "Invalid transport service type. Must be Flight, Train, or Bus."

    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        return False, "MySQL connection failed."
    cursor = mysql_conn.cursor()
    try:
        sql_query = """
        INSERT INTO Schedules (route_id, transport_id, departure_time, arrival_time, available_seats)
        VALUES (%s, %s, %s, %s, %s)
        """
        cursor.execute(sql_query, (route_id, transport_id, departure_time, arrival_time, available_seats))
        mysql_conn.commit()
        return True, "Schedule added successfully."
    except Exception as e:
        return False, f"Failed to save schedule in MySQL: {e}"
    finally:
        cursor.close()
        mysql_conn.close()


def update_schedule(schedule_id, transport_type, departure_time, arrival_time, available_seats):
    """Updates an existing schedule record in MySQL."""
    try:
        schedule_id = int(schedule_id)
        available_seats = int(available_seats)
    except ValueError:
        return False, "Schedule ID and Available Seats must be numeric integers."

    transport_map = {"Flight": 1, "Train": 2, "Bus": 3}
    transport_id = transport_map.get(transport_type)
    if not transport_id:
        return False, "Invalid transport service type."

    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        return False, "MySQL connection failed."
    cursor = mysql_conn.cursor()
    try:
        sql_query = """
        UPDATE Schedules 
        SET transport_id = %s, departure_time = %s, arrival_time = %s, available_seats = %s
        WHERE schedule_id = %s
        """
        cursor.execute(sql_query, (transport_id, departure_time, arrival_time, available_seats, schedule_id))
        mysql_conn.commit()
        return True, "Schedule updated successfully."
    except Exception as e:
        return False, f"Failed to update schedule in MySQL: {e}"
    finally:
        cursor.close()
        mysql_conn.close()


def delete_schedule(schedule_id):
    """Deletes a schedule record from MySQL."""
    try:
        schedule_id = int(schedule_id)
    except ValueError:
        return False, "Invalid schedule ID."

    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        return False, "MySQL connection failed."
    cursor = mysql_conn.cursor()
    try:
        sql_query = "DELETE FROM Schedules WHERE schedule_id = %s"
        cursor.execute(sql_query, (schedule_id,))
        mysql_conn.commit()
        return True, "Schedule deleted successfully."
    except Exception as e:
        return False, f"Failed to delete schedule: {e}"
    finally:
        cursor.close()
        mysql_conn.close()


# ==========================================
# 5. ISOLATED LOCAL TEST BENCH
# ==========================================
if __name__ == "__main__":
    # Test your connection and querying directly before creating Flask files
    # Change 'Mumbai' and 'Pune' to values currently inside your databases
    test_result = query_transport_system("New Delhi", "Bangalore")

    print("\n--- TEST RUN RESULTS ---")
    import json

    print(json.dumps(test_result, indent=4, default=str))
