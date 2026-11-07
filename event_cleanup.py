
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from database import SessionLocal
from models import Event


logger = logging.getLogger(__name__)

# Interval pemeriksaan event dalam detik.
CLEANUP_INTERVAL = 60


async def cleanup_expired_events():
    """
    Melakukan soft delete pada event yang sudah selesai.

    Ketentuan:
    - Hanya event yang sudah disetujui.
    - Event harus memiliki end_date.
    - end_date harus sudah melewati waktu sekarang.
    - Event yang dibatalkan tidak diproses.
    - Event tidak dihapus secara permanen.
    - TicketClass tetap tersimpan.
    - Order dan Payment tetap tersimpan.
    - deleted_by bernilai None karena proses otomatis.
    """

    while True:
        db = SessionLocal()

        try:
            now = datetime.now(timezone.utc)

            expired_events = db.scalars(
                select(Event)
                .where(
                    Event.end_date.is_not(None),
                    Event.end_date < now,
                    Event.approval_status == "approved",
                    Event.is_cancelled.is_(False),
                    Event.is_deleted.is_(False),
                )
                .with_for_update(
                    skip_locked=True
                )
            ).all()

            deleted_count = 0

            for event in expired_events:
                event.is_deleted = True
                event.deleted_at = now
                event.deleted_by = None

                deleted_count += 1

            if deleted_count > 0:
                db.commit()

                logger.info(
                    "Berhasil melakukan soft delete pada %s event yang sudah selesai.",
                    deleted_count,
                )
            else:
                db.rollback()

        except SQLAlchemyError:
            db.rollback()

            logger.exception(
                "Database error saat menjalankan event cleanup."
            )

        except Exception:
            db.rollback()

            logger.exception(
                "Kesalahan saat menjalankan event cleanup."
            )

        finally:
            db.close()

        await asyncio.sleep(CLEANUP_INTERVAL)
