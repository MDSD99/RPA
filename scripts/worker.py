#!/usr/bin/env python3
"""Run code-registered workflows independently from the web management panel."""

from __future__ import annotations

import argparse
import signal
import sys
from threading import Event

from app.core.config import DatabaseSettings
from app.database.connection import create_db_engine, create_session_factory
from app.services.execution_service import ExecutionService
from app.services.management_service import ManagementService
from app.workers.runtime import WorkflowWorkerPool
from app.workflows.registered import build_registry


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list", action="store_true", help="List registered workflow names without DB access"
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--lease-seconds", type=float, default=30)
    args = parser.parse_args(argv)
    engine = None
    pool = None
    old_handlers = {}
    requested_stop = Event()

    def stop_requested(_signum, _frame):
        requested_stop.set()

    try:
        registry = build_registry()
        if args.list:
            print("\n".join(registry.names()) or "Kayıtlı otomasyon akışı yok.")
            return 0
        if not registry.names():
            print(
                "Çalıştırılacak otomasyon tanımlanmamış. Akışı app/workflows/registered.py "
                "içindeki build_registry işlevine kaydedin.",
                file=sys.stderr,
            )
            return 2
        settings = DatabaseSettings.from_env()
        engine = create_db_engine(settings)
        factory = create_session_factory(engine)
        registry = build_registry(factory)
        management = ManagementService(factory, system=True)
        pool = WorkflowWorkerPool(
            ExecutionService(factory, system=True),
            management,
            registry,
            worker_count=args.workers,
            poll_seconds=args.poll_seconds,
            lease_seconds=args.lease_seconds,
        )
        if not management.healthy():
            print(
                "Veritabanı şeması hazır değil; yetkili migration işlemini tamamlayın.",
                file=sys.stderr,
            )
            return 2
        for signum in (signal.SIGTERM, signal.SIGINT):
            old_handlers[signum] = signal.signal(signum, stop_requested)
        pool.start()
        print(f"{args.workers} worker, {len(registry.names())} kayıtlı akış için çalışıyor.")
        while not requested_stop.wait(0.5):
            pass
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        print(
            "Worker başlatılamadı veya çalışması kesildi. Akış kaydını ve environment "
            "yapılandırmasını kontrol edin; hassas hata ayrıntıları bastırıldı.",
            file=sys.stderr,
        )
        return 1
    finally:
        if pool is not None:
            pool.stop()
        if engine is not None:
            engine.dispose()
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
