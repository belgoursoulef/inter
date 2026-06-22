CREATE DATABASE IF NOT EXISTS carhorizon;
USE carhorizon;

CREATE TABLE IF NOT EXISTS employees (
    badge_id VARCHAR(50) PRIMARY KEY,
    nom VARCHAR(100) NOT NULL,
    prenom VARCHAR(100) NOT NULL,
    service VARCHAR(100) NOT NULL,
    color VARCHAR(20) NOT NULL,
    initials VARCHAR(5) NOT NULL
);

-- Seed employees
INSERT INTO employees (badge_id, nom, prenom, service, color, initials) VALUES
('EMP001', 'BELGOUR', 'Aicha Soulef', 'IT', 'blue', 'AB'),
('EMP002', 'ROLIN', 'Tom', 'Production', 'amber', 'TR'),
('EMP003', 'Balde', 'Mamadou', 'Administratif', 'green', 'MB'),
('EMP004', 'Diahouila', 'Ferancel Iverson', 'Production', 'amber', 'FD'),
('EMP005', 'Jacaton', 'Paul', 'IT', 'blue', 'PJ')
ON DUPLICATE KEY UPDATE badge_id=badge_id;

CREATE TABLE IF NOT EXISTS device_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    device_name VARCHAR(100) NOT NULL,
    log_level VARCHAR(20) NOT NULL,
    message TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Seed with some demo logs relative to the current time
INSERT INTO device_logs (device_name, log_level, message, timestamp) VALUES
('Camera 1 (Entrance)', 'INFO', 'Access GRANTED - Aicha Soulef BELGOUR (IT)', DATE_SUB(NOW(), INTERVAL 10 MINUTE)),
('Camera 1 (Entrance)', 'INFO', 'Access GRANTED - Tom ROLIN (Production)', DATE_SUB(NOW(), INTERVAL 22 MINUTE)),
('Camera 1 (Entrance)', 'INFO', 'Access GRANTED - Paul JACATON (IT)', DATE_SUB(NOW(), INTERVAL 29 MINUTE)),
('Camera 1 (Entrance)', 'INFO', 'Access GRANTED - Mamadou BALDE (Administratif)', DATE_SUB(NOW(), INTERVAL 38 MINUTE)),
('Camera 1 (Entrance)', 'INFO', 'Access GRANTED - Ferancel Iverson Diahouila (Production)', DATE_SUB(NOW(), INTERVAL 53 MINUTE)),
('Camera 1 (Entrance)', 'WARNING', 'Access DENIED - Unknown badge scanned: BADGE-4921X', DATE_SUB(NOW(), INTERVAL 72 MINUTE)),
('Camera 1 (Entrance)', 'WARNING', 'Access DENIED - Unknown badge scanned: BADGE-3312A', DATE_SUB(NOW(), INTERVAL 86 MINUTE)),
('Camera 2 (Garage)', 'ALERT', 'Cam 2 (Garage) - Reconnexion RTSP après déconnexion', DATE_SUB(NOW(), INTERVAL 6 HOUR)),
('Camera 2 (Garage)', 'INFO', 'Aucune intrusion — surveillance nocturne normale', DATE_SUB(NOW(), INTERVAL 14 HOUR)),
('System', 'INFO', 'Système de surveillance démarré — threads actifs', DATE_SUB(NOW(), INTERVAL 16 HOUR));
