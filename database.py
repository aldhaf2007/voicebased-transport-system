import mysql.connector
from neo4j import GraphDatabase
import os
from werkzeug.security import generate_password_hash, check_password_hash

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

    # Find paths from start to end (up to 5 hops)
    cypher_query = """
    MATCH p = (start:Station {name: $src})-[r:CONNECTS_TO*1..5]->(end:Station {name: $dest})
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

    # Separate direct paths (1 hop) and transit paths (multiple hops)
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
        schedules = []
        # Case A: Direct routes are available
        if direct_paths:
            route_ids = [p["route_ids"][0] for p in direct_paths]
            format_strings = ", ".join(["%s"] * len(route_ids))
            sql_query = f"""
            SELECT 
                s.schedule_id,
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
            
        if schedules:
            return {
                "status": "Success",
                "origin": src,
                "destination": dest,
                "schedules": schedules,
                "is_transit": False
            }

        # Case B: No direct route (or direct routes have no schedules), but multi-hop transit paths exist
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
                    s.schedule_id,
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
            # Sort transit paths by fewest legs first
            resolved_transit_paths.sort(key=lambda x: len(x["legs"]))
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
# 5. TICKET BOOKING MANAGEMENT
# ==========================================
def init_bookings_table():
    """Creates the Bookings table in MySQL if it does not exist, and ensures travel_date exists."""
    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        print("❌ MySQL Connection failed for database initialization.")
        return
    cursor = mysql_conn.cursor()
    try:
        create_table_query = """
        CREATE TABLE IF NOT EXISTS Bookings (
            booking_id INT PRIMARY KEY AUTO_INCREMENT,
            schedule_id INT NOT NULL,
            passenger_name VARCHAR(255) NOT NULL,
            passenger_email VARCHAR(255) NOT NULL,
            seats_booked INT NOT NULL DEFAULT 1,
            booking_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (schedule_id) REFERENCES Schedules(schedule_id)
        );
        """
        cursor.execute(create_table_query)
        mysql_conn.commit()

        # Ensure travel_date column exists
        cursor.execute("SHOW COLUMNS FROM Bookings LIKE 'travel_date'")
        if not cursor.fetchone():
            print("Adding 'travel_date' column to Bookings table...")
            cursor.execute("ALTER TABLE Bookings ADD COLUMN travel_date DATE")
            mysql_conn.commit()

        # Ensure status column exists
        cursor.execute("SHOW COLUMNS FROM Bookings LIKE 'status'")
        if not cursor.fetchone():
            print("Adding 'status' column to Bookings table...")
            cursor.execute("ALTER TABLE Bookings ADD COLUMN status VARCHAR(20) DEFAULT 'ACTIVE'")
            mysql_conn.commit()
            
        print("✅ Bookings table checked/created in MySQL.")
    except Exception as e:
        print(f"❌ Error creating Bookings table: {e}")
    finally:
        cursor.close()
        mysql_conn.close()

def init_users_and_update_bookings():
    """Creates the Users table and updates Bookings to include user_id if not present."""
    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        print("❌ MySQL Connection failed for users database initialization.")
        return
    cursor = mysql_conn.cursor()
    try:
        # Create Users Table
        create_users_query = """
        CREATE TABLE IF NOT EXISTS Users (
            user_id INT PRIMARY KEY AUTO_INCREMENT,
            username VARCHAR(255) UNIQUE NOT NULL,
            email VARCHAR(255) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        cursor.execute(create_users_query)
        mysql_conn.commit()
        print("✅ Users table checked/created in MySQL.")

        # Ensure user_id column exists in Bookings table
        cursor.execute("SHOW COLUMNS FROM Bookings LIKE 'user_id'")
        if not cursor.fetchone():
            print("Adding 'user_id' column to Bookings table...")
            cursor.execute("ALTER TABLE Bookings ADD COLUMN user_id INT")
            cursor.execute("ALTER TABLE Bookings ADD FOREIGN KEY (user_id) REFERENCES Users(user_id)")
            mysql_conn.commit()
            print("✅ 'user_id' column added to Bookings table.")
    except Exception as e:
        print(f"❌ Error initializing users/bookings database: {e}")
    finally:
        cursor.close()
        mysql_conn.close()


def get_schedule_by_id(schedule_id):
    """Fetches a specific schedule from MySQL by its ID, including origin/destination stations."""
    try:
        schedule_id = int(schedule_id)
    except ValueError:
        return None

    routes = get_all_routes()
    routes_map = {r["route_id"]: (r["source"], r["destination"]) for r in routes}

    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        return None
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
    WHERE s.schedule_id = %s
    """
    try:
        cursor.execute(sql_query, (schedule_id,))
        row = cursor.fetchone()
        if row:
            route_id = row["route_id"]
            source, destination = routes_map.get(route_id, ("Unknown", "Unknown"))
            row["source"] = source
            row["destination"] = destination
            
            import datetime
            for key in ["departure_time", "arrival_time"]:
                val = row[key]
                if isinstance(val, (datetime.timedelta, datetime.time)):
                    row[key] = str(val)
            return row
    except Exception as e:
        print(f"❌ Error fetching schedule {schedule_id}: {e}")
    finally:
        cursor.close()
        mysql_conn.close()
    return None


def create_booking(schedule_id, passenger_name, passenger_email, seats_booked, travel_date, user_id=None):
    """
    Creates a booking in MySQL.
    1. Verifies if there are enough available seats in the schedule.
    2. Decrements the available seats.
    3. Inserts the booking entry.
    Uses a transaction to ensure atomic execution.
    """
    try:
        schedule_id = int(schedule_id)
        seats_booked = int(seats_booked)
    except ValueError:
        return False, "Invalid schedule ID or seat count."

    if seats_booked <= 0:
        return False, "Seats booked must be at least 1."

    if not travel_date:
        return False, "Travel date is required."

    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        return False, "MySQL connection failed."
    
    mysql_conn.autocommit = False
    cursor = mysql_conn.cursor(dictionary=True)
    
    try:
        cursor.execute(
            "SELECT available_seats FROM Schedules WHERE schedule_id = %s FOR UPDATE",
            (schedule_id,)
        )
        row = cursor.fetchone()
        if not row:
            mysql_conn.rollback()
            return False, "Schedule not found."
        
        available = row["available_seats"]
        if available < seats_booked:
            mysql_conn.rollback()
            return False, f"Not enough seats available. Requested: {seats_booked}, Available: {available}."
        
        new_seats = available - seats_booked
        cursor.execute(
            "UPDATE Schedules SET available_seats = %s WHERE schedule_id = %s",
            (new_seats, schedule_id)
        )
        
        insert_query = """
        INSERT INTO Bookings (schedule_id, passenger_name, passenger_email, seats_booked, travel_date, user_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        """
        cursor.execute(insert_query, (schedule_id, passenger_name, passenger_email, seats_booked, travel_date, user_id))
        booking_id = cursor.lastrowid
        
        mysql_conn.commit()
        return True, {
            "booking_id": booking_id,
            "schedule_id": schedule_id,
            "passenger_name": passenger_name,
            "passenger_email": passenger_email,
            "seats_booked": seats_booked,
            "travel_date": str(travel_date),
            "remaining_seats": new_seats,
            "user_id": user_id
        }
    except Exception as e:
        mysql_conn.rollback()
        print(f"❌ Error creating booking: {e}")
        return False, f"Booking transaction failed: {str(e)}"
    finally:
        cursor.close()
        mysql_conn.close()


def create_transit_bookings(schedule_ids, passenger_name, passenger_email, seats_booked, travel_date, user_id=None):
    """
    Creates multiple bookings for a transit journey inside a single MySQL transaction.
    If any single leg fails (e.g. sold out), the entire transaction is rolled back.
    """
    if not schedule_ids:
        return False, "No schedule IDs provided."

    try:
        seats_booked = int(seats_booked)
    except ValueError:
        return False, "Invalid seat count."

    if seats_booked <= 0:
        return False, "Seats booked must be at least 1."

    if not travel_date:
        return False, "Travel date is required."

    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        return False, "MySQL connection failed."
    
    mysql_conn.autocommit = False
    cursor = mysql_conn.cursor(dictionary=True)
    
    bookings_created = []
    try:
        for s_id in schedule_ids:
            try:
                s_id = int(s_id)
            except ValueError:
                mysql_conn.rollback()
                return False, f"Invalid schedule ID: {s_id}."

            # Lock and check seats
            cursor.execute(
                "SELECT available_seats FROM Schedules WHERE schedule_id = %s FOR UPDATE",
                (s_id,)
            )
            row = cursor.fetchone()
            if not row:
                mysql_conn.rollback()
                return False, f"Schedule ID {s_id} not found."
            
            available = row["available_seats"]
            if available < seats_booked:
                mysql_conn.rollback()
                return False, f"Not enough seats available on Schedule ID {s_id}. Requested: {seats_booked}, Available: {available}."
            
            # Update available seats
            new_seats = available - seats_booked
            cursor.execute(
                "UPDATE Schedules SET available_seats = %s WHERE schedule_id = %s",
                (new_seats, s_id)
            )
            
            # Insert booking record
            insert_query = """
            INSERT INTO Bookings (schedule_id, passenger_name, passenger_email, seats_booked, travel_date, user_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            """
            cursor.execute(insert_query, (s_id, passenger_name, passenger_email, seats_booked, travel_date, user_id))
            booking_id = cursor.lastrowid
            
            bookings_created.append({
                "booking_id": booking_id,
                "schedule_id": s_id,
                "passenger_name": passenger_name,
                "passenger_email": passenger_email,
                "seats_booked": seats_booked,
                "travel_date": str(travel_date),
                "remaining_seats": new_seats,
                "user_id": user_id
            })
            
        mysql_conn.commit()
        return True, bookings_created
    except Exception as e:
        mysql_conn.rollback()
        print(f"❌ Error creating transit bookings: {e}")
        return False, f"Transit booking transaction failed: {str(e)}"
    finally:
        cursor.close()
        mysql_conn.close()


# Automatically initialize the bookings table on module load
init_bookings_table()
init_users_and_update_bookings()


# ==========================================
# 6. USER AUTHENTICATION & MANAGEMENT
# ==========================================
def register_user(username, email, password):
    """Registers a new user in the database."""
    username = username.strip()
    email = email.strip()
    password = password.strip()
    if not username or not email or not password:
        return False, "All fields are required."

    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        return False, "Database connection failed."
    cursor = mysql_conn.cursor()
    try:
        # Check if username already exists
        cursor.execute("SELECT user_id FROM Users WHERE username = %s", (username,))
        if cursor.fetchone():
            return False, "Username is already taken."

        # Insert user
        password_hash = generate_password_hash(password)
        sql = "INSERT INTO Users (username, email, password_hash) VALUES (%s, %s, %s)"
        cursor.execute(sql, (username, email, password_hash))
        mysql_conn.commit()
        return True, "User registered successfully."
    except Exception as e:
        print(f"❌ Error registering user: {e}")
        return False, f"Failed to register user: {str(e)}"
    finally:
        cursor.close()
        mysql_conn.close()


def authenticate_user(username, password):
    """Authenticates a user and returns their user data if successful."""
    username = username.strip()
    password = password.strip()
    if not username or not password:
        return None

    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        return None
    cursor = mysql_conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT user_id, username, email, password_hash FROM Users WHERE username = %s", (username,))
        user = cursor.fetchone()
        if user and check_password_hash(user["password_hash"], password):
            # Remove hash before returning
            user.pop("password_hash")
            return user
    except Exception as e:
        print(f"❌ Error authenticating user: {e}")
    finally:
        cursor.close()
        mysql_conn.close()
    return None


def get_all_users():
    """Fetches all registered users from MySQL."""
    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        return []
    cursor = mysql_conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT user_id, username, email, created_at FROM Users ORDER BY created_at DESC")
        return cursor.fetchall()
    except Exception as e:
        print(f"❌ Error fetching users: {e}")
        return []
    finally:
        cursor.close()
        mysql_conn.close()


def get_all_bookings():
    """Fetches all bookings along with their schedule details."""
    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        return []
    cursor = mysql_conn.cursor(dictionary=True)
    try:
        query = """
        SELECT b.booking_id, b.passenger_name, b.passenger_email, b.seats_booked, 
               b.travel_date, b.booking_time, b.user_id, b.status, s.route_id, t.type AS transport_type, 
               s.departure_time, s.arrival_time
        FROM Bookings b
        JOIN Schedules s ON b.schedule_id = s.schedule_id
        JOIN Transport_Details t ON s.transport_id = t.transport_id
        ORDER BY b.booking_time DESC
        """
        cursor.execute(query)
        rows = cursor.fetchall()

        routes = get_all_routes()
        routes_map = {r["route_id"]: (r["source"], r["destination"]) for r in routes}

        import datetime
        for row in rows:
            route_id = row["route_id"]
            source, destination = routes_map.get(route_id, ("Unknown", "Unknown"))
            row["source"] = source
            row["destination"] = destination
            
            if isinstance(row["travel_date"], datetime.date):
                row["travel_date"] = row["travel_date"].strftime("%d-%m-%Y")
            if isinstance(row["booking_time"], datetime.datetime):
                row["booking_time"] = row["booking_time"].strftime("%d-%m-%Y %H:%M")
            for key in ["departure_time", "arrival_time"]:
                if isinstance(row[key], (datetime.timedelta, datetime.time)):
                    row[key] = str(row[key])

        return rows
    except Exception as e:
        print(f"❌ Error fetching bookings: {e}")
        return []
    finally:
        cursor.close()
        mysql_conn.close()


# ==========================================
# 5. ISOLATED LOCAL TEST BENCH
# ==========================================
def get_user_bookings(user_id):
    """Fetches all bookings for a specific user."""
    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        return []
    cursor = mysql_conn.cursor(dictionary=True)
    try:
        query = """
        SELECT b.booking_id, b.passenger_name, b.passenger_email, b.seats_booked, 
               b.travel_date, b.booking_time, b.user_id, b.status, s.route_id, t.type AS transport_type, 
               s.departure_time, s.arrival_time
        FROM Bookings b
        JOIN Schedules s ON b.schedule_id = s.schedule_id
        JOIN Transport_Details t ON s.transport_id = t.transport_id
        WHERE b.user_id = %s
        ORDER BY b.booking_time DESC
        """
        cursor.execute(query, (user_id,))
        rows = cursor.fetchall()

        routes = get_all_routes()
        routes_map = {r["route_id"]: (r["source"], r["destination"]) for r in routes}

        import datetime
        for row in rows:
            route_id = row["route_id"]
            source, destination = routes_map.get(route_id, ("Unknown", "Unknown"))
            row["source"] = source
            row["destination"] = destination
            
            if isinstance(row["travel_date"], datetime.date):
                row["travel_date"] = row["travel_date"].strftime("%d-%m-%Y")
            if isinstance(row["booking_time"], datetime.datetime):
                row["booking_time"] = row["booking_time"].strftime("%d-%m-%Y %H:%M")
            for key in ["departure_time", "arrival_time"]:
                if isinstance(row[key], (datetime.timedelta, datetime.time)):
                    row[key] = str(row[key])

        return rows
    except Exception as e:
        print(f"❌ Error fetching bookings for user {user_id}: {e}")
        return []
    finally:
        cursor.close()
        mysql_conn.close()

def cancel_user_booking(booking_id, user_id):
    """Cancels a user booking, restoring available seats in the schedule."""
    mysql_conn = get_mysql_connection()
    if not mysql_conn:
        return False, "Database connection failed."
    
    mysql_conn.autocommit = False
    cursor = mysql_conn.cursor(dictionary=True)
    try:
        # Check if booking exists and belongs to user
        cursor.execute(
            "SELECT schedule_id, seats_booked FROM Bookings WHERE booking_id = %s AND user_id = %s FOR UPDATE",
            (booking_id, user_id)
        )
        booking = cursor.fetchone()
        
        if not booking:
            mysql_conn.rollback()
            return False, "Booking not found or access denied."
            
        schedule_id = booking['schedule_id']
        seats_booked = booking['seats_booked']
        
        # Restore seats
        cursor.execute(
            "UPDATE Schedules SET available_seats = available_seats + %s WHERE schedule_id = %s",
            (seats_booked, schedule_id)
        )
        
        # Mark booking as cancelled
        cursor.execute(
            "UPDATE Bookings SET status = 'CANCELLED' WHERE booking_id = %s",
            (booking_id,)
        )
        
        mysql_conn.commit()
        return True, "Booking cancelled successfully."
    except Exception as e:
        mysql_conn.rollback()
        print(f"❌ Error cancelling booking {booking_id}: {e}")
        return False, f"Error cancelling booking: {str(e)}"
    finally:
        cursor.close()
        mysql_conn.close()

# ==========================================
# 6. ISOLATED LOCAL TEST BENCH
# ==========================================
if __name__ == "__main__":
    # Test your connection and querying directly before creating Flask files
    # Change 'Mumbai' and 'Pune' to values currently inside your databases
    test_result = query_transport_system("New Delhi", "Bangalore")

    print("\n--- TEST RUN RESULTS ---")
    import json

    print(json.dumps(test_result, indent=4, default=str))
