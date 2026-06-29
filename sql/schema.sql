-- ============================================================================
-- blogbook 数据库 schema
-- 设计目标：
--   1. 统一管理多个博客来源（paulgraham / naval / 未来更多）
--   2. 完整保留原始网页 + 解析后的结构化内容（标题/作者/发布时间/正文）
--   3. 翻译结果与原文解耦，支持多目标语言、增量重译（按内容 hash）
--   4. 完整审计：每次抓取(fetch_log)、每次模型调用(api_call) 全量留痕
-- 字符集统一 utf8mb4，正文用 LONGTEXT 容纳长文 + 原始 HTML
-- ============================================================================

SET NAMES utf8mb4;

-- 博客来源 ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
  source_key  VARCHAR(64)  NOT NULL,            -- paulgraham / naval
  name        VARCHAR(255) NOT NULL,            -- Paul Graham / Naval Ravikant
  base_url    VARCHAR(512) NOT NULL,
  kind        VARCHAR(32)  NOT NULL,            -- static_html / wordpress_api
  lang        VARCHAR(16)  NOT NULL DEFAULT 'en',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_source_key (source_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 文章（原文）------------------------------------------------------------
CREATE TABLE IF NOT EXISTS articles (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  source_id       INT UNSIGNED NOT NULL,
  slug            VARCHAR(255) NOT NULL,         -- PG: greatwork ; Naval: industrial
  url             VARCHAR(768) NOT NULL,
  title           VARCHAR(512) NOT NULL,
  author          VARCHAR(255) NULL,
  -- 发布时间：published_at 为可排序的真实时间；published_text 保留原始文本(如 "July 2023")
  published_at    DATETIME     NULL,
  published_text  VARCHAR(128) NULL,
  -- chrono_index：按时间正序的全局序号（最早=1），制书时使用
  chrono_index    INT          NULL,
  word_count      INT          NULL,
  -- 原始与解析内容
  raw_html        LONGTEXT     NULL,             -- 完整原始网页 / WP API 原始 JSON
  content_html    LONGTEXT     NULL,             -- 清洗后的正文 HTML（用于制书）
  content_text    LONGTEXT     NULL,             -- 纯文本（用于翻译/字数统计）
  content_hash    CHAR(64)     NULL,             -- content_text 的 sha256，增量判断
  -- 元数据 JSON：categories / tags / wp_id 等来源特有字段
  meta_json       JSON         NULL,
  -- 抓取状态
  http_status     INT          NULL,
  fetched_at      DATETIME     NULL,
  is_external     TINYINT(1)   NOT NULL DEFAULT 0,  -- 链接指向站外/非文章(PG 索引里混入)
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_source_slug (source_id, slug),
  KEY idx_source_chrono (source_id, chrono_index),
  KEY idx_published (published_at),
  CONSTRAINT fk_articles_source FOREIGN KEY (source_id) REFERENCES sources (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 翻译结果 --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS translations (
  id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  article_id         BIGINT UNSIGNED NOT NULL,
  target_lang        VARCHAR(16)  NOT NULL DEFAULT 'zh',
  model              VARCHAR(64)  NOT NULL,
  title_translated   VARCHAR(768) NULL,
  content_translated LONGTEXT     NULL,          -- 译文（Markdown / HTML）
  -- src_hash：翻译时所依据的原文 content_hash；与 articles.content_hash 不一致即为过期
  src_hash           CHAR(64)     NULL,
  status             VARCHAR(32)  NOT NULL DEFAULT 'pending', -- pending/running/done/failed
  prompt_tokens      INT          NULL,
  completion_tokens  INT          NULL,
  error              TEXT         NULL,
  created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_article_lang (article_id, target_lang),
  CONSTRAINT fk_tr_article FOREIGN KEY (article_id) REFERENCES articles (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 核心导读（每篇文章的一句话论点 + 要点，供制书渲染导读卡片/思维导图）------
CREATE TABLE IF NOT EXISTS summaries (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  article_id  BIGINT UNSIGNED NOT NULL,
  model       VARCHAR(64)  NOT NULL,
  thesis_en   VARCHAR(1024) NULL,
  thesis_zh   VARCHAR(1024) NULL,
  points_json JSON         NULL,             -- {"en":[{label,detail}],"zh":[...]}
  src_hash    CHAR(64)     NULL,             -- 原文 hash，增量重做依据
  status      VARCHAR(32)  NOT NULL DEFAULT 'done',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_article (article_id),
  CONSTRAINT fk_sum_article FOREIGN KEY (article_id) REFERENCES articles (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 抓取日志（每次 HTTP 请求留痕）-----------------------------------------
CREATE TABLE IF NOT EXISTS fetch_log (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  source_id   INT UNSIGNED NULL,
  url         VARCHAR(768) NOT NULL,
  http_status INT          NULL,
  bytes       INT          NULL,
  ok          TINYINT(1)   NOT NULL DEFAULT 0,
  note        VARCHAR(512) NULL,
  fetched_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_fetch_source (source_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 模型调用日志（每次翻译请求/响应全量留痕）---------------------------
CREATE TABLE IF NOT EXISTS api_call (
  id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  article_id        BIGINT UNSIGNED NULL,
  target_lang       VARCHAR(16)  NULL,
  model             VARCHAR(64)  NOT NULL,
  request_json      LONGTEXT     NULL,           -- 完整请求体
  response_json     LONGTEXT     NULL,           -- 完整响应体
  prompt_tokens     INT          NULL,
  completion_tokens INT          NULL,
  latency_ms        INT          NULL,
  status            VARCHAR(32)  NULL,           -- ok / error
  error             TEXT         NULL,
  created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_api_article (article_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 制书记录 --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS book_build (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  source_id     INT UNSIGNED NOT NULL,
  lang          VARCHAR(16)  NOT NULL,           -- en / zh
  format        VARCHAR(16)  NOT NULL,           -- epub / pdf / mobi / azw3
  file_path     VARCHAR(768) NOT NULL,
  article_count INT          NULL,
  built_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_book_source (source_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 预置来源 --------------------------------------------------------------
INSERT INTO sources (source_key, name, base_url, kind, lang) VALUES
  ('paulgraham', 'Paul Graham', 'https://paulgraham.com', 'static_html', 'en'),
  ('naval',      'Naval Ravikant', 'https://nav.al', 'wordpress_api', 'en')
ON DUPLICATE KEY UPDATE name=VALUES(name), base_url=VALUES(base_url), kind=VALUES(kind);
