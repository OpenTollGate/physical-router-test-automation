import enum
import time
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "micro-vpn-ansible", "api"))

from sqlalchemy import Column, Integer, String, Float, Boolean, Enum, Text, ForeignKey, Index, create_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

import pytest


Base = declarative_base()


class SubscriptionState(enum.Enum):
    PENDING_PAYMENT = "pending_payment"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Client(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True)
    client_id = Column(String(12), unique=True, nullable=False, index=True)
    vpn_subnet = Column(String(20), nullable=False)
    vpn_ip = Column(String(20), nullable=False)
    private_key = Column(String(50), nullable=False)
    public_key = Column(String, nullable=False)
    wireguard_registered = Column(Boolean, default=False)
    created_at = Column(Float, default=time.time)
    subscriptions = relationship("Subscription", back_populates="client")
    port_allocations = relationship("PortAllocation", back_populates="client")


class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True)
    client_id_str = Column(String(12), ForeignKey("clients.client_id"), nullable=False, index=True)
    state = Column(Enum(SubscriptionState), default=SubscriptionState.PENDING_PAYMENT)
    mint_quote_id = Column(String(100), nullable=True, index=True)
    bolt11_invoice = Column(Text, nullable=True)
    payment_hash = Column(String(100), nullable=True)
    amount_sats = Column(Integer, nullable=False)
    duration_days = Column(Integer, nullable=False)
    expires_at = Column(Float, nullable=True)
    created_at = Column(Float, default=time.time)
    paid_at = Column(Float, nullable=True)
    renewed_count = Column(Integer, default=0)
    client = relationship("Client", back_populates="subscriptions")


class PortAllocation(Base):
    __tablename__ = "port_allocations"
    id = Column(Integer, primary_key=True)
    client_id_str = Column(String(12), ForeignKey("clients.client_id"), nullable=False, index=True)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id"), nullable=False, index=True)
    public_port = Column(Integer, nullable=False, unique=True, index=True)
    target_port = Column(Integer, nullable=False)
    protocol = Column(String(10), default="tcp")
    iptables_rule_added = Column(Boolean, default=False)
    created_at = Column(Float, default=time.time)
    client = relationship("Client", back_populates="port_allocations")
    subscription = relationship("Subscription")
    __table_args__ = (Index("ix_port_alloc_public", "public_port", unique=True),)


@pytest.fixture
def db_session(tmp_path):
    db_path = str(tmp_path / "test.db")
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestClientModel:
    def test_create_client(self, db_session):
        c = Client(
            client_id="client001",
            vpn_subnet="10.254.1.0/30",
            vpn_ip="10.254.1.1",
            private_key="abc123",
            public_key="pub456",
        )
        db_session.add(c)
        db_session.commit()
        assert c.id is not None
        assert c.client_id == "client001"
        assert not c.wireguard_registered

    def test_client_id_unique(self, db_session):
        c1 = Client(client_id="client001", vpn_subnet="10.254.1.0/30", vpn_ip="10.254.1.1",
                     private_key="a", public_key="b")
        c2 = Client(client_id="client001", vpn_subnet="10.254.2.0/30", vpn_ip="10.254.2.1",
                     private_key="c", public_key="d")
        db_session.add(c1)
        db_session.commit()
        db_session.add(c2)
        with pytest.raises(Exception):
            db_session.commit()

    def test_client_subnet_pattern(self, db_session):
        for i in range(1, 6):
            c = Client(
                client_id=f"client{i:03d}",
                vpn_subnet=f"10.254.{i}.0/30",
                vpn_ip=f"10.254.{i}.1",
                private_key=f"priv{i}",
                public_key=f"pub{i}",
            )
            db_session.add(c)
        db_session.commit()
        assert db_session.query(Client).count() == 5


class TestSubscriptionModel:
    def test_create_subscription(self, db_session):
        c = Client(client_id="client001", vpn_subnet="10.254.1.0/30", vpn_ip="10.254.1.1",
                    private_key="a", public_key="b")
        db_session.add(c)
        db_session.flush()
        s = Subscription(
            client_id_str="client001",
            state=SubscriptionState.PENDING_PAYMENT,
            amount_sats=2000,
            duration_days=30,
        )
        db_session.add(s)
        db_session.commit()
        assert s.id is not None
        assert s.state == SubscriptionState.PENDING_PAYMENT
        assert s.renewed_count == 0

    def test_subscription_state_transitions(self, db_session):
        c = Client(client_id="client001", vpn_subnet="10.254.1.0/30", vpn_ip="10.254.1.1",
                    private_key="a", public_key="b")
        db_session.add(c)
        db_session.flush()
        s = Subscription(client_id_str="client001", amount_sats=1000, duration_days=30)
        db_session.add(s)
        db_session.commit()

        assert s.state == SubscriptionState.PENDING_PAYMENT
        s.state = SubscriptionState.ACTIVE
        s.paid_at = time.time()
        s.expires_at = time.time() + 86400 * 30
        db_session.commit()
        assert s.state == SubscriptionState.ACTIVE

        s.state = SubscriptionState.EXPIRED
        db_session.commit()
        assert s.state == SubscriptionState.EXPIRED

    def test_renewal_increments_counter(self, db_session):
        c = Client(client_id="client001", vpn_subnet="10.254.1.0/30", vpn_ip="10.254.1.1",
                    private_key="a", public_key="b")
        db_session.add(c)
        db_session.flush()
        s = Subscription(client_id_str="client001", state=SubscriptionState.ACTIVE,
                          amount_sats=1000, duration_days=30)
        db_session.add(s)
        db_session.commit()
        assert s.renewed_count == 0
        s.renewed_count += 1
        db_session.commit()
        assert s.renewed_count == 1


class TestPortAllocationModel:
    def test_create_allocation(self, db_session):
        c = Client(client_id="client001", vpn_subnet="10.254.1.0/30", vpn_ip="10.254.1.1",
                    private_key="a", public_key="b")
        db_session.add(c)
        db_session.flush()
        s = Subscription(client_id_str="client001", amount_sats=1000, duration_days=30)
        db_session.add(s)
        db_session.flush()
        a = PortAllocation(
            client_id_str="client001",
            subscription_id=s.id,
            public_port=10080,
            target_port=8080,
        )
        db_session.add(a)
        db_session.commit()
        assert a.id is not None
        assert a.public_port == 10080
        assert a.target_port == 8080
        assert not a.iptables_rule_added

    def test_public_port_unique(self, db_session):
        c = Client(client_id="client001", vpn_subnet="10.254.1.0/30", vpn_ip="10.254.1.1",
                    private_key="a", public_key="b")
        db_session.add(c)
        db_session.flush()
        s = Subscription(client_id_str="client001", amount_sats=1000, duration_days=30)
        db_session.add(s)
        db_session.flush()
        a1 = PortAllocation(client_id_str="client001", subscription_id=s.id, public_port=10080, target_port=80)
        a2 = PortAllocation(client_id_str="client001", subscription_id=s.id, public_port=10080, target_port=443)
        db_session.add(a1)
        db_session.commit()
        db_session.add(a2)
        with pytest.raises(Exception):
            db_session.commit()

    def test_relationships(self, db_session):
        c = Client(client_id="client001", vpn_subnet="10.254.1.0/30", vpn_ip="10.254.1.1",
                    private_key="a", public_key="b")
        db_session.add(c)
        db_session.flush()
        s = Subscription(client_id_str="client001", state=SubscriptionState.ACTIVE,
                          amount_sats=2000, duration_days=30)
        db_session.add(s)
        db_session.flush()
        a1 = PortAllocation(client_id_str="client001", subscription_id=s.id, public_port=10080, target_port=80)
        a2 = PortAllocation(client_id_str="client001", subscription_id=s.id, public_port=10443, target_port=443)
        db_session.add_all([a1, a2])
        db_session.commit()
        assert len(c.port_allocations) == 2
        assert len(c.subscriptions) == 1
        assert a1.subscription.state == SubscriptionState.ACTIVE


class TestPortPoolLogic:
    def test_parse_port_ranges(self):
        ranges_str = "10000-20000,30000-40000"
        result = []
        for part in ranges_str.split(","):
            part = part.strip()
            if "-" in part:
                start, end = part.split("-", 1)
                result.append((int(start), int(end)))
        assert result == [(10000, 20000), (30000, 40000)]

    def test_available_ports_excludes_allocated(self, db_session):
        c = Client(client_id="client001", vpn_subnet="10.254.1.0/30", vpn_ip="10.254.1.1",
                    private_key="a", public_key="b")
        db_session.add(c)
        db_session.flush()
        s = Subscription(client_id_str="client001", amount_sats=1000, duration_days=30)
        db_session.add(s)
        db_session.flush()
        db_session.add(PortAllocation(client_id_str="client001", subscription_id=s.id,
                                       public_port=10080, target_port=80))
        db_session.commit()

        allocated = set(p.public_port for p in db_session.query(PortAllocation).all())
        assert 10080 in allocated
        assert 10081 not in allocated

    def test_available_ports_excludes_reserved(self):
        reserved = set()
        for p in "80,443,51820".split(","):
            p = p.strip()
            if p:
                reserved.add(int(p))
        assert 80 in reserved
        assert 443 in reserved
        assert 51820 in reserved
        assert 8080 not in reserved

    def test_reserved_port_ranges(self):
        reserved = set()
        for p in "8085-8095".split(","):
            p = p.strip()
            if "-" in p:
                start, end = p.split("-", 1)
                reserved.update(range(int(start), int(end) + 1))
            elif p:
                reserved.add(int(p))
        assert 8085 in reserved
        assert 8090 in reserved
        assert 8095 in reserved
        assert 8096 not in reserved
