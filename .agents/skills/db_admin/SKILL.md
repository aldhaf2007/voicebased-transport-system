---
name: db_admin
description: Diagnostics, seeding, and administrative helpers for MySQL and Neo4j databases in the Voice Transport System.
---

# Database Administration & Verification Skill

Use this skill to diagnose database connection issues, verify database integrity, check schema structure, or reset database seed configurations.

---

## 🛠️ Connection Check & Diagnostics

When the user reports database failures or connection issues, execute these steps sequentially:

1. **Verify MySQL Service**:
   - Check if MySQL is running locally on port 3306.
   - Run the Python verification script or execute a basic select query:
     ```python
     import mysql.connector
     from database import MYSQL_CONFIG
     try:
         conn = mysql.connector.connect(**MYSQL_CONFIG)
         print("MySQL Connected successfully!")
         conn.close()
     except Exception as e:
         print(f"MySQL Connection Failed: {e}")
     ```

2. **Verify Neo4j Service**:
   - Check if the bolt endpoint (`bolt://localhost:7687`) is reachable.
   - Run verification in Python:
     ```python
     from neo4j import GraphDatabase
     from database import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
     try:
         driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
         driver.verify_connectivity()
         print("Neo4j Connected successfully!")
         driver.close()
     except Exception as e:
         print(f"Neo4j Connection Failed: {e}")
     ```

---

## 🗄️ Querying and Seeding Data

To list nodes, connections, or relational rows, use the functions inside `database.py` rather than raw SQL or Cypher when possible:

- **List All Stations**: Run `database.py` with the corresponding list functions or import it:
  ```python
  from database import get_all_stations
  print(get_all_stations())
  ```
- **List All Routes**: Run `database.py`:
  ```python
  from database import get_all_routes
  print(get_all_routes())
  ```
- **List All Schedules**: Run `database.py`:
  ```python
  from database import get_all_schedules
  print(get_all_schedules())
  ```

---

## 🏗️ Rebuilding Schema / Re-seeding
If a reset of the database schema is requested:
1. Direct the user to start MySQL and Neo4j.
2. Initialize MySQL tables:
   - Ensure the database `transport_db` is created.
   - Recreate the `Transport_Details` and `Schedules` tables.
3. Initialize Neo4j nodes and constraints:
   - Ensure a uniqueness constraint is set on `Station(name)`.
