CREATE DATABASE IF NOT EXISTS dataagent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'dataagent'@'%' IDENTIFIED BY 'dataagent123';
GRANT ALL PRIVILEGES ON dataagent.* TO 'dataagent'@'%';
