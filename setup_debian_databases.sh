#!/usr/bin/env bash
# ==============================================================================
# Automated Database Setup Script for Debian Linux
# Sets up and configures both MySQL (MariaDB) and Neo4j for VoiceTransportSystem
# ==============================================================================

set -eo pipefail

# Text formatting
BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}${BOLD}======================================================${NC}"
echo -e "${BLUE}${BOLD}  Voice Transport System - Automated Database Setup   ${NC}"
echo -e "${BLUE}${BOLD}  Target: Debian / Ubuntu Linux (MySQL & Neo4j)       ${NC}"
echo -e "${BLUE}${BOLD}======================================================${NC}"

# 1. Root privilege check
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}❌ Error: This script must be run as root (or via sudo).${NC}"
    echo -e "Please run: ${BOLD}sudo $0${NC}"
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive

# ------------------------------------------------------------------------------
# STEP 1: Install Core System Dependencies
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}--> [1/5] Updating packages and installing prerequisites...${NC}"
apt-get update -y
apt-get install -y \
    curl \
    wget \
    gnupg \
    ca-certificates \
    apt-transport-https \
    software-properties-common \
    lsb-release

# ------------------------------------------------------------------------------
# STEP 2: Install and Configure MySQL / MariaDB
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}--> [2/5] Installing MySQL / MariaDB Server...${NC}"
if ! command -v mariadb &>/dev/null && ! command -v mysql &>/dev/null; then
    apt-get install -y mariadb-server mariadb-client || apt-get install -y default-mysql-server default-mysql-client
fi

# Ensure service is enabled and running
echo "Starting MySQL/MariaDB service..."
systemctl daemon-reload
systemctl enable mariadb 2>/dev/null || systemctl enable mysql 2>/dev/null || true
systemctl restart mariadb 2>/dev/null || systemctl restart mysql 2>/dev/null || true

# Wait for MySQL to accept connections
echo "Waiting for MySQL service to become available..."
for i in {1..15}; do
    if mysql -e "SELECT 1;" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo "Configuring MySQL permissions & creating 'transport_db'..."

# Create database and configure root access for localhost/127.0.0.1
if mysql -e "SELECT VERSION();" 2>/dev/null | grep -qi "mariadb"; then
    # MariaDB authentication configuration
    mysql <<EOF
CREATE DATABASE IF NOT EXISTS transport_db;
-- Allow root connection without socket constraint for local Python app
ALTER USER 'root'@'localhost' IDENTIFIED VIA mysql_native_password USING PASSWORD('');
GRANT ALL PRIVILEGES ON transport_db.* TO 'root'@'localhost';
FLUSH PRIVILEGES;
EOF
else
    # Standard MySQL 8+ configuration
    mysql <<EOF
CREATE DATABASE IF NOT EXISTS transport_db;
ALTER USER 'root'@'localhost' IDENTIFIED BY '';
GRANT ALL PRIVILEGES ON transport_db.* TO 'root'@'localhost';
FLUSH PRIVILEGES;
EOF
fi

# Initialize database schema and seed data
echo "Creating tables and seeding initial transport schedules..."
mysql transport_db <<EOF
-- 1. Transport Types
CREATE TABLE IF NOT EXISTS Transport_Details (
    transport_id INT PRIMARY KEY,
    type VARCHAR(50) NOT NULL
);

INSERT IGNORE INTO Transport_Details (transport_id, type)
VALUES (1, 'Flight'), (2, 'Train'), (3, 'Bus');

-- 2. Schedules Table
CREATE TABLE IF NOT EXISTS Schedules (
    schedule_id INT PRIMARY KEY AUTO_INCREMENT,
    route_id INT NOT NULL,
    transport_id INT NOT NULL,
    departure_time TIME NOT NULL,
    arrival_time TIME NOT NULL,
    available_seats INT NOT NULL,
    FOREIGN KEY (transport_id) REFERENCES Transport_Details(transport_id)
);

-- 3. Users Table
CREATE TABLE IF NOT EXISTS Users (
    user_id INT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. Bookings Table
CREATE TABLE IF NOT EXISTS Bookings (
    booking_id INT PRIMARY KEY AUTO_INCREMENT,
    schedule_id INT NOT NULL,
    passenger_name VARCHAR(255) NOT NULL,
    passenger_email VARCHAR(255) NOT NULL,
    seats_booked INT NOT NULL DEFAULT 1,
    booking_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    travel_date DATE,
    status VARCHAR(20) DEFAULT 'ACTIVE',
    user_id INT,
    FOREIGN KEY (schedule_id) REFERENCES Schedules(schedule_id),
    FOREIGN KEY (user_id) REFERENCES Users(user_id)
);

-- 5. Seed Initial Schedules matching Neo4j Routes
-- Route 1: New Delhi -> Mumbai
INSERT INTO Schedules (route_id, transport_id, departure_time, arrival_time, available_seats)
SELECT 1, 2, '06:00:00', '14:30:00', 50
WHERE NOT EXISTS (SELECT 1 FROM Schedules WHERE route_id = 1 AND transport_id = 2);

INSERT INTO Schedules (route_id, transport_id, departure_time, arrival_time, available_seats)
SELECT 1, 1, '09:00:00', '11:15:00', 30
WHERE NOT EXISTS (SELECT 1 FROM Schedules WHERE route_id = 1 AND transport_id = 1);

-- Route 2: Mumbai -> New Delhi
INSERT INTO Schedules (route_id, transport_id, departure_time, arrival_time, available_seats)
SELECT 2, 2, '16:00:00', '23:45:00', 45
WHERE NOT EXISTS (SELECT 1 FROM Schedules WHERE route_id = 2 AND transport_id = 2);

INSERT INTO Schedules (route_id, transport_id, departure_time, arrival_time, available_seats)
SELECT 2, 1, '18:30:00', '20:45:00', 25
WHERE NOT EXISTS (SELECT 1 FROM Schedules WHERE route_id = 2 AND transport_id = 1);

-- Route 3: Mumbai -> Pune
INSERT INTO Schedules (route_id, transport_id, departure_time, arrival_time, available_seats)
SELECT 3, 3, '07:00:00', '10:30:00', 40
WHERE NOT EXISTS (SELECT 1 FROM Schedules WHERE route_id = 3 AND transport_id = 3);

INSERT INTO Schedules (route_id, transport_id, departure_time, arrival_time, available_seats)
SELECT 3, 2, '08:15:00', '11:45:00', 60
WHERE NOT EXISTS (SELECT 1 FROM Schedules WHERE route_id = 3 AND transport_id = 2);

-- Route 4: Pune -> Mumbai
INSERT INTO Schedules (route_id, transport_id, departure_time, arrival_time, available_seats)
SELECT 4, 3, '17:00:00', '20:30:00', 40
WHERE NOT EXISTS (SELECT 1 FROM Schedules WHERE route_id = 4 AND transport_id = 3);

-- Route 5: Pune -> Bangalore
INSERT INTO Schedules (route_id, transport_id, departure_time, arrival_time, available_seats)
SELECT 5, 2, '12:00:00', '22:00:00', 50
WHERE NOT EXISTS (SELECT 1 FROM Schedules WHERE route_id = 5 AND transport_id = 2);

INSERT INTO Schedules (route_id, transport_id, departure_time, arrival_time, available_seats)
SELECT 5, 3, '19:30:00', '08:00:00', 35
WHERE NOT EXISTS (SELECT 1 FROM Schedules WHERE route_id = 5 AND transport_id = 3);

-- Route 6: Bangalore -> Pune
INSERT INTO Schedules (route_id, transport_id, departure_time, arrival_time, available_seats)
SELECT 6, 2, '07:30:00', '17:30:00', 50
WHERE NOT EXISTS (SELECT 1 FROM Schedules WHERE route_id = 6 AND transport_id = 2);

-- Route 7: New Delhi -> Bangalore
INSERT INTO Schedules (route_id, transport_id, departure_time, arrival_time, available_seats)
SELECT 7, 1, '06:15:00', '09:00:00', 40
WHERE NOT EXISTS (SELECT 1 FROM Schedules WHERE route_id = 7 AND transport_id = 1);

INSERT INTO Schedules (route_id, transport_id, departure_time, arrival_time, available_seats)
SELECT 7, 2, '20:00:00', '06:30:00', 50
WHERE NOT EXISTS (SELECT 1 FROM Schedules WHERE route_id = 7 AND transport_id = 2);

-- Route 8: Bangalore -> New Delhi
INSERT INTO Schedules (route_id, transport_id, departure_time, arrival_time, available_seats)
SELECT 8, 1, '10:00:00', '12:45:00', 40
WHERE NOT EXISTS (SELECT 1 FROM Schedules WHERE route_id = 8 AND transport_id = 1);
EOF

echo -e "${GREEN}✅ MySQL setup and data initialization complete.${NC}"

# ------------------------------------------------------------------------------
# STEP 3: Install Java and Neo4j
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}--> [3/5] Setting up Neo4j repository and Java Runtime...${NC}"

# Neo4j 5 requires Java 17
apt-get install -y openjdk-17-jre-headless || apt-get install -y default-jre-headless

# Add Neo4j GPG Key
mkdir -p -m 0755 /etc/apt/keyrings
curl -fsSL https://debian.neo4j.com/neotechnology.gpg.key | gpg --dearmor -o /etc/apt/keyrings/neo4j.gpg --yes

# Add Neo4j Apt Repository
echo "deb [signed-by=/etc/apt/keyrings/neo4j.gpg] https://debian.neo4j.com stable latest" > /etc/apt/sources.list.d/neo4j.list

# Install Neo4j and cypher-shell
apt-get update -y
apt-get install -y neo4j cypher-shell

# ------------------------------------------------------------------------------
# STEP 4: Configure Neo4j Credentials & Start Service
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}--> [4/5] Configuring Neo4j credentials...${NC}"

NEO4J_TARGET_PASS="0362007Ag"

# Stop service if running to set initial password cleanly
systemctl stop neo4j 2>/dev/null || true

# Set initial admin password before starting the database
echo "Setting initial password for user 'neo4j' to '${NEO4J_TARGET_PASS}'..."
if command -v neo4j-admin &>/dev/null; then
    neo4j-admin dbms set-initial-password "${NEO4J_TARGET_PASS}" 2>/dev/null || true
fi

# Enable and start Neo4j service
echo "Starting Neo4j service..."
systemctl daemon-reload
systemctl enable neo4j
systemctl start neo4j

# Wait for Bolt port (7687) and authentication
echo "Waiting for Neo4j Bolt port 7687 to accept connections..."
NEO4J_READY=0
for i in {1..30}; do
    # Try with target password
    if cypher-shell -a "bolt://localhost:7687" -u neo4j -p "${NEO4J_TARGET_PASS}" "RETURN 1;" >/dev/null 2>&1; then
        NEO4J_READY=1
        break
    fi
    # If initial password prompt is required or default password still active
    if cypher-shell -a "bolt://localhost:7687" -u neo4j -p "neo4j" "ALTER CURRENT USER SET PASSWORD FROM 'neo4j' TO '${NEO4J_TARGET_PASS}';" >/dev/null 2>&1; then
        NEO4J_READY=1
        break
    fi
    sleep 2
done

if [ "$NEO4J_READY" -ne 1 ]; then
    echo -e "${RED}⚠️ Warning: Neo4j took longer than expected to start.${NC}"
    echo "Check status with: systemctl status neo4j"
else
    echo -e "${GREEN}✅ Neo4j is online and authenticated!${NC}"
fi

# ------------------------------------------------------------------------------
# STEP 5: Seed Neo4j Graph Topologies (Stations & Routes)
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}--> [5/5] Seeding Neo4j Station Nodes and Route Relationships...${NC}"

cypher-shell -a "bolt://localhost:7687" -u neo4j -p "${NEO4J_TARGET_PASS}" <<'EOF'
// Create Station Nodes
MERGE (delhi:Station {name: 'New Delhi'})
MERGE (mumbai:Station {name: 'Mumbai'})
MERGE (pune:Station {name: 'Pune'})
MERGE (bangalore:Station {name: 'Bangalore'});

// Create Route Relationships (Route IDs match MySQL Schedules table)
MATCH (delhi:Station {name: 'New Delhi'}), (mumbai:Station {name: 'Mumbai'})
MERGE (delhi)-[:CONNECTS_TO {route_id: 1}]->(mumbai)
MERGE (mumbai)-[:CONNECTS_TO {route_id: 2}]->(delhi);

MATCH (mumbai:Station {name: 'Mumbai'}), (pune:Station {name: 'Pune'})
MERGE (mumbai)-[:CONNECTS_TO {route_id: 3}]->(pune)
MERGE (pune)-[:CONNECTS_TO {route_id: 4}]->(mumbai);

MATCH (pune:Station {name: 'Pune'}), (bangalore:Station {name: 'Bangalore'})
MERGE (pune)-[:CONNECTS_TO {route_id: 5}]->(bangalore)
MERGE (bangalore)-[:CONNECTS_TO {route_id: 6}]->(pune);

MATCH (delhi:Station {name: 'New Delhi'}), (bangalore:Station {name: 'Bangalore'})
MERGE (delhi)-[:CONNECTS_TO {route_id: 7}]->(bangalore)
MERGE (bangalore)-[:CONNECTS_TO {route_id: 8}]->(delhi);
EOF

echo -e "${GREEN}✅ Neo4j stations and route graph topology successfully populated.${NC}"

# ------------------------------------------------------------------------------
# Verification & Summary
# ------------------------------------------------------------------------------
echo -e "\n${BLUE}${BOLD}======================================================${NC}"
echo -e "${GREEN}${BOLD}      Database Setup & Configuration Complete!        ${NC}"
echo -e "${BLUE}${BOLD}======================================================${NC}"

# Verification queries
SCHEDULE_COUNT=$(mysql -N -s -e "SELECT count(*) FROM transport_db.Schedules;" 2>/dev/null || echo "N/A")
STATION_COUNT=$(cypher-shell -a "bolt://localhost:7687" -u neo4j -p "${NEO4J_TARGET_PASS}" "MATCH (s:Station) RETURN count(s);" 2>/dev/null | tail -n 1 || echo "N/A")

echo -e "\n${BOLD}Database Status:${NC}"
echo -e "  • MySQL Database : ${GREEN}transport_db${NC} (${SCHEDULE_COUNT} active schedule records)"
echo -e "  • MySQL Host     : ${GREEN}localhost:3306${NC} (User: root)"
echo -e "  • Neo4j Graph    : ${GREEN}bolt://localhost:7687${NC} (${STATION_COUNT} station nodes connected)"
echo -e "  • Neo4j User     : ${GREEN}neo4j${NC}"
echo -e "  • Neo4j Password : ${GREEN}${NEO4J_TARGET_PASS}${NC}"

echo -e "\n${BOLD}Next Steps:${NC}"
echo -e "  Your application's ${BOLD}database.py${NC} is already pre-configured to connect"
echo -e "  to these defaults out-of-the-box. You can now start the application:"
echo -e "    ${YELLOW}python3 app.py${NC}\n"
