#!/usr/bin/env python3
"""Dispatch saved schedules using explicit environment database configuration."""

from __future__ import annotations

import argparse
import signal
import sys
from threading import Event

from app.core.config import DatabaseSettings
from app.database.connection import create_db_engine, create_session_factory
from app.services.management_service import ManagementService
from app.services.schedule_service import ScheduleService
from app.workers.scheduler import SchedulerRunner, poll_interval


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Dispatch one batch, then exit")
    parser.add_argument("--poll-seconds", type=float, default=1)
    args = parser.parse_args(argv)
    engine = None
    runner = None
    old_handlers = {}
    requested_stop = Event()

    def stop_requested(_signum, _frame):
        requested_stop.set()

    try:
        interval = poll_interval(args.poll_seconds)
        settings = DatabaseSettings.from_env()
        engine = create_db_engine(settings)
        factory = create_session_factory(engine)
        if not ManagementService(factory, system=True).healthy():
            print(
                "Veritabanı şeması hazır değil; migration durumunu kontrol edin.", file=sys.stderr
            )
            return 2
        schedules = ScheduleService(factory, system=True)
        if args.once:
            dispatched = schedules.dispatch_due(limit=20)
            print(f"{len(dispatched)} zamanlanmış çalıştırma kuyruğa alındı.")
            return 0
        runner = SchedulerRunner(schedules, poll_seconds=interval)
        for signum in (signal.SIGTERM, signal.SIGINT):
            old_handlers[signum] = signal.signal(signum, stop_requested)
        runner.start()
        print("Zamanlayıcı çalışıyor. Akışları yürütmek için worker açık olmalıdır.")
        while not requested_stop.wait(0.5):
            pass
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        print(
            "Zamanlayıcı çalıştırılamadı. Environment ve migration yapılandırmasını "
            "kontrol edin; hassas hata ayrıntıları bastırıldı.",
            file=sys.stderr,
        )
        return 1
    finally:
        if runner is not None:
            runner.stop()
        if engine is not None:
            engine.dispose()
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
