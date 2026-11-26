-- DataAgent 统一账号：一次性数据迁移（手动执行，可重复执行）
--
-- 前提：已备份（scripts/backup-db.sh），且新版 backend 已启动过一次，
--       Alembic 20261123_000042 已建好 da_user / da_user_identity / da_role / da_agent_grant。
-- 执行：scripts/migrate-accounts.sh（在 backend 镜像内用 mysql 客户端执行本文件）。
--
-- 做四件事：
--   1. 为旧会话（da_agent_topic.auth_user_id）和已发布助手名单（allowed_users）中出现的每个旧用户
--      `<来源>:<标识>` 建普通账号并记录外部身份，不设密码。
--      `local:<用户名>` 直接对应同名账号（例如 local:admin → admin）。
--   2. 按外部身份回填旧会话的 user_id（不区分大小写：SSO:u123 与 SSO:U123 是同一用户）。
--   3. 已发布助手的 allowed_users 转为 da_agent_grant，助手配置和草稿只保留 {"mode": ...}。
--   4. 已退役的「仅登录用户」(authenticated) 改写为「全部用户」(all)。
-- 不处理密码：管理员用 python -m core.accounts create-user --username admin --password ... --admin 设置。

SET NAMES utf8mb4;
SET @user_role_id = (SELECT id FROM da_role WHERE role_key = 'user');

-- ---------------------------------------------------------------------------
-- 1. 旧用户 → 账号 + 外部身份
-- ---------------------------------------------------------------------------
DROP TEMPORARY TABLE IF EXISTS tmp_legacy_principal;
CREATE TEMPORARY TABLE tmp_legacy_principal (
    -- Same collation as da_user_identity: principals differing only in case are one user.
    principal VARCHAR(255) COLLATE utf8mb4_unicode_ci NOT NULL PRIMARY KEY,
    source VARCHAR(128) COLLATE utf8mb4_unicode_ci NOT NULL,
    subject VARCHAR(255) COLLATE utf8mb4_unicode_ci NOT NULL,
    legacy_name VARCHAR(255) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT '',
    -- Same collation as da_user.username, so "taken" means what the unique key means.
    desired VARCHAR(128) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT ''
) DEFAULT CHARSET = utf8mb4;

INSERT IGNORE INTO tmp_legacy_principal (principal, source, subject, legacy_name)
SELECT p.principal,
       SUBSTRING_INDEX(p.principal, ':', 1),
       SUBSTRING(p.principal, LOCATE(':', p.principal) + 1),
       p.legacy_name
FROM (
    SELECT auth_user_id AS principal, MAX(auth_username) AS legacy_name
    FROM da_agent_topic
    WHERE auth_user_id <> ''
    GROUP BY auth_user_id
    UNION ALL
    SELECT CONVERT(jt.principal USING utf8mb4) COLLATE utf8mb4_unicode_ci, ''
    FROM da_agent_profile pr,
         JSON_TABLE(pr.visibility_json, '$.allowed_users[*]' COLUMNS (principal VARCHAR(255) PATH '$')) jt
    WHERE JSON_VALID(pr.visibility_json) AND jt.principal <> ''
) p
WHERE LOCATE(':', p.principal) > 1 AND SUBSTRING(p.principal, LOCATE(':', p.principal) + 1) <> '';

-- Already bound (re-run, or the user logged in since): nothing to do.
DELETE p FROM tmp_legacy_principal p
JOIN da_user_identity i ON i.source = p.source AND i.subject = p.subject;

-- local:<用户名> is the local account of that name.
INSERT IGNORE INTO da_user_identity (source, subject, user_id)
SELECT p.source, p.subject, u.id
FROM tmp_legacy_principal p JOIN da_user u ON u.username = p.subject
WHERE p.source = 'local';
DELETE p FROM tmp_legacy_principal p
JOIN da_user_identity i ON i.source = p.source AND i.subject = p.subject;

-- New accounts. First try the old user name (or the subject); a name that is
-- taken or shared by two principals falls back to <来源>_<标识>.
UPDATE tmp_legacy_principal SET desired = LEFT(IF(legacy_name <> '', legacy_name, subject), 128);
DROP TEMPORARY TABLE IF EXISTS tmp_desired_taken;
CREATE TEMPORARY TABLE tmp_desired_taken (
    desired VARCHAR(128) COLLATE utf8mb4_unicode_ci NOT NULL PRIMARY KEY
) DEFAULT CHARSET = utf8mb4;
INSERT IGNORE INTO tmp_desired_taken
SELECT desired FROM tmp_legacy_principal GROUP BY desired HAVING COUNT(*) > 1;
INSERT IGNORE INTO tmp_desired_taken
SELECT p.desired FROM tmp_legacy_principal p JOIN da_user u ON u.username = p.desired;
UPDATE tmp_legacy_principal p JOIN tmp_desired_taken t ON t.desired = p.desired
SET p.desired = LEFT(CONCAT(p.source, '_', p.subject), 128);
-- Anything still colliding is reported below and left for the admin.
DELETE FROM tmp_desired_taken;
INSERT IGNORE INTO tmp_desired_taken
SELECT desired FROM tmp_legacy_principal GROUP BY desired HAVING COUNT(*) > 1;
INSERT IGNORE INTO tmp_desired_taken
SELECT p.desired FROM tmp_legacy_principal p JOIN da_user u ON u.username = p.desired;

INSERT INTO da_user (username, display_name, password_hash, is_active)
SELECT p.desired, IF(p.legacy_name <> '', p.legacy_name, p.desired), '', 1
FROM tmp_legacy_principal p
WHERE p.desired NOT IN (SELECT desired FROM tmp_desired_taken);

INSERT INTO da_user_identity (source, subject, user_id)
SELECT p.source, p.subject, u.id
FROM tmp_legacy_principal p JOIN da_user u ON u.username = p.desired
WHERE p.desired NOT IN (SELECT desired FROM tmp_desired_taken);

INSERT IGNORE INTO da_user_role (user_id, role_id)
SELECT u.id, @user_role_id
FROM tmp_legacy_principal p JOIN da_user u ON u.username = p.desired
WHERE p.desired NOT IN (SELECT desired FROM tmp_desired_taken);

SELECT p.principal AS `未建账号的旧用户（用户名冲突，请管理员处理）`
FROM tmp_legacy_principal p
WHERE p.desired IN (SELECT desired FROM tmp_desired_taken);

-- ---------------------------------------------------------------------------
-- 2. 回填旧会话归属（不区分大小写）
-- 本脚本的 UPDATE 都写 updated_at = updated_at：列带 ON UPDATE CURRENT_TIMESTAMP，否则时间会变成迁移时刻。
-- ---------------------------------------------------------------------------
UPDATE da_agent_topic t
JOIN da_user_identity i
  ON t.auth_user_id = CONCAT(i.source, ':', i.subject)
SET t.user_id = i.user_id, t.updated_at = t.updated_at
WHERE t.user_id IS NULL AND t.auth_user_id <> '';

-- ---------------------------------------------------------------------------
-- 3. 已发布名单 → da_agent_grant；4. authenticated → all
-- ---------------------------------------------------------------------------
INSERT IGNORE INTO da_agent_grant (agent_id, principal_type, principal_id)
SELECT pr.agent_id, 'user', i.user_id
FROM da_agent_profile pr,
     JSON_TABLE(pr.visibility_json, '$.allowed_users[*]' COLUMNS (principal VARCHAR(255) PATH '$')) jt,
     da_user_identity i
WHERE JSON_VALID(pr.visibility_json)
  AND CONVERT(jt.principal USING utf8mb4) COLLATE utf8mb4_unicode_ci = CONCAT(i.source, ':', i.subject);

UPDATE da_agent_profile
SET visibility_json = JSON_OBJECT(
    'mode', IF(JSON_UNQUOTE(JSON_EXTRACT(visibility_json, '$.mode')) = 'selected', 'selected', 'all')),
    updated_at = updated_at
WHERE JSON_VALID(visibility_json);

UPDATE da_agent_draft
SET draft_json = JSON_SET(draft_json, '$.visibility', JSON_OBJECT(
    'mode', IF(JSON_UNQUOTE(JSON_EXTRACT(draft_json, '$.visibility.mode')) = 'selected', 'selected', 'all'))),
    updated_at = updated_at
WHERE JSON_VALID(draft_json) AND JSON_EXTRACT(draft_json, '$.visibility') IS NOT NULL;

UPDATE da_agent_draft
SET published_json = JSON_SET(published_json, '$.visibility', JSON_OBJECT(
    'mode', IF(JSON_UNQUOTE(JSON_EXTRACT(published_json, '$.visibility.mode')) = 'selected', 'selected', 'all'))),
    updated_at = updated_at
WHERE published_json IS NOT NULL AND JSON_VALID(published_json)
  AND JSON_EXTRACT(published_json, '$.visibility') IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 结果
-- ---------------------------------------------------------------------------
SELECT
    (SELECT COUNT(*) FROM da_user) AS `账号数`,
    (SELECT COUNT(*) FROM da_agent_topic WHERE auth_user_id <> '' AND user_id IS NOT NULL) AS `已归属的旧会话`,
    (SELECT COUNT(*) FROM da_agent_topic WHERE user_id IS NULL) AS `无归属会话（仅管理员审计可见）`,
    (SELECT COUNT(*) FROM da_agent_grant) AS `助手授权数`;

DROP TEMPORARY TABLE IF EXISTS tmp_desired_taken;
DROP TEMPORARY TABLE IF EXISTS tmp_legacy_principal;
