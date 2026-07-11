from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import relationship
from database import Base
import enum


class BotStatus(str, enum.Enum):
    stopped = "stopped"
    starting = "starting"
    running = "running"
    error = "error"


class SourceType(str, enum.Enum):
    manual = "manual"
    api = "api"


class DeliveryMode(str, enum.Enum):
    manual = "manual"
    api_auto = "api_auto"


class AuthType(str, enum.Enum):
    x_api_key = "x_api_key"
    bearer = "bearer"


class ApiType(str, enum.Enum):
    zampto_standard = "zampto_standard"
    custom = "custom"


class OrderStatus(str, enum.Enum):
    pending_manual = "pending_manual"
    processing_api = "processing_api"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


def now():
    return datetime.utcnow()


class AdminUser(Base):
    __tablename__ = "admin_users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class Setting(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class TelegramBotConfig(Base):
    __tablename__ = "telegram_bot_config"
    id = Column(Integer, primary_key=True, index=True)
    bot_token_encrypted = Column(Text, nullable=True)
    admin_telegram_id = Column(String(100), nullable=True)
    welcome_message = Column(Text, nullable=True)
    support_username = Column(String(100), nullable=True)
    is_enabled = Column(Boolean, default=False)
    bot_status = Column(SAEnum(BotStatus), default=BotStatus.stopped)
    bot_username = Column(String(100), nullable=True)
    bot_name = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String(50), unique=True, nullable=False)
    username = Column(String(100), nullable=True)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    total_orders = Column(Integer, default=0)
    total_spent = Column(Float, default=0.0)
    is_banned = Column(Boolean, default=False)
    last_active_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    orders = relationship("Order", back_populates="user", foreign_keys="Order.telegram_user_id")


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    product_code = Column(String(100), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    image_path = Column(String(500), nullable=True)
    sale_price = Column(Float, default=0.0)
    source_type = Column(SAEnum(SourceType), default=SourceType.manual)
    delivery_mode = Column(SAEnum(DeliveryMode), default=DeliveryMode.manual)
    is_active = Column(Boolean, default=True)
    sold_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    sources = relationship("ProductSource", back_populates="product", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="product")


class ApiConnection(Base):
    __tablename__ = "api_connections"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    base_url = Column(String(500), nullable=False)
    api_key_encrypted = Column(Text, nullable=True)
    auth_type = Column(SAEnum(AuthType), default=AuthType.x_api_key)
    api_type = Column(SAEnum(ApiType), default=ApiType.zampto_standard)
    is_active = Column(Boolean, default=True)
    sync_interval_minutes = Column(Integer, default=60)
    last_sync_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    api_products = relationship("ApiProduct", back_populates="connection", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="api_connection")


class ApiProduct(Base):
    __tablename__ = "api_products"
    id = Column(Integer, primary_key=True, index=True)
    api_connection_id = Column(Integer, ForeignKey("api_connections.id"), nullable=False)
    external_product_id = Column(String(255), nullable=False)
    external_name = Column(String(500), nullable=True)
    external_price = Column(Float, nullable=True)
    external_stock = Column(Integer, nullable=True)
    external_status = Column(String(100), nullable=True)
    raw_json = Column(Text, nullable=True)
    last_sync_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    connection = relationship("ApiConnection", back_populates="api_products")
    product_sources = relationship("ProductSource", back_populates="api_product")


class ProductSource(Base):
    __tablename__ = "product_sources"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    api_product_id = Column(Integer, ForeignKey("api_products.id"), nullable=False)
    priority = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)
    last_cost = Column(Float, nullable=True)
    last_stock = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    product = relationship("Product", back_populates="sources")
    api_product = relationship("ApiProduct", back_populates="product_sources")
    order_attempts = relationship("OrderSourceAttempt", back_populates="product_source")


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    order_code = Column(String(100), unique=True, nullable=False)
    telegram_user_id = Column(String(50), ForeignKey("users.telegram_id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, default=1)
    unit_price = Column(Float, default=0.0)
    total_price = Column(Float, default=0.0)
    api_connection_id = Column(Integer, ForeignKey("api_connections.id"), nullable=True)
    external_order_id = Column(String(255), nullable=True)
    delivery_data = Column(Text, nullable=True)
    status = Column(SAEnum(OrderStatus), default=OrderStatus.pending_manual)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    user = relationship("User", back_populates="orders", foreign_keys=[telegram_user_id])
    product = relationship("Product", back_populates="orders")
    api_connection = relationship("ApiConnection", back_populates="orders")
    source_attempts = relationship("OrderSourceAttempt", back_populates="order", cascade="all, delete-orphan")


class OrderSourceAttempt(Base):
    __tablename__ = "order_source_attempts"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_source_id = Column(Integer, ForeignKey("product_sources.id"), nullable=False)
    attempt_number = Column(Integer, default=1)
    status = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    external_order_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    order = relationship("Order", back_populates="source_attempts")
    product_source = relationship("ProductSource", back_populates="order_attempts")


class ActivityLog(Base):
    __tablename__ = "activity_logs"
    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    user_type = Column(String(50), nullable=True)
    user_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)
