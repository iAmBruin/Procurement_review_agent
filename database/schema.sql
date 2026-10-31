-- 采购履约异常审查 Agent：MySQL 数据表结构
-- 目标库：procurement_agent（由 init_database 按 config.DB_NAME 创建）
-- 说明：本文件只含建表语句，不含建库/切换库，方便程序内幂等执行。

-- 1. 采购单主表：一张采购单一条记录
CREATE TABLE IF NOT EXISTS purchase_orders (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    po_number VARCHAR(50) NOT NULL COMMENT '采购单号，如 PO-001',
    supplier_code VARCHAR(50) NULL COMMENT '供应商编码，可选',
    supplier_name VARCHAR(200) NOT NULL COMMENT '供应商名称',
    order_date DATE NULL COMMENT '采购单日期',
    currency CHAR(3) NOT NULL DEFAULT 'CNY' COMMENT '币种，如 CNY、USD、TWD',
    status ENUM('draft','open','partially_received','received','closed','cancelled')
        NOT NULL DEFAULT 'open' COMMENT '采购单状态',
    remarks VARCHAR(500) NULL COMMENT '备注',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_purchase_orders_po_number (po_number),
    KEY idx_purchase_orders_supplier (supplier_name),
    KEY idx_purchase_orders_order_date (order_date)
) ENGINE=InnoDB COMMENT='采购单主表';

-- 2. 采购商品明细：一张采购单可以有多条商品明细
CREATE TABLE IF NOT EXISTS purchase_order_items (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    po_id BIGINT UNSIGNED NOT NULL COMMENT '关联 purchase_orders.id',
    line_no INT UNSIGNED NOT NULL COMMENT '采购单行号',
    sku VARCHAR(80) NULL COMMENT '商品编码',
    product_name VARCHAR(255) NOT NULL COMMENT '商品名称',
    specification VARCHAR(255) NULL COMMENT '规格型号',
    unit VARCHAR(30) NOT NULL DEFAULT '件' COMMENT '计量单位',
    ordered_qty DECIMAL(14,3) NOT NULL COMMENT '采购数量',
    unit_price DECIMAL(14,4) NOT NULL COMMENT '采购单价',
    tax_rate DECIMAL(7,4) NOT NULL DEFAULT 0 COMMENT '税率比例，例如 0.05 表示 5%',
    remarks VARCHAR(500) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_po_item_line (po_id, line_no),
    KEY idx_po_items_sku (sku),
    CONSTRAINT fk_po_items_po
        FOREIGN KEY (po_id) REFERENCES purchase_orders(id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT chk_po_ordered_qty CHECK (ordered_qty >= 0),
    CONSTRAINT chk_po_unit_price CHECK (unit_price >= 0)
) ENGINE=InnoDB COMMENT='采购商品明细';

-- 3. 收货明细：同一采购商品可分多次收货
CREATE TABLE IF NOT EXISTS receipt_items (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    receipt_no VARCHAR(50) NOT NULL COMMENT '收货单号',
    po_item_id BIGINT UNSIGNED NOT NULL COMMENT '关联 purchase_order_items.id',
    received_date DATE NULL COMMENT '实际收货日期',
    received_qty DECIMAL(14,3) NOT NULL COMMENT '本次收货数量',
    received_by VARCHAR(100) NULL COMMENT '收货人/录入人',
    remarks VARCHAR(500) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_receipt_no (receipt_no),
    KEY idx_receipt_po_item (po_item_id),
    KEY idx_receipt_date (received_date),
    CONSTRAINT fk_receipt_po_item
        FOREIGN KEY (po_item_id) REFERENCES purchase_order_items(id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT chk_received_qty CHECK (received_qty >= 0)
) ENGINE=InnoDB COMMENT='收货明细';

-- 4. 发票明细：一张发票可对应多个采购商品，也可分批开票
CREATE TABLE IF NOT EXISTS invoice_items (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    invoice_no VARCHAR(50) NOT NULL COMMENT '发票号码',
    po_item_id BIGINT UNSIGNED NOT NULL COMMENT '关联 purchase_order_items.id',
    invoice_date DATE NULL COMMENT '发票日期',
    invoiced_qty DECIMAL(14,3) NOT NULL COMMENT '本条发票对应数量',
    unit_price DECIMAL(14,4) NOT NULL COMMENT '发票单价',
    tax_rate DECIMAL(7,4) NOT NULL DEFAULT 0 COMMENT '税率比例，例如 0.05 表示 5%',
    currency CHAR(3) NOT NULL DEFAULT 'CNY',
    invoice_status ENUM('received','verified','rejected','paid')
        NOT NULL DEFAULT 'received' COMMENT '发票处理状态',
    remarks VARCHAR(500) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_invoice_no (invoice_no),
    KEY idx_invoice_po_item (po_item_id),
    KEY idx_invoice_date (invoice_date),
    CONSTRAINT fk_invoice_po_item
        FOREIGN KEY (po_item_id) REFERENCES purchase_order_items(id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT chk_invoiced_qty CHECK (invoiced_qty >= 0),
    CONSTRAINT chk_invoice_unit_price CHECK (unit_price >= 0)
) ENGINE=InnoDB COMMENT='发票明细';

-- 5. 异常记录：由 Python 检查逻辑生成，人工审核后更新状态
CREATE TABLE IF NOT EXISTS exception_cases (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    exception_code VARCHAR(50) NOT NULL COMMENT '异常编号，如 EX-0001',
    po_id BIGINT UNSIGNED NOT NULL COMMENT '关联采购单',
    po_item_id BIGINT UNSIGNED NULL COMMENT '关联采购商品明细；订单级异常可为空',
    exception_type VARCHAR(80) NOT NULL COMMENT '如 QTY_EXCEEDED、UNIT_PRICE_MISMATCH',
    severity ENUM('low','medium','high','critical')
        NOT NULL DEFAULT 'medium' COMMENT '异常严重程度',
    status ENUM('open','under_review','confirmed','rejected','resolved')
        NOT NULL DEFAULT 'open' COMMENT '异常处理状态',
    title VARCHAR(255) NOT NULL COMMENT '异常标题',
    description TEXT NULL COMMENT '异常描述',
    expected_value DECIMAL(16,4) NULL COMMENT '预期值（如收货数量）',
    actual_value DECIMAL(16,4) NULL COMMENT '实际值（如开票数量）',
    difference_value DECIMAL(16,4) NULL COMMENT '差异值',
    evidence_json JSON NULL COMMENT '证据摘要/相关单据编号，JSON 格式',
    detected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at DATETIME NULL,
    resolved_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_exception_code (exception_code),
    KEY idx_exception_po (po_id),
    KEY idx_exception_status (status),
    KEY idx_exception_type (exception_type),
    KEY idx_exception_detected_at (detected_at),
    CONSTRAINT fk_exception_po
        FOREIGN KEY (po_id) REFERENCES purchase_orders(id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_exception_po_item
        FOREIGN KEY (po_item_id) REFERENCES purchase_order_items(id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB COMMENT='采购异常记录';

-- 6. 人工审核记录：保留每一次审核动作，形成审计轨迹
CREATE TABLE IF NOT EXISTS review_logs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    exception_id BIGINT UNSIGNED NOT NULL COMMENT '关联 exception_cases.id',
    action VARCHAR(50) NOT NULL COMMENT '如 comment、confirm、reject、request_documents、resolve',
    from_status VARCHAR(30) NULL COMMENT '操作前状态',
    to_status VARCHAR(30) NULL COMMENT '操作后状态',
    reviewer VARCHAR(100) NOT NULL COMMENT '审核人/操作人',
    comment TEXT NULL COMMENT '审核意见或补充说明',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_review_exception (exception_id),
    KEY idx_review_created_at (created_at),
    CONSTRAINT fk_review_exception
        FOREIGN KEY (exception_id) REFERENCES exception_cases(id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB COMMENT='人工审核日志';